"""
backend/scripts/eval_ragas.py
────────────────────────────────────────────────────────────────────────
Eden 하이브리드 RAG 파이프라인(hybrid_rag.py)을 RAGAS로 평가하는 CLI 스크립트.

설계 근거: docs(내부 산출물) "RAGAS 평가" 설계 문서(평가 목적/데이터셋 구성/
시나리오/6개 지표: Faithfulness, Answer Relevancy, Context Precision,
Context Recall, Answer Correctness, Answer Similarity)를 그대로 구현한다.
질문 생성 방식은 backend/scripts/eval_embeddings.py의 방식(구절 샘플링 →
GPT-4o-mini로 자연어 질문 생성, 족보/율법 목록 위주 책 제외)을 재사용한다.

★★★ 실행 전 반드시 확인 ★★★
현재(PyPI 최신) `ragas`(0.4.x)는 `langchain-community`가 vertexai 관련
서브모듈을 제거한 버전(0.4.x)과 함께 있으면 아래 오류로 아예 임포트가
되지 않는다(직접 재현·확인함):

    ModuleNotFoundError: No module named 'langchain_community.chat_models.vertexai'

또한 ragas 0.4.x는 metrics API가 `ragas.metrics.faithfulness` 같은 클래식
싱글턴 방식에서 `instructor` 기반의 `ragas.metrics.collections.*` 방식으로
전면 개편되어, 이 스크립트가 쓰는 클래식 API(`from ragas.metrics import
faithfulness, ...`)가 그대로 동작하지 않는다.

따라서 이 스크립트를 쓰려면 별도 평가 전용 의존성 파일
(backend/requirements-eval.txt, 이 스크립트와 함께 제공)로 다음처럼
버전을 고정해서 설치해야 한다(직접 설치·임포트·평가 데이터셋 구성까지
검증 완료):

    pip install "ragas==0.2.15" "datasets" "langchain-community<0.4.0"

이 조합은 Eden의 루트 requirements.txt가 오늘 기준으로 설치하는
langchain 1.3.x / langchain-core 1.4.x / langchain-openai 1.3.x /
langchain-chroma 1.1.x 스택과 충돌 없이 공존한다(직접 재현·확인함).
langchain-community만 0.4.2 → 0.3.31로 낮아지며, Eden 자체 코드는
langchain_community의 vertexai 서브모듈을 쓰지 않으므로 이 다운그레이드로
인한 기능 손실은 없다(직접 재현·확인함).

전제조건:
  1. <프로젝트 루트>/data/bible_structured.json 존재
  2. backend/.env 또는 루트 .env에 OPENAI_API_KEY 설정
  3. 위 pip 조합이 설치되어 있을 것

실행:
  python backend/scripts/eval_ragas.py
  python backend/scripts/eval_ragas.py --n-cases 30 --seed 42
"""

import argparse
import json
import random
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(BACKEND / ".env")
load_dotenv(ROOT / ".env")

import json as _json  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services import hybrid_rag, mock_data  # noqa: E402
from app.services.llm import get_llm  # noqa: E402
from app.services.embeddings import get_embeddings  # noqa: E402

# ── 질문 생성 대상에서 제외할 책(족보·율법 목록 위주) ────────────────────
# eval_embeddings.py와 동일한 기준을 재사용해 검증 방식의 일관성을 유지한다.
EXCLUDE_QUERY_BOOKS = {"레", "민", "대상", "대하", "스", "느"}

QGEN_PROMPT = (
    "다음은 성경 구절입니다.\n\n구절: {verse}\n\n"
    "이 구절의 핵심 상황·인물·감정·교훈 중 최소 하나는 구체적으로 담아서, "
    "사용자가 챗봇에게 자연스럽게 물어볼 법한 한국어 질문을 한 문장으로 만들어줘. "
    "구절 문장을 그대로 인용하지는 말되, 검색으로 이 구절을 다시 찾아낼 수 있을 만큼 "
    "구체적인 상황/인물/키워드는 반드시 살려줘. 질문 문장만 출력하고 다른 설명은 붙이지 마."
)

# 시나리오 커버리지를 위해 person_id(예수님+12제자)와 MBTI를 순환 샘플링한다.
PERSON_IDS = list(mock_data.PEOPLE.keys())            # jesus + 12 disciples
MBTI_TYPES = list(mock_data.TYPE_ORDER)                # 16종


def load_bible_records() -> list[dict]:
    with open(settings.BIBLE_FILE, "r", encoding="utf-8") as f:
        return _json.load(f)


def sample_test_cases(records: list[dict], n: int, seed: int) -> list[dict]:
    """구절 샘플링 + person_id/MBTI 순환 배정. (질문/응답은 별도 단계에서 채운다)"""
    random.seed(seed)
    eligible = [r for r in records if r.get("book") not in EXCLUDE_QUERY_BOOKS]
    sampled = random.sample(eligible, min(n, len(eligible)))
    cases = []
    for i, rec in enumerate(sampled):
        cases.append({
            "verse_ref": f"{rec['book']}_{rec['chapter']}_{rec['verse']}",
            "verse_content": rec["content"],
            "verse_book": rec["book"],
            "person_id": PERSON_IDS[i % len(PERSON_IDS)],
            "user_mbti": MBTI_TYPES[i % len(MBTI_TYPES)],
        })
    return cases


def generate_question(qgen_llm: ChatOpenAI, verse_content: str) -> str:
    resp = qgen_llm.invoke(QGEN_PROMPT.format(verse=verse_content))
    return resp.content.strip().strip('"')


