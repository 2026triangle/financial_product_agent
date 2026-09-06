# 금융 상품 Q&A AI 에이전트

### 2026 미래에셋 AI 페스티벌 — 팀 Triangle

자연어로 입력된 금융 상품 질의(국내채권·국내ETF·해외ETF·공모펀드)를 온톨로지 기반으로 검증하고,
SQL로 실제 데이터를 조회한 뒤 조회된 원본 데이터에 근거해 답변을 생성하는 에이전트입니다.
LLM 백엔드는 대회 규정에 따라 CLOVA Studio(HyperCLOVA X, HCX-005) 단일 모델만 사용합니다.
설계 배경과 의사결정 근거는 기술 제안서를 참고하세요.

## 레포 구조

```
.
├── README.md
├── requirements.txt
├── .env.example                 # CLOVA_API_KEY / CLOVA_BASE_URL / CLOVA_MODEL
├── ontology/                    # 도메인별 Ontology(TTL) — 대회 제출 규격
│   ├── common.ttl                 # 공통 상위 클래스 + 참조 엔티티 + 통제 어휘
│   ├── bond_kr.ttl                 # 국내채권 (tb_domestic_bond)
│   ├── etf_kr.ttl                  # 국내ETF (tb_domestic_etf)
│   ├── etf_gl.ttl                  # 해외ETF (tb_foreign_etf)
│   └── fund_pub.ttl                # 공모펀드 (tb_fund)
├── src/
│   ├── agent/                     # 에이전트 파이프라인 구현체 ("에이전트 구현" 절 참고)
│   ├── ontology/endpoint/         # 온톨로지 런타임 검증기 (OntologyValidator)
│   ├── data/                      # 원본/전처리 데이터, DB 적재, SQLite DB ("데이터 파이프라인" 절 참고)
│   └── endpoint/                  # MCP 서버 관련 모듈
```

## 설치 및 실행

```bash
pip install -r requirements.txt
cp .env.example .env   # CLOVA_API_KEY / CLOVA_BASE_URL / CLOVA_MODEL 채우기

cd src/agent

# CLI로 단일 질의 실행
python3 main.py

# 평가용 API 서버 기동
uvicorn api_server:app --host 0.0.0.0 --port <PORT>
```

### API

```
GET /answer?question_id=<ID>&question=<자연어 질의>
```

응답 스키마는 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer`로 고정되며,
내부에서 예외가 발생해도 500이 아닌 200과 동일 스키마로 응답합니다.

```bash
curl "http://localhost:<PORT>/answer?question_id=Q-001&question=신용등급 AAA인 국내채권 찾아줘"
```

### 평가 재현

```bash
cd src/agent
python3 run_eval_queries.py       # 대회 공식 평가 질의 8종 배치 재현 (난이도별 5, 답변불가 3)
python3 -m tools.sql_guard_tool   # SQL 가드레일 자체 점검 (DML/DDL·주석인젝션·화이트리스트 밖 테이블 차단)
```

## 아키텍처

```
[원천 데이터: 정형 SQL 테이블]                [온톨로지: RDF 트리플]
        │                                       │
        │                              [메타데이터 색인: BGE-M3 임베딩]
        │                                       │
        └───────────────┬───────────────────────┘
                         ▼
   ① QueryParser — 카테고리/상품명/등급비교/미지원조건 분리 추출 + 온톨로지 검증 + 하이브리드 검색
                         ▼
   ② TextToSQLAgent — 테이블별 SQL 생성(병렬, 카테고리 스코프 격리) + 무단조건 코드 검증
                         ▼
   ③ SQL 가드레일 — SELECT-only + 테이블 화이트리스트 + 주석인젝션 차단 → 읽기전용 실행
                         ▼
   ④ 결과 정제 — 식별 컬럼 + SQL 참조 컬럼만 추려 축소, 미검증 조건 요약 첨부
                         ▼
   ⑤ AnswerAgent — 도입 문장만 LLM 생성 + 원본 데이터 그대로 병합 삽입
                         ▼
   ⑥ GET /answer 응답
