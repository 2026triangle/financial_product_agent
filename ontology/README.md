# ontology/ — 온톨로지 (TBox) + 런타임 검증기

대회 제출 규격(`AI페스티벌_과제설명_금융상품Agent_참여자공유용.pdf` "① 소스코드" 섹션)에 맞춘
도메인별 Turtle 파일입니다. 이 디렉터리는 **TTL과 README만** 둡니다(대회 규격 트리) — 이 온톨로지를
실제로 조회하는 런타임 검증기는 `src/ontology/endpoint/`에, TTL/통제어휘 생성 스크립트는
`scripts/ontology/`에 있습니다. 작업 명세는 `ot_instructions/ot_spec.md`(Task 1~3),
`ot_instructions/ot_task4.md`(채권분류 통제어휘), `ot_instructions/ot_task6.md`(이 레포 구조 이관) 참고.

## 파일 구성

| 파일 | 도메인 | 소스 테이블 | 비고 |
|---|---|---|---|
| `common.ttl` | 공통 상위 클래스(`fp:Product`, `fp:ETF`) + 참조 엔티티(`Institution`/`BenchmarkIndex`/`RiskGrade`/`CreditRating`/`AssetClass`/`Region`/`BondCategory`) + 공통 관계·통제 어휘 | - | 손으로 관리 |
| `bond_kr.ttl` | 국내채권 (`fp:Bond`) | `tb_domestic_bond` | **자동 생성** |
| `etf_kr.ttl` | 국내ETF (`fp:DomesticETF`) — 국내/해외ETF 공유 속성(예: 총보수율 `cu_charge_rt`)을 여기서 정의 | `tb_domestic_etf` | **자동 생성** |
| `etf_gl.ttl` | 해외ETF (`fp:ForeignETF`) | `tb_foreign_etf` | **자동 생성** |
| `fund_pub.ttl` | 공모펀드 (`fp:Fund`) + 펀드속성태그 (`fp:AttributeTag`) | `tb_fund`, `tb_fund_attribute` | **자동 생성** |

생성기·검증기·통제어휘 원본은 이 레포의 다른 디렉터리에 있습니다.

| 대상 | 위치 | 비고 |
|---|---|---|
| `build_ttl_from_meta.py` | `scripts/ontology/build_ttl_from_meta.py` | `meta_columns/*.json`(203개 컬럼) → 도메인 TTL 4개 생성기. 재실행 가능(idempotent) |
| `extract_bond_category.py` | `scripts/ontology/extract_bond_category.py` | `tb_domestic_bond` 채권분류 3계층(DAG) 추출 + `common.ttl`용 인스턴스 TTL 블록 생성(`--ttl`) |
| `extract_vocab.py` | `scripts/ontology/extract_vocab.py` | v4 전처리 엑셀에서 통제 어휘 후보를 카디널리티 기준으로 자동 발굴 |
| `meta_columns/*.json` (5개) | `scripts/ontology/meta_columns/` | 203컬럼 메타(한글명·설명·예시값·예상질문) — `build_ttl_from_meta.py` 입력 |
| `vocab/*.json` (5개) | `scripts/ontology/vocab/` | v4 실측 통제 어휘 후보 + `bond_category_hierarchy.json`(채권분류 61노드 원본) |
| `ontology_validator.py` | `src/ontology/endpoint/ontology_validator.py` | TTL을 rdflib로 로드해 트리플 모순 검사·허용값 조회·부등호 질의 변환·채권분류 DAG 조회를 제공하는 런타임 검증기. LLM 호출 없음 |

**옮기지 않은 것**: `legacy_ttl/`(이전 초안 — 네임스페이스가 `fp:` 규격과 달라 제출물로 오해될 수 있어 원본 레포에만 보관), 원본 데이터 파일(`raw/`, `perprocessed_datas/`, `finance_master.db` — 네이버클라우드 DB로 대체).

네임스페이스: `@prefix fp: <http://mafest.ai/product#>` (대회 예시 규격 그대로).
각 도메인 파일은 `owl:imports <./common.ttl>`로 공통 정의를 참조합니다(상대경로라 레포 위치가 바뀌어도 그대로 동작).

## `bond_kr.ttl` / `etf_kr.ttl` / `etf_gl.ttl` / `fund_pub.ttl` 은 자동 생성 파일입니다

**손으로 직접 편집하지 마세요.** `scripts/ontology/build_ttl_from_meta.py`가
`scripts/ontology/meta_columns/*.json`의 203개 컬럼을 기계적으로 `owl:DatatypeProperty`로
변환해 매번 이 4개 파일을 통째로 덮어씁니다.

```bash
# 레포 루트에서
python3 scripts/ontology/build_ttl_from_meta.py
```

- `column_name`(snake_case) → `fp:속성명`(camelCase), `column_korean_name` → `rdfs:label`,
  `data_type` → `rdfs:range`, `description` → `rdfs:comment`, `synonyms` → `fp:hasSynonym`
  (핵심 비즈니스 컬럼 A그룹만 — 식별자/규격 B그룹은 라벨만 두고 synonym 생략)
- 같은 컬럼명이 여러 테이블에 동시에 존재하면(예: `cu_charge_rt`는 국내/해외ETF 공통) 가장 먼저
  나오는 도메인 파일에서 한 번만 정의하고 domain을 공통 상위클래스(`fp:ETF`/`fp:Product`)로 넓힙니다.
  나머지 파일은 재사용 안내 코멘트만 남기고 재선언하지 않습니다 — 4개 파일을 합쳐도 중복 정의가 없습니다.