def build_ground_truth(verse_content: str) -> str:
    """
    ★ 임시 ground_truth 생성 방식 (설계 문서에서 명시한 '신규 구축 필요' 항목의
    최소 동작 버전) ★
    실제 사람이 검수한 참조 답변이 아직 없으므로, 정답 구절 원문 자체를
    참조 답변으로 사용하는 최소 구현이다. Answer Correctness/Answer Similarity
    점수는 이 근사치를 기준으로 계산되므로, 사람이 직접 작성한 참조 답변으로
    교체하면 두 지표의 신뢰도가 높아진다. (설계 문서 "평가 데이터셋 구성" 항목 참고)
    """
    return f"이 구절 말씀에 따르면: {verse_content}"


def collect_case(case: dict, qgen_llm: ChatOpenAI) -> dict:
    """실제 hybrid_rag 파이프라인을 호출해 question/contexts/answer/ground_truth를 채운다."""
    question = generate_question(qgen_llm, case["verse_content"])

    person = mock_data.PEOPLE.get(case["person_id"], {})
    books = person.get("books") or None

    # Retrieval: hybrid_rag.retrieve_verses()가 실제로 answer()에 넘기는 것과
    # 동일한 문맥(HyDE/Rerank 토글 포함)을 그대로 재사용한다.
    try:
        retrieved = hybrid_rag.retrieve_verses(question, books)
    except Exception:
        retrieved = []
    contexts = [v["content"] for v in retrieved] or [case["verse_content"]]

    # Generation: 실제 answer() 호출(제자/예수님 페르소나 + 검색 문맥 + LLM)
    result = hybrid_rag.answer(
        person_id=case["person_id"],
        user_mbti=case["user_mbti"],
        message=question,
        history="",
    )

    return {
        "question": question,
        "contexts": contexts,
        "answer": result["answer"],
        "ground_truth": build_ground_truth(case["verse_content"]),
        # 아래는 결과 CSV/JSON에서 시나리오별 세분화 분석에 쓰는 부가 컬럼
        "person_id": case["person_id"],
        "user_mbti": case["user_mbti"],
        "verse_ref": case["verse_ref"],
        "verse_source": result.get("verse_source", ""),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-cases", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "ragas_eval_results.json")
    args = parser.parse_args()

    try:
        # Ragas 0.2.x 에는 nest_asyncio.apply() 가 전역으로 호출되어 
        # Python 3.11+ 환경에서 "RuntimeError: Timeout should be used inside a task"
        # 버그를 유발하는 문제가 있으므로 모의 객체로 우회합니다.
        import sys, types
        if "nest_asyncio" not in sys.modules:
            dummy_nest = types.ModuleType("nest_asyncio")
            dummy_nest.apply = lambda *args, **kwargs: None
            sys.modules["nest_asyncio"] = dummy_nest

        from ragas import evaluate
        from ragas.metrics import (
            faithfulness,
            answer_relevancy,
            context_precision,
            context_recall,
            answer_correctness,
            answer_similarity,
        )
        from datasets import Dataset
    except ImportError as e:
        print("[에러] ragas/datasets 임포트 실패:", e)
        print("       backend/requirements-eval.txt 기준으로 재설치하세요:")
        print('       pip install "ragas==0.2.15" "datasets" "langchain-community<0.4.0"')
        sys.exit(1)

    print("[1/5] 성경 원본 로드 및 테스트 케이스 샘플링...", flush=True)
    records = load_bible_records()
    cases = sample_test_cases(records, args.n_cases, args.seed)
    print(f"    샘플된 케이스: {len(cases)}개 "
          f"(person_id/MBTI 순환 배정, 족보·율법 목록 제외)", flush=True)

    from app.services import vector_store
    print("[1.5/5] 벡터 DB 인덱스 점검 및 준비...", flush=True)
    vector_store.ensure_vector_store()

    print("[2/5] 질문 생성 + hybrid_rag 파이프라인 실제 호출...", flush=True)
    qgen_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
    rows = []
    for i, case in enumerate(cases, 1):
        row = collect_case(case, qgen_llm)
        rows.append(row)
        print(f"    ({i}/{len(cases)}) [{case['person_id']}/{case['user_mbti']}] "
              f"{row['question'][:40]}...", flush=True)

    print("[3/5] RAGAS 데이터셋 변환...", flush=True)
    dataset = Dataset.from_list([
        {
            "question": r["question"],
            "contexts": r["contexts"],
            "answer": r["answer"],
            "ground_truth": r["ground_truth"],
        }
        for r in rows
    ])

    print("[4/5] RAGAS 평가 실행 (judge LLM: config.LLM_MODEL 재사용)...", flush=True)
    # Eden 자체 llm/embeddings 팩토리를 그대로 재사용한다 — judge 모델을
    # config.py 한 곳에서만 관리하기 위함(설계 문서 "평가 환경" 항목 참고).
    judge_llm = get_llm(temperature=0)
    judge_embeddings = get_embeddings()

    result = evaluate(
        dataset=dataset,
        metrics=[
            faithfulness,
            answer_relevancy,
            context_precision,
            context_recall,
            answer_correctness,
            answer_similarity,
        ],
        llm=judge_llm,
        embeddings=judge_embeddings,
        raise_exceptions=False,
    )

    print("\n" + "=" * 60)
    print(result)
    print("=" * 60)

    print("[5/5] 결과 저장...", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        scores_dict = dict(result)
    except Exception:
        scores_dict = getattr(result, "_repr_dict", {})

    out_payload = {
        "n_cases": len(cases),
        "seed": args.seed,
        "judge_llm": settings.LLM_MODEL,
        "scores": scores_dict,
        "cases": rows,
    }
    args.out.write_text(
        json.dumps(out_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"저장됨: {args.out}")


if __name__ == "__main__":
    main()
