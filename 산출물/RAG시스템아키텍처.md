## RAG 시스템 아키텍처

Eden의 RAG 파이프라인은 `backend/app/services/hybrid_rag.py`가 오케스트레이터 역할을 하며, `recommend()`(추천)와 `answer()`(응답 생성) 2단계로 분리되어 있다. `streamlit_app.py`가 HTTP 계층 없이 이 서비스 함수들을 직접 import해 호출하는 단일 프로세스 구조다.

### 1단계 — 추천 (`recommend`)

```python
def recommend(user_mbti: str, message: str, emo_weight: float = 1.0) -> dict:
    emotion = infer_emotion(message, use_llm_fallback=False)
    bias = EMOTION_BIAS.get(emotion, [])
    try:
        graph_rows = graph_store.recommend_disciples(user_mbti, limit=12)
        if not graph_rows:
            raise RuntimeError("empty graph result")
    except Exception:
        from app.services import mock_data
        graph_rows = mock_data.recommend_disciples(user_mbti, limit=12)
    ...
```

`emotion.infer_emotion()`으로 사용자 발화의 감정을 먼저 분류하고, `EMOTION_BIAS` 딕셔너리로 감정별 어울리는 제자에 가산점을 준다. `graph_store.recommend_disciples()`가 Neo4j의 MBTI 궁합 점수(0~100)를 조회하며, 실패 시 `mock_data.recommend_disciples()`로 자동 폴백한다.

### 대화 전환 판단 (`should_recommend`)

예수님과의 자유 대화가 제자를 추천할 만큼 충분히 깊어졌는지 판단하는 함수다.

```python
MIN_JESUS_TURNS = 2   # 이보다 적으면 아직 추천하지 않음
MAX_JESUS_TURNS = 6   # 이보다 많으면 LLM 판단 없이 강제 추천
```

사용자 턴 수가 최소치 미만이면 추천하지 않고, 최대치 이상이면 무조건 추천한다. 그 사이 구간에서만 LLM에게 "지금 추천해도 되는지"를 '예/아니오'로 판단하게 하며, LLM 호출 실패 시 턴 수 휴리스틱(3턴 이상)으로 폴백한다.

### 2단계 — 응답 생성 (`answer`)

```python
def retrieve_verses(message: str, books: list[str] | None) -> list[dict]:
    query = _hyde_expand(message) if settings.USE_HYDE else message
    fetch_k = settings.RETRIEVAL_FETCH_K if settings.USE_RERANK else settings.RETRIEVAL_K
    docs = vector_store.search(query, k=fetch_k, books=books)
    if settings.USE_RERANK:
        docs = _rerank(message, docs, settings.RETRIEVAL_K)
    return docs
```

- **(선택) HyDE**: `config.USE_HYDE`(기본 `False`)가 켜지면 질문을 가상의 성경적 답변으로 확장해 검색어로 사용한다.
- **Vector 검색**: `vector_store.search()`가 추천된 제자의 연관 성경서로 필터링된(graph-guided) 유사도 검색을 수행한다.
- **(선택) Rerank**: `config.USE_RERANK`(기본 `False`)가 켜지면 `sentence_transformers.CrossEncoder`로 1차 결과를 재정렬한다.

이후 `_persona_prompt()`가 인물 프로필·검색된 구절·대화 이력·`shared_memory`(예수님과 나눈 대화 요약)를 `prompts.build_prompt()`에 전달해 최종 프롬프트를 조립하고, `llm.get_llm().invoke()`로 답변을 생성한다.

### 핵심 구절 1개 추출

```python
_KEY_RE = re.compile(r"\[\s*핵심\s*구절\s*[:：]\s*([^\]]*)\]")

def _split_key_verse(text: str, verses: list[dict]) -> tuple[str, list[dict]]:
    ...
```

LLM 응답 끝의 `[핵심구절: 마태복음 11:28]` 표시를 읽어, 검색된 여러 구절 중 이번 답변에서 실제로 근거로 삼은 구절 1개만 골라 화면에 표시한다. LLM이 표시를 빠뜨리면 검색 1순위 구절로 폴백하고, `[핵심구절: 없음]`이면 구절을 붙이지 않는다.

### 폴백 설계

`answer()`는 각 단계를 독립적으로 try/except로 감싸 다음과 같이 폴백한다.

| 단계 | 실패 시 폴백 |
|---|---|
| 인물 프로필 조회(`graph_store.get_person`) | `mock_data.PEOPLE` |
| 구절 검색(`retrieve_verses`) | `settings.ALLOW_MOCK_VERSES`(기본 `False`)가 켜져 있으면 `mock_data.mock_verses()`, 꺼져 있으면 구절 없이 진행 |
| LLM 응답 생성 | `mock_data.mock_persona_answer()` |

이 구조 덕분에 Neo4j·Vector DB·OpenAI API 중 무엇이 끊겨도 서비스가 끝까지 응답을 반환한다.