- `meta_columns/*.json`이 갱신되면 이 스크립트도 다시 돌려야 합니다.

## `fp:BondCategory` — 채권분류 통제 어휘 (DAG, 트리 아님)

`tb_domestic_bond`의 3계층 분류(대분류 `std_pd_mcls_nm` 6종 → 중분류 `std_pd_scls_nm` 16종 →
소분류 `bd_knd` 39종, 총 **61개 인스턴스**)를 `common.ttl`에 통제 어휘로 박아넣은 것입니다.
원본은 `scripts/ontology/vocab/bond_category_hierarchy.json`(`extract_bond_category.py` 산출물).

- **계층은 트리가 아니라 DAG입니다.** `fp:parentCategory` 엣지가 총 **70개**인데, 이 중
  **13개 노드**(중분류 3개, 소분류 10개)는 부모가 2개 이상입니다(예: `일반사채`의 상위는
  `회사채`·`특수채` 둘 다). `rdfs:subClassOf`는 쓰지 않습니다 — 클래스 서브섬션(도메인 검사)과
  인스턴스 계층을 섞으면 `OntologyValidator._ancestors()`가 오염되기 때문입니다.
- **레벨 간 동명이인이 2건 있습니다**: `특수은행채`(중분류이자 소분류), `개인투자용국채`(대분류이자
  소분류). 그래서 URI에 레벨 접두어(`BondCatM_`/`BondCatS_`/`BondCatK_`)가 필수입니다.
- **하위 전개 결과를 SQL 필터로 쓰지 마세요.** `bd_knd` NULL 924건이 하위 전개에서 누락되고,
  다부모 노드 때문에 과다매칭도 발생합니다. 올바른 필터는 `resolve_category()`가 돌려주는
  `column`에 매칭된 레벨 자신의 값으로 직접 `WHERE`를 거는 것입니다. `expand_category()`는
  역질문·설명 문구 생성 전용입니다(독스트링에도 동일 경고가 있습니다).

## `endpoint/` — 런타임 검증기 (`src/ontology/endpoint/`)

`OntologyValidator`는 생성 시 TTL 5개를 rdflib로 1회 로드해 클래스/속성/통제어휘를 딕셔너리로
전개하고, 이후 메서드 호출은 전부 순수 조회입니다(LLM 호출 없음).

```python
import sys; sys.path.insert(0, "src")   # src/ontology 패키지를 찾기 위함
from ontology import OntologyValidator

v = OntologyValidator(ontology_dir="ontology")   # 레포 루트를 cwd로 실행할 때
v.validate_triples(['fp:Bond fp:hasCreditRating "AAAA"'])
# -> {"valid": False, "violations": [{"reason": "...", "suggestion": "AAA, AA+, ... 중 어느 것을..."}]}

v.resolve_source_table("domestic_bond")           # -> "tb_domestic_bond"
v.expand_rank_filter("CreditRating", "AA-", ">=") # -> ["AAA","AA+","AA0","AA-"]
v.normalize_value("AssetClass", "주식", "tb_foreign_etf")  # -> "Equity"

v.list_allowed_values("BondCategory")             # -> 61개 라벨 전체
v.list_allowed_values("BondCategory", level="major")  # -> 대분류 6개만 (level: "major"|"mid"|"minor")

v.resolve_category("국고채")
# -> {"level":"mid","column":"std_pd_scls_nm","value":"국고채",
#     "parents":["국공채"],"children":["국고채권"],"ambiguous":[]}
# Text-to-SQL이 WHERE <column> = <value> 를 바로 만들 수 있게 하는 것이 목적.
# 동명이인('특수은행채' 등)은 상위 레벨(major>mid>minor)을 우선 반환하고 "ambiguous"에 나머지를 담는다.

v.expand_category("국공채")
# -> 하위 전 레벨 라벨 목록(예: "국고채","국고채권",...). 역질문·설명 문구 생성 전용.
# ※ 이 결과를 SQL IN 절 필터로 쓰지 말 것 — bd_knd NULL 924건 누락, 다부모 과다매칭.
```

에이전트 쪽 연동은 `src/agent/tools/ontology_tool.py`(싱글톤 `get_ontology_validator()`)가 담당하고,
`src/agent/agents/query_parser.py`가 이를 직접 호출해 LLM 응답의 `logical_verification` 트리플을
검증합니다. 위반이면 추가 LLM 호출 없이 즉시 `answerable: False` + 역질문으로 컷됩니다.

## 검증

```bash
pip install rdflib
# 레포 루트에서
python3 -c "
import rdflib
g = rdflib.Graph()
for f in ['common.ttl','bond_kr.ttl','etf_kr.ttl','etf_gl.ttl','fund_pub.ttl']:
    g.parse(f'ontology/{f}', format='turtle')
print(len(g), 'triples loaded OK')
"
```

`rdflib`는 레포 루트 `requirements.txt`에 `rdflib==7.6.0`으로 고정되어 있습니다.
2026-09-04 스모크 테스트 결과: TTL 5개(`common.ttl`/`bond_kr.ttl`/`etf_kr.ttl`/`etf_gl.ttl`/`fund_pub.ttl`)
파싱 2,055 트리플 로드 OK, `OntologyValidator`의 `validate_triples`/`resolve_source_table`/
`expand_rank_filter`/`normalize_value`/`resolve_category`/`expand_category` 전부 위 예시 그대로 정상 동작 확인.