```

`main.py`의 `Agent._run_pipeline(query)`가 위 흐름 전체를 한 번에 실행하고,
CLI(`Agent.query()`)와 API(`api_server.py`의 `answer()`)가 모두 이 메서드 하나만 호출해
오케스트레이션 로직을 중복 구현하지 않습니다.

## 에이전트 구현 (`src/agent/`)

```
src/agent/
├── main.py                    # 파이프라인 오케스트레이터 (Agent 클래스, CLI 진입점)
├── api_server.py               # GET /answer 평가용 API 서버 (FastAPI)
├── config.py                   # 환경설정 (.env 로드, 모델/경로 관리)
├── run_eval_queries.py         # 대회 공식 평가 질의 8종 배치 재현 스크립트
├── agents/
│   ├── agent.py                 # 모든 에이전트의 공통 베이스 클래스 (LLM 호출 + 히스토리)
│   ├── query_parser.py          # ① 질의 파싱 + 온톨로지 검증 + 하이브리드 메타데이터 검색
│   ├── text_to_sql_agent.py     # ② 카테고리별 SQL 생성 + 무단조건 코드 검증 + 상품명 재검색
│   └── answer_agent.py          # ⑤ 답변 합성 (도입 문장만 LLM 생성, 데이터는 원본 그대로 삽입)
├── core/
│   ├── model_manager.py         # OpenAI 호환 LLM 클라이언트 (CLOVA Studio 엔드포인트로 설정)
│   └── tool_manager.py          # 도구 등록/자동 스키마 추출/Tool Calling 실행 엔진
└── tools/
    ├── ontology_tool.py          # OntologyValidator 싱글톤 연동
    ├── concept_column_map.py     # 온톨로지 개념 ↔ DB 컬럼 매핑
    ├── meta_search_tool.py       # BGE-M3 하이브리드(dense+sparse) 메타데이터 검색
    └── sql_guard_tool.py         # SELECT-only/화이트리스트/주석인젝션 차단 SQL 가드레일
```

`main.py`의 `Agent`(오케스트레이터)와 `agents/agent.py`의 `Agent`(베이스 클래스, `QueryParser`/
`TextToSQLAgent`/`AnswerAgent`가 상속)는 이름은 같지만 다른 클래스입니다.

**QueryParser**는 LLM으로 카테고리·조건을 추출하고 `OntologyValidator`로 검증합니다. 존재하지
않는 값(예: 신용등급 "AAAA")은 즉시 역질문으로 컷하고, 온톨로지가 검증할 수 없는 조건(구성종목·
기업관계 등)은 `unsupported_conditions`로 분리해 조용히 무시하지 않습니다. "AA- 이상" 같은 등급
비교를 LLM이 잘못된 트리플 형태로 출력하는 사례가 실측으로 확인되어, `_rescue_misplaced_rank_comparisons()`가
코드 레벨로 보정합니다.

**TextToSQLAgent**는 카테고리(테이블)별 독립 LLM 호출로 SQL을 생성합니다. LLM이 프롬프트 지침을
어기고 무단으로 상품명 조건을 추가하는 사례가 확인되어, 생성된 SQL을 실행 전 `_violates_name_filter_policy()`로
코드 검증하고 위반 시 재시도, 끝내 실패하면 안전한 오류 응답으로 처리합니다. 정식명 매칭이 0건이면
토큰 분해 후 재검색합니다.

**AnswerAgent**는 도입 문장만 LLM으로 생성하고, 실제 상품 데이터는 `_build_data_block()`이 원본
그대로 삽입합니다(LLM 재생성 금지). 생성된 도입 문장에서 마크다운 표(가짜 상품 데이터 생성 방지)는
`_strip_markdown_tables()`로 코드 레벨 제거합니다.

## 데이터 파이프라인 (`src/data/`)

```
raw/ (원천 정형 데이터, 2026-08-24 재배포본)
   │
   ▼
preprocess.py           # 컬럼 단위 정제: 공백/전량결측 컬럼 정리, 날짜 표준화,
   │                     # 결측 플레이스홀더·더미코드 통일, 이진 플래그·코드 포맷 표준화
   │                     # → 결과 리포트: preprocessed/preprocessing_report.md
   ▼
db/init_db.py            # DB 적재: pd_no 기준 중복 제거(시장구분 우선순위 정렬 후 대표 레코드만 유지),
   │                     # 상품군별 스키마에 맞춰 SQLite 테이블 구성
   ▼
