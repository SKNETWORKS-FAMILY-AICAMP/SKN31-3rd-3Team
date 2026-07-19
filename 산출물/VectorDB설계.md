## Vector DB 설계

Eden은 로컬 개발 환경과 배포 환경에서 서로 다른 Vector DB 백엔드를 사용한다. `backend/app/core/config.py`의 `VECTOR_BACKEND` 값 하나로 전환되며, 나머지 코드는 백엔드 종류를 신경 쓰지 않도록 추상화되어 있다.

| 환경 | 백엔드 | 컬렉션 | 비고 |
|---|---|---|---|
| 로컬 개발 | Chroma | `langchain`(`CHROMA_COLLECTION`) | `data/chroma_db`에 자동 영속화, 최초 실행 시 자동 재생성 |
| 운영 배포 | Qdrant Cloud | `bible_verses` | 사전에 OpenAI 임베딩으로 만들어 둔 31,077개 포인트 스냅샷을 복원해 사용, 재임베딩 없음 |

### 백엔드 로딩

```python
def _load_chroma():
    from langchain_chroma import Chroma
    return Chroma(
        collection_name=settings.CHROMA_COLLECTION,
        persist_directory=settings.CHROMA_DB_DIR,
        embedding_function=get_embeddings(),
    )

def _load_qdrant():
    from langchain_qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient
    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
    return QdrantVectorStore(
        client=client,
        collection_name=settings.QDRANT_COLLECTION,
        embedding=get_embeddings(),
    )
```

### Metadata 스키마

| 필드 | 타입 | 설명 |
|---|---|---|
| `page_content` | string | `"[책이름 장:절] 본문내용"` 형태로 조합된 임베딩 대상 텍스트 |
| `book` | string | 성경 책 약어(예: `창`, `벧전`) |
| `chapter` | int | 장 번호 |
| `verse` | int | 절 번호 |
| `content` | string | 구절 원문(출처 정보와 분리해 UI 서빙용으로 별도 보관) |

### 검색 및 Graph-guided Filter

질의 임베딩과의 코사인 유사도 기반 Top-K 검색(`RETRIEVAL_K=5`)을 기본으로 한다. Neo4j가 고른 제자의 연관 성경서로 `book` metadata 필터를 적용해 검색 범위를 좁힌다(graph-guided filter).

```python
def _build_filter(books: list[str]):
    norm = normalize_books(books) or books
    backend = settings.VECTOR_BACKEND.lower()
    if backend == "qdrant":
        from qdrant_client import models
        return models.Filter(
            must=[models.FieldCondition(key="metadata.book", match=models.MatchAny(any=norm))]
        )
    return {"book": {"$in": norm}} if len(norm) > 1 else {"book": norm[0]}
```

필터에 넘기는 책 이름이 전체명/약어 어느 쪽이든 `bible_books.normalize_books()`가 실제 인덱스 표기(약어)로 맞춰준다. 필터 적용 결과가 0건이면 필터 없이 재검색해 구절이 통째로 사라지는 것을 방지한다.
