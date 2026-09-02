# 🛠️ [Tool Specification] `search_finance_metadata`

## 1. 도구 개요 (Tool Overview)
* **도구명**: `search_finance_metadata`
* **역할**: 사용자의 자연어 금융 질의를 분석하여 마스터 데이터베이스(국내채권, 국내ETF, 해외ETF, 공모펀드)에서 **가장 적합한 테이블 및 컬럼 메타데이터(한글명, 데이터타입, 상세설명, 유의어, 샘플값)**를 인메모리 BGE-M3 하이브리드(Dense+Sparse) 벡터 검색으로 탐색합니다.
* **호출 목적**:
  1. 사용자 질의 속 구어체/변형 키워드(예: *"안전한 채권"*, *"수수료 제일 싼 ETF"*, *"작년에 대박 난 펀드"*, *"IRP 계좌에서 살 수 있는"*)를 정확한 DB 컬럼명으로 매핑할 때
  2. SQL 쿼리(Text-to-SQL) 작성 전 테이블 스키마 및 실제 데이터 포맷을 검증할 때

---

## 2. 도구 호출 시점 (When to Call)
* 사용자가 금융 상품의 **수익률, 금리, 신용등급, 보수/수수료, 자산규모, 배당, 연금거래 적격 여부, 운용전략** 등에 대해 질문할 때 **반드시 SQL 생성 전 가장 먼저 호출**하십시오.
* 테이블이나 컬럼명을 임의로 추측(Hallucination)하여 SQL을 작성하지 말고, 이 도구를 통해 검색된 실제 컬럼명과 데이터 타입을 기반으로 쿼리를 작성해야 합니다.

---

## 3. 매개변수 명세 (Parameters)

| 매개변수명 | 타입 | 필수 여부 | 기본값 | 설명 및 가이드 |
| :--- | :---: | :---: | :---: | :--- |
| **`query`** | `string` | **필수** | - | 사용자 질의 원문 또는 핵심 금융 키워드 (예: `"만기 1년 미만 안전한 채권 금리"`, `"총보수 수수료 낮은 미국 배당 ETF"`, `"설정액 1000억 이상 수익률 좋은 펀드"`) |
| **`table_name`** | `string` | 선택 | `"all"` | 특정 상품군으로 검색을 제한할 때 지정. <br>• `tb_domestic_bond`: 국내채권 마스터<br>• `tb_domestic_etf`: 국내 상장 ETF<br>• `tb_foreign_etf`: 해외 상장 ETF<br>• `tb_fund`: 공모펀드 본체<br>• `tb_fund_attribute`: 공모펀드 1:N 속성 분류<br>• `all`: 전체 테이블 대상 통합 검색 |
| **`top_k`** | `integer` | 선택 | `5` | 반환받을 최상위 연관 컬럼 개수 (권장: 3~5개, 범위: 1~20) |
| **`alpha`** | `number` | 선택 | `0.7` | 시맨틱 의미(Dense) vs 키워드 일치(Sparse) 가중치 비율.<br>• `0.7`: 권장 표준 균형값<br>• `1.0`: 순수 의미 유사도<br>• `0.0`: 순수 키워드/토큰 일치 |

---

## 4. 도구 반환값 구조 (Output Structure)

```json
{
  "status": "success",
  "query": "만기 1년 미만 안전한 채권 금리",
  "total_matched": 3,
  "columns": [
    {
      "table_name": "tb_domestic_bond",
      "column_name": "crd_grd",
      "column_korean_name": "대표신용등급 (신용도/안전도)",
      "data_type": "VARCHAR(50)",
      "description": "국채/지방채 면제 종목을 제외하고 신용평가 3사의 평가를 종합한 표준 대표 신용등급(AAA, AA+, AA0, AA-, A+, BBB- 등)입니다. 채권의 원금 상환 안전성과 부도 위험을 나타내는 핵심 지표입니다.",
      "synonyms": ["신용등급", "신용도", "안전도", "채권 안정성", "투자적격", "우량 채권"],
      "sample_values": ["AAA", "AA+", "AA-", "A+"],
      "sample_queries": ["신용도 높은 안전한 채권 찾아줘", "위험등급 낮은 안전한 채권"],
      "hybrid_score": 0.4861
    },
    {
      "table_name": "tb_domestic_bond",
      "column_name": "buy_yield",
      "column_korean_name": "당사매수수익률 (실시간 매수금리)",
      "data_type": "NUMERIC(10, 4)",
      "description": "미래에셋증권 영업점 및 MTS/HTS에서 리테일 고객이 즉시 매수할 수 있는 실시간 매수 연환산 수익률(%)입니다 (매수가능 881건에만 존재).",
      "sample_values": ["3.2", "3.28", "3.35"],
      "hybrid_score": 0.4640
    }
  ]
}
```

---

## 5. 프롬프트 인클루드용 텍스트 (System Prompt Template)

에이전트의 System Prompt에 아래 텍스트를 그대로 삽입하여 사용할 수 있습니다:

```markdown
### 금융 스키마 탐색 도구: search_finance_metadata
금융 상품(채권, ETF, 펀드) 관련 질의를 처리할 때, 당신은 반드시 `search_finance_metadata` 도구를 호출하여 관련 테이블과 컬럼 정보를 먼저 확인해야 합니다.

* **호출 방법**:
  `search_finance_metadata(query="사용자의 질의 키워드", table_name="선택적 테이블명", top_k=3)`
* **결과 활용 가이드**:
  - 반환된 `table_name`과 `column_name`을 사용하여 SQL 쿼리를 구성하십시오.
  - `sample_values`를 참고하여 실제 데이터의 값 형식(예: Y/N 플래그, 등급 표기법, 날짜 포맷 등)에 맞게 WHERE 조건을 작성하십시오.
  - `data_type`을 확인하여 수치형 필터링(NUMERIC)인지 문자열 일치(VARCHAR)인지 정확히 구분하십시오.
```
