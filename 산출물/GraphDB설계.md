## Graph DB 설계

Eden은 "누가 답할지"(제자 궁합)와 "어떤 성경서 범위에서 찾을지"를 Neo4j 그래프로 결정한다. `backend/app/services/graph_store.py`가 조회 로직을, `backend/scripts/migrate_neo4j_to_aura.py`가 로컬→Neo4j Aura(운영) 마이그레이션을 담당한다.

### Node 설계

| Node | 주요 Property |
|---|---|
| `Jesus` | `name, quote, quote_ref, traits, role, epithet, person_order, mbti` |
| `Disciple` | `id, name, title, speech_style, quote, quote_ref, traits, role, epithet, person_order` |
| `MBTI` | `type` |
| `Trait` | `name` |
| `Verse` | `ref, text` |
| `User` | `name, mbti, created_at` |

### Relationship 설계

| 관계 | 방향 | 의미 |
|---|---|---|
| `FOLLOWS` | Disciple → Jesus | 제자가 예수님을 따름 |
| `HAS_MBTI {rank}` | Disciple → MBTI | 제자 자신과 어울리는 MBTI 1·2순위 |
| `MATCHES` | MBTI → Disciple | 사용자 MBTI → 직접 매칭되는 제자(16종 1:1) |
| `MBTI_COMPATIBILITY {score}` | MBTI → MBTI | 16×16 전체 궁합 curated 매트릭스(256쌍) |
| `HAS_TRAIT` | Disciple → Trait | 제자의 성향 키워드 |
| `RELATED_VERSE` | Disciple → Verse | 제자와 연관된 성경 구절 |
| `BROTHER_OF` | Disciple → Disciple | 형제 관계(예: 베드로–안드레, 야고보–요한) |
| `MATCHED_WITH {matched_at}` | User → Disciple | 회원이 실제로 매칭된 이력 |

### Query 흐름

`recommend_disciples(user_mbti, limit)`는 3개 Cypher 쿼리를 순차 실행해 순위를 매긴다.

```python
_DIRECT_MATCH_CYPHER = """
MATCH (:MBTI {type: $user_mbti})-[:MATCHES]->(d:Disciple)
RETURN d.id AS id
"""

_MBTI_COMPAT_CYPHER = """
MATCH (:MBTI {type: $user_mbti})-[c:MBTI_COMPATIBILITY]->(m:MBTI)
RETURN m.type AS type, c.score AS score
"""
```

- `MATCHES` 관계로 직접 매칭되는 제자는 최고점(100점)을 부여한다.
- 나머지 제자는 자신의 `HAS_MBTI` 1·2순위 타입과 사용자 MBTI 간 `MBTI_COMPATIBILITY` curated 점수로 순위를 매긴다(1순위는 가중치 1.0, 2순위는 0.85).
- `get_person(person_id)`는 단일 제자의 프로필(quote, traits, 연관 구절 등)을 조회하며, `Jesus`는 `Disciple` 라벨이 아니므로 조회 결과가 없을 경우 상위 `hybrid_rag.py`가 `mock_data.py`로 폴백한다.

### 폴백 설계

Neo4j 연결 실패 시 `mock_data.PEOPLE`(13개 인물 프로필)과 `mock_data.SCORES`(동일 구조의 16×16 궁합 매트릭스 로컬 사본)로 자동 대체되어 서비스가 중단되지 않는다.

### 운영 전환

로컬 Neo4j(Desktop/Docker)의 데이터를 Neo4j Aura로 옮기기 위한 범용 export/import 스크립트(`migrate_neo4j_to_aura.py`)가 제공된다. 노드와 관계를 그대로 읽어와 대상 DB를 비운 뒤 재생성하는 방식이며, 재실행해도 안전하다.