db/finance_master.db     # 최종 SQLite DB (tb_domestic_bond / tb_domestic_etf / tb_foreign_etf / tb_fund)
```

컬럼별 정제 상세 내역(어떤 컬럼에서 몇 건이 어떻게 정제됐는지)은 자동 생성된
`src/data/preprocessed/preprocessing_report.md`에 전부 기록되어 있습니다. 대회 측이 공지한
"0/빈 값은 오류가 아닌 의도된 값" 원칙에 따라 결측치를 임의로 채우지 않고 카테고리별 실측값을
그대로 반영했으며, "판매 가능" 여부처럼 카테고리마다 컬럼·값 체계가 다른 항목은 실측 기반으로
개별 처리했습니다.

## 온톨로지 (`ontology/`)

대회 제출 규격에 맞춘 도메인별 Turtle 파일입니다. 이 온톨로지를 실제로 조회하는 런타임 검증기는
`src/ontology/endpoint/`에 있습니다.

| 파일 | 도메인 | 클래스 | 소스 테이블 |
|---|---|---|---|
| `common.ttl` | 공통 상위 클래스·참조 엔티티·통제 어휘 | — | — |
| `bond_kr.ttl` | 국내채권 | `fp:Bond` | `tb_domestic_bond` |
| `etf_kr.ttl` | 국내ETF | `fp:DomesticETF` | `tb_domestic_etf` |
| `etf_gl.ttl` | 해외ETF | `fp:ForeignETF` | `tb_foreign_etf` |
| `fund_pub.ttl` | 공모펀드 | `fp:Fund` | `tb_fund` |

네임스페이스는 `@prefix fp: <http://mafest.ai/product#>`(대회 예시 규격 그대로)이며, 각 도메인
파일은 `owl:imports <./common.ttl>`로 공통 정의를 참조합니다. 전체 2,260 트리플, 클래스 15개,
ObjectProperty 10개, DatatypeProperty 170개, AnnotationProperty 11개로 구성됩니다.

**`fp:BondCategory`(채권분류)는 트리가 아니라 DAG입니다.** 국내채권 3계층 분류(대분류 6종 →
중분류 16종 → 소분류 39종, 총 61개 인스턴스)를 실제 데이터에서 추출해보니 다부모 노드가 13개
있었습니다(예: 일반사채는 회사채·특수채 양쪽에 속함). 하위 카테고리 전개 결과를 SQL `IN` 절
필터로 쓰면 소분류가 비어있는 924건이 누락되고 다부모 노드가 과다매칭되므로, `resolve_category()`가
반환하는 해당 레벨의 컬럼·값으로 직접 조건을 거는 방식을 사용합니다. `expand_category()`는
역질문·설명 문구 생성 전용입니다.

`OntologyValidator`는 생성 시 TTL 5개를 rdflib로 1회 로드해 인메모리 인덱스로 전개하며, 이후
모든 검증·정규화·라우팅은 LLM 호출 없이 결정론적으로 수행됩니다.

```python
import sys; sys.path.insert(0, "src")
from ontology import OntologyValidator

v = OntologyValidator(ontology_dir="ontology")
v.validate_triples(['fp:Bond fp:hasCreditRating "AAAA"'])
# -> {"valid": False, "violations": [{"reason": "...", "suggestion": "AAA, AA+, ... 중 어느 것을..."}]}

v.resolve_source_table("domestic_bond")            # -> "tb_domestic_bond"
v.expand_rank_filter("CreditRating", "AA-", ">=")  # -> ["AAA","AA+","AA0","AA-"]
v.normalize_value("AssetClass", "주식", "tb_foreign_etf")  # -> "Equity"
v.resolve_category("국고채")
# -> {"level":"mid","column":"std_pd_scls_nm","value":"국고채","parents":["국공채"],"children":["국고채권"],"ambiguous":[]}
```

에이전트 쪽 연동은 `src/agent/tools/ontology_tool.py`(싱글톤 `get_ontology_validator()`)가 담당하고,
`src/agent/agents/query_parser.py`가 이를 직접 호출해 LLM 응답의 논리 트리플을 검증합니다.

### 검증

```bash
pip install rdflib
python3 -c "
import rdflib
g = rdflib.Graph()
for f in ['common.ttl','bond_kr.ttl','etf_kr.ttl','etf_gl.ttl','fund_pub.ttl']:
    g.parse(f'ontology/{f}', format='turtle')
print(len(g), 'triples loaded OK')
"
```