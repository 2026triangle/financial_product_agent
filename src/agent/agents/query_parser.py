'''
query_parser.py
v4.0 (Task 10 — 다중 카테고리 필터 버그 수정)
A1
가장 처음 사용자의 질의를 받고, 질의의 카테고리를 나누고 적절한 쿼리 데이터 첨부 에이전트를 호출하는 에이전트임

이 파일은 2026triangle/agent(개발 레포) 174줄 버전을 이식한 뒤 여러 차례 확장했다:
  - Task 8(qp_spec.md): PROMPT 주어/술어 명시적 열거, resolve_category로 채권분류 컬럼 라우팅,
    expand_rank_filter로 부등호 전개, meta_schema_table이 비어 있으면 search_finance_metadata를
    호출하지 않는 런타임 가드
  - Task 9(qp_task9_json.md): _extract_json을 균형매칭 스캐너로 교체, {{retry_hint}} 플레이스홀더로
    재시도 프롬프트 안정화
  - Task 10(qp_task10_multicategory.md): 값 정규화를 normalize_value 대신
    OntologyValidator.resolve_filter_value()로 전환(개념없음/변환불필요/값없음 구분), 필터를
    트리플 주어가 지목한 카테고리에만 적용(_build_filters로 분리)
'''

import json
import logging
import os
import random
import re
import sqlite3
import time

from agents.agent import Agent
from config import config
from tools.ontology_tool import get_ontology_validator
from tools.meta_search_tool import search_finance_metadata

logger = logging.getLogger("agent.agents.query_parser")

# LLM 출력이 JSON으로 안 깨질 때 재시도할 최대 횟수 (dev_docs.md: "json 출력 자체가
# 깨질 수 있음. 재시도 로직 필수")
MAX_PARSE_RETRIES = 3

PROMPT = '''
task : 주어진 질문을 분석해주세요.
질문은 반드시 "금융 제품(Financial products)" 에 관련한 질문이어야 합니다.
출력은 반드시 json 형태로 표현하세요.
분석에서는 질문을 "카테고리", "논리 검증", "수치 검증"으로 분리해야 합니다.

define :
* 카테고리 - 카테고리는 "domestic_bond(국내 채권)", "domestic_etf(국내 etf)", "foreign_etf(해외 etf)", "fund(공모 펀드)" 가 있습니다.
사용자의 요청은 모두 이 카테고리 안에 금융 상품에 대한 질문이어야 하며 하나 이상의 카테고리와 연관된 복합 질문일 수도 있습니다.
* 논리 검증 - 논리적으로 질문에 모순이 없는지 판단해야 합니다.
질문에서 가정하고 있는 사실을 온톨로지 트리플("주어 술어 목적어" 문자열)로 분리하세요.
반드시 "fp:" 접두사를 쓰세요.

주어는 반드시 다음 4개 중 하나여야 합니다 (다른 이름을 지어내지 마세요):
fp:Bond (국내채권) / fp:DomesticETF (국내ETF) / fp:ForeignETF (해외ETF) / fp:Fund (공모펀드)

술어는 반드시 다음 중에서 골라 쓰세요 (다른 이름을 지어내지 마세요):
fp:hasCreditRating (신용등급, fp:Bond 전용) / fp:hasRiskGrade (위험등급)
fp:hasBondCategory (채권분류, fp:Bond 전용) / fp:investsInAssetClass (투자자산군)
fp:investsInRegion (투자지역) / fp:isSellable (판매가능여부)

질문에 등급·분류·자산군·지역·판매가능 여부 등 "값"에 대한 가정이 하나라도 있으면
반드시 logical_verification 트리플로 표현하세요. 값 가정이 전혀 없을 때만 빈 배열을 반환하세요.

아래 속성은 정해진 값만 허용합니다. 목록에 없는 표현은 이 속성으로 쓰지 마세요.

fp:investsInAssetClass (13종):
  주식, 채권, 원자재, 혼합자산, 주식혼합, 채권혼합, 단기자금, 통화,
  부동산, 대안투자, 재간접, 대출형, 기타
fp:investsInRegion (13종):
  국내, 미국, 글로벌, 미국제외 글로벌, 이머징/브릭스, 중국, 일본, 인도,
  베트남, 아시아, 유럽, 남미/북미, 중동/아프리카
fp:hasRiskGrade (7종):
  1등급 매우높은위험 ~ 6등급 매우낮은위험, 미분류
fp:hasCreditRating (19종, fp:Bond 전용):
  AAA, AA+, AA0, AA-, A+, A0, A-, BBB+, BBB0, BBB-, BB+, BB0, BB-,
  B+, B0, B-, CCC, CC0, C0
fp:hasBondCategory (fp:Bond 전용, 대분류 6종):
  회사채, 특수채, 국공채, 개인투자용국채, 외화채권-회사채, 외화채권-금융채

업종·테마(반도체, 2차전지, 우주항공 등), 기업명, 기업 간 관계(자회사·계열사),
구성종목 이름은 위 속성 어디에도 해당하지 않습니다.
이런 표현은 logical_verification에 넣지 말고, 아래 필드에 그대로 담으세요.

  "unsupported_conditions" : ["반도체 테마", "에코프로의 자회사"]

상품명·종목명·펀드명·ETF명 등 특정 금융상품을 가리키는 고유명사가 질의에 나오면
logical_verification 트리플로 만들지 마세요(예: fp:hasName 같은 속성을 지어내지
마세요 — 그런 속성은 온톨로지에 없습니다). 대신 아래 필드에 그대로 담으세요.

  "product_name" : ["국민성장펀드"]

부분 이름이어도 그대로 담습니다("KODEX AI로봇" 등).

* 수치 검증 - 사용자가 수치적으로 확인하려는 조건을 분리하세요.

  아래 두 경우는 모두 logical_verification이 아니라 numerical_verification에
  부등호로 표현합니다.
   (1) 상대적 표현 — "낮은", "높은", "안전한", "우량한", "좋은"
   (2) 명시적 비교 — "AA- 이상", "3등급 이하", "BBB보다 높은", "5등급 미만"

  값은 반드시 ">=" "<=" ">" "<" 중 하나로 시작하고, 그 뒤에 등급 이름을 그대로 씁니다.
  한국어 "이상/이하"를 값 안에 남기지 마세요.

  "AA- 이상"        -> {"credit_rating": ">=AA-"}
  "BBB0 이하"       -> {"credit_rating": "<=BBB0"}
  "위험 낮은/안전한"  -> {"risk_grade": "<=5등급 낮은위험"}
  "위험 높은"        -> {"risk_grade": ">=2등급 높은위험"}

  비교어가 전혀 없이 등급 하나만 말한 경우("신용등급 AAA인", "5등급인")에만
  logical_verification 트리플로 표현하세요.

배경 지식 :
기본적으로 질문자는 한국인이며 한국에 세금을 납부합니다.
따라서 "국내"는 한국을 지칭합니다.

example :
[query]
{
    "query" : "현재 판매 가능한 원화채권 중 AAAA 이상이면서 수익률 상위 10% 알려줘"
}
[answer]
{
    "category" : ["domestic_bond"],
    "logical_verification" : ["fp:Bond fp:isSellable \\"true\\"", "fp:Bond fp:hasCreditRating \\"AAAA\\""],
    "numerical_verification" :
    {
        "rate_of_return" : ">10%"
    }
}

[query]
{
    "query" : "국고채권 중에 안전한 거 알려줘"
}
[answer]
{
    "category" : ["domestic_bond"],
    "logical_verification" : ["fp:Bond fp:hasBondCategory \\"국고채권\\""],
    "numerical_verification" : {}
}

[query]
{
    "query" : "위험 낮고 주식형인 국내ETF 추천해줘"
}
[answer]
{
    "category" : ["domestic_etf"],
    "logical_verification" : ["fp:DomesticETF fp:investsInAssetClass \\"주식형\\""],
    "numerical_verification" : {"risk_grade" : "<=5등급 낮은위험"}
}

[query]
{
    "query" : "현재 판매 가능한 원화채권 중 AA- 이상 종목 알려줘"
}
[answer]
{
    "category" : ["domestic_bond"],
    "logical_verification" : ["fp:Bond fp:isSellable \\"true\\""],
    "numerical_verification" : {"credit_rating" : ">=AA-"}
}

[query]
{
    "query" : "국민성장펀드의 구조와 투자전략 동향 등 찾아서 알려줘"
}
[answer]
{
    "category" : ["fund"],
    "logical_verification" : [],
    "numerical_verification" : {},
    "product_name" : ["국민성장펀드"]
}

{{retry_hint}}
query :
{
    "query" : {{query}}
}
answer :

'''

# 재시도(2회차부터)에서만 프롬프트에 끼워 넣는 안내문. base_query가 이미 "answer :"로
# 끝나는 상태에서 이 문구를 뒤에 그냥 이어붙이면(예전 방식) 답을 써야 할 자리에 새 지시문이
# 끼어들어 few-shot 패턴이 깨진다 — 그래서 PROMPT 안의 {{retry_hint}} 자리에 넣는다
# (qp_task9_json.md §4). 1회차엔 반드시 빈 문자열로 치환해야 한다.
RETRY_HINT = (
    "[중요] 직전 응답이 유효한 JSON이 아니었습니다. "
    "설명·해설·머리말 없이 JSON 객체 **하나만** 출력하세요. "
    "위 example 블록을 다시 출력하지 마세요."
)

# 트리플 목적어에서 술어 로컬명 → OntologyValidator 통제 어휘 개념명 매핑.
# fp:hasBondCategory는 라벨마다 컬럼(대/중/소분류)이 달라 resolve_category()로 별도 처리한다.
# fp:isSellable도 통제 어휘 클래스가 아니라 xsd:boolean이라 resolve_filter_value 대상이
# 아니다(판매가능 판정은 상품군마다 다른 컬럼/문자열 — ot_spec.md §2.6). 그래서 hasBondCategory와
# 마찬가지로 _build_filters의 전용 분기에서 처리하고(qp_task16_parser_fixes.md §3), 이 dict에는
# "등록된 술어"라는 기록용으로만 넣는다 — 실제로는 아래 전용 분기가 먼저 continue하므로 이
# dict를 거쳐 resolve_filter_value로 흘러가지 않는다(hasBondCategory가 애초에 이 dict에 없는
# 것과 대칭이지만, isSellable은 "미등록 술어 로그"(Step 2)에 잘못 걸리지 않는다는 걸 명시하기
# 위해 넣어둔다).
_PREDICATE_TO_CONCEPT = {
    "hasCreditRating": "CreditRating",
    "hasRiskGrade": "RiskGrade",
    "investsInAssetClass": "AssetClass",
    "investsInRegion": "Region",
    "isSellable": "Sellable",
}

# 클래스 로컬명 → 카테고리 문자열 역매핑. OntologyValidator.CATEGORY_TO_CLASS의 역방향
# (qp_task10_multicategory.md §2.1). 트리플의 주어가 이미 상품군을 지목하고 있을 때
# (fp:Bond/fp:DomesticETF/fp:ForeignETF/fp:Fund) 그 카테고리에만 필터를 적용하기 위함이다.
_CLASS_TO_CATEGORY = {
    "Bond": "domestic_bond",
    "DomesticETF": "domestic_etf",
    "ForeignETF": "foreign_etf",
    "Fund": "fund",
}

# 서열이 있는(부등호 전개가 되는) 개념만 대상으로 한다 (qp_spec.md §4.3).
_RANK_CONCEPTS = ("CreditRating", "RiskGrade")

# "주어 술어 목적어" 형태의 단순 트리플 문자열 파서 (OntologyValidator._parse_triple과 동일한
# 규칙을 독립적으로 구현 — validator의 파서는 private이라 여기서 재사용하지 않는다).
_TRIPLE_RE = re.compile(r'^\s*(\S+)\s+(\S+)\s+(.+?)\s*\.?\s*$')

# 큰따옴표뿐 아니라 작은따옴표 리터럴도 인정한다(qp_task16_parser_fixes.md §1, 버그 D) —
# LLM이 `'중국'`처럼 작은따옴표를 쓰면 예전 정규식(큰따옴표 전용)이 매칭에 실패해 따옴표가
# 그대로 값에 남았다("'중국'"). 두 대안(큰따옴표.../작은따옴표...) 중 하나만 매칭되도록
# 하되, 여는/닫는 따옴표 종류가 일치해야 한다 — `"값'`처럼 섞이면 두 대안 모두 실패해서
# (의도적으로) 리터럴로 인정하지 않고 원문 그대로 남긴다. 그룹 1=큰따옴표 안 값,
# 그룹 2=작은따옴표 안 값 — 정확히 하나만 채워진다(_split_triple에서 둘 중 있는 쪽을 쓴다).
# 언어태그(@ko)·데이터타입(^^xsd:string) 접미사, 이스케이프(\", \') 처리는 그대로 유지한다.
# 따옴표가 전혀 없는 값(`AAAA`)은 애초에 이 정규식에 매칭되지 않아 기존처럼 원문 그대로
# 쓰인다 — 이 동작은 바꾸지 않는다.
_LITERAL_RE = re.compile(
    r'^(?:"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\')'
    r'(?:@[A-Za-z][A-Za-z0-9-]*|\^\^\S+)?$'
)

# numerical_verification 값에서 "AA- 이상"류 부등호 표현을 잡아낸다.
# LLM에게 영어 부등호 기호(>=, <=, >, <)로 표현하도록 유도하는 편이 한국어 "이상/이하"
# 파싱보다 결정론적이라 이 형태를 우선 지원한다.
_RANK_INEQUALITY_RE = re.compile(r'^\s*(>=|<=|>|<|=)\s*(.+?)\s*$')

# PROMPT로 유도해도 LLM은 확률적이라 {"credit_rating": "AA- 이상"}처럼 한국어 비교
# 표현을 그대로 내보낼 수 있다(qp_task13_comparison.md §1.2 — 버그 A). 기호 패턴이
# 실패했을 때만 이 패턴으로 재시도한다. "이상의"/"이하의"를 "이상"/"이하"보다 먼저
# 나열해야 한다 — 알파벳(이 경우 한글) 순서가 아니라 더 긴 접미어가 먼저 매치되도록
# 순서를 맞춘 것이다("이상의"가 뒤에 있으면 "이상"이 먼저 매치되고 "의"가 라벨에
# 남아 끝(\s*$)에 걸려 전체 매치가 실패한다).
_RANK_KOREAN_RE = re.compile(r'^\s*(.+?)\s*(이상의|이상|초과|넘는|이하의|이하|미만)\s*$')

# 한국어 비교 표현 → 부등호 연산자. "초과"/"넘는"은 등급 자체를 포함하지 않는 진짜
# 초과라 ">"(포함하지 않음), 나머지("이상"류)는 ">="/"<="(포함함)로 매핑한다.
_RANK_KOREAN_OP = {
    "이상": ">=", "이상의": ">=",
    "초과": ">", "넘는": ">",
    "이하": "<=", "이하의": "<=",
    "미만": "<",
}


# 검증기(src/ontology/endpoint/ontology_validator.py, validate_triples())가 내는 위반
# reason 문구 중 "커버리지 밖"(온톨로지가 이 속성/클래스를 아예 모름 — LLM이 지어낸
# fp:hasName 같은 경우)에 해당하는 두 문구의 부분 문자열이다(qp_task14_productname.md §2).
# 근거 위치 — ontology_validator.py validate_triples():
#   "'{pred_raw}'는 온톨로지에 정의되지 않은 속성입니다." (술어 존재 검사)
#   "'{subj_raw}'는 온톨로지에 존재하지 않는 클래스입니다." (주어 클래스 존재 검사)
# 이 두 가지가 아닌 위반(허용값 위반, domain 위반, 트리플 형식 오류)은 전부 "진짜 모순"으로
# 취급해 기존처럼 즉시 컷한다 — 답변 불가 5문항 방어를 지키기 위한 보수적 기본값이다.
# 검증기 자체는 수정하지 않는다 — reason 문구가 바뀌면 이 상수 두 개만 고치면 된다.
_COVERAGE_VIOLATION_MARKERS = (
    "온톨로지에 정의되지 않은 속성입니다",
    "온톨로지에 존재하지 않는 클래스입니다",
)


def _is_coverage_violation(violation: dict) -> bool:
    """validate_triples()가 낸 위반 하나가 '온톨로지 커버리지 밖'(모르는 속성/클래스)인지
    판별한다. LLM 없이 직접 단위 테스트할 수 있도록 순수 함수로 둔다."""
    reason = violation.get("reason") or ""
    return any(marker in reason for marker in _COVERAGE_VIOLATION_MARKERS)


def _parse_rank_comparison(val):
    """numerical_verification 값 문자열에서 (연산자, 라벨)을 뽑아낸다.

    기호 표현(">=AA-")을 먼저 시도하고, 실패했을 때만 한국어 표현("AA- 이상")을
    시도한다(qp_task13_comparison.md §3) — 기호 쪽이 더 결정론적이라 우선순위를 둔다.
    둘 다 매칭되지 않으면 None을 반환한다. 로그는 호출부(query())에서 남긴다 —
    이 함수는 순수 파싱만 하고 로거를 갖지 않아 LLM 없이 직접 단위 테스트할 수 있다.
    """
    m = _RANK_INEQUALITY_RE.match(val)
    if m:
        return m.group(1), m.group(2).strip()
    m = _RANK_KOREAN_RE.match(val)
    if m:
        label = m.group(1).strip()
        op = _RANK_KOREAN_OP.get(m.group(2))
        if op and label:
            return op, label
    return None


def _extract_json(text):
    """LLM 응답 텍스트에서 JSON 객체를 최대한 관대하게 뽑아낸다.

    qp_task9_json.md §3 — find/rfind 방식과 코드펜스 정규식은 쓰지 않는다. 둘 다
    "첫 { 부터 마지막 } 까지"를 통째로 잘라내는 방식이라 출력에 완결된 JSON 객체가
    둘 이상이면(모델이 초안 후 다시 정리해서 출력하는 경우 등) 그 사이 텍스트까지
    삼켜서 반드시 깨진다. 대신 중괄호 깊이를 세는 스캐너로 완결된 `{...}` 후보를
    전부 모으고, 그중 파싱되는 dict만 골라 채택한다.

    - 문자열 리터럴 안의 중괄호/따옴표는 깊이 계산에서 제외한다(백슬래시 이스케이프 처리 포함).
      이게 없으면 `"description": "{인덱스}"` 같은 값에서 깊이가 어긋난다. 이 판정은
      **후보마다 독립적으로** 한다 — 텍스트 전체에 걸친 전역 "지금 문자열 안인가"
      상태를 미리 계산해두지 않는다. 전역으로 계산하면 앞쪽 해설 문장에 짝이 안 맞는
      큰따옴표가 하나만 있어도 그 뒤 전체가 "문자열 안"으로 잘못 인식돼, 그 뒤의
      진짜 JSON 시작 중괄호를 전부 후보에서 걸러버린다(교차검증 라운드 3에서 지적됨).
      깊이 카운터를 전역으로 하나만 두면 안 되는 것과 정확히 같은 이유다.
    - 코드펜스(```json ... ```)는 따로 처리하지 않는다 — 백틱은 중괄호가 아니라서
      스캐너가 자연히 무시한다.
    - 모든 `{`를 잠재적 후보 시작점으로 독립적으로 시도한다 — 앞선 시작점이 깨져
      있어도(안 닫히거나, 실은 문자열 안이었거나) 뒤따르는 완결된 객체를 놓치지 않는다.
      문자열 안의 `{`에서 시작을 시도해도 결과 조각이 올바른 JSON이 아니면 `json.loads`가
      실패해서 자연히 걸러진다 — 별도로 "문자열 안이니 시도조차 하지 말자"는 최적화를
      두지 않는다(그 최적화 자체가 위 버그의 원인이었다).
    - 다른 후보 범위 안에 완전히 포함된(중첩된) 후보는 제외한다. 예를 들어
      `numerical_verification`의 값처럼 최상위 객체 안에 중첩된 하위 객체도 독립적으로
      후보에 잡히는데, 이걸 걸러내지 않으면 최상위 객체에 "category" 키가 없는
      비정상 응답에서 하위 객체가 [-1] 선택 로직에 의해 전체 대신 잘못 뽑힐 수 있다
      (교차검증 라운드 3에서 지적됨).
    - 남은(중첩되지 않은) 후보가 여럿이면 "category" 키를 가진 것 중 마지막 것을
      채택한다(모델이 초안을 내고 다시 정리해 출력하면 나중 것이 최종본일 확률이 높다).
      "category" 키를 가진 게 하나도 없으면 그중 마지막 것을 쓴다.
    - 후보가 하나도 없거나 전부 파싱에 실패하면 None을 반환한다. 예외를 던지지 않는다
      (호출부가 재시도로 이어감). 시그니처(`_extract_json(text) -> dict | None`)는 그대로다.
    """
    if not text:
        return None

    n = len(text)

    # 문자열 밖/안 여부를 전역으로 미리 계산하지 않는다 — '{' 마다 독립적으로 자기만의
    # 상태(depth, 문자열 안 여부, 이스케이프)로 스캔하고, 완결되면 즉시 json.loads로
    # 검증한다. 유효한 dict만 spans에 (시작, 끝, 파싱결과)로 남긴다.
    spans = []
    for start in range(n):
        if text[start] != "{":
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(start, n):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:j + 1])
                    except (json.JSONDecodeError, TypeError):
                        obj = None
                    if isinstance(obj, dict):
                        spans.append((start, j, obj))
                    break
        # 끝까지 스캔해도 depth가 0으로 안 돌아오면(안 닫힌 조각) 이 시작점은 후보 없음.

    if not spans:
        return None

    # 다른 후보 범위 안에 완전히 포함된 후보(중첩된 하위 객체)는 제외한다.
    def _is_nested(idx):
        s, e, _ = spans[idx]
        return any(
            other_idx != idx and s2 <= s and e <= e2
            for other_idx, (s2, e2, _) in enumerate(spans)
        )

    top_level = [obj for i, (_, _, obj) in enumerate(spans) if not _is_nested(i)]
    if not top_level:
        top_level = [obj for _, _, obj in spans]  # 이론상 도달하지 않는 방어적 폴백

    with_category = [d for d in top_level if "category" in d]
    return with_category[-1] if with_category else top_level[-1]


def _ensure_str_list(value):
    """LLM이 배열 대신 단일 문자열(항목이 하나뿐일 때 흔한 실수)을 반환해도
    아래 로직이 문자열을 글자 단위로 순회하지 않도록 리스트로 정규화한다."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _split_triple(triple):
    """'fp:Bond fp:hasCreditRating "AAAA"' -> ('fp:Bond', 'fp:hasCreditRating', 'AAAA').
    validate_triples를 이미 통과한 트리플만 여기로 오므로 형식 파싱 실패는 사실상 없지만,
    방어적으로 실패 시 None을 반환한다."""
    m = _TRIPLE_RE.match(triple)
    if not m:
        return None
    subj, pred, obj_raw = m.group(1), m.group(2), m.group(3).strip()
    if obj_raw.endswith("."):
        obj_raw = obj_raw[:-1].strip()
    lit_m = _LITERAL_RE.match(obj_raw)
    if lit_m:
        # 그룹 1(큰따옴표)과 그룹 2(작은따옴표) 중 실제로 매칭된 쪽만 값이 채워진다.
        obj = lit_m.group(1) if lit_m.group(1) is not None else lit_m.group(2)
    elif ":" in obj_raw:
        obj = obj_raw.split(":", 1)[1]
    else:
        obj = obj_raw
    return subj, pred, obj


def _meta_index_ready() -> bool:
    """meta_schema_table에 조회 가능한 데이터가 있는지 가볍게 확인한다 (qp_spec.md §6, "건수 조회" 방식).

    get_mct_instance()/search_finance_metadata()를 직접 불러서 확인하면 안 된다 — MCT.__init__이
    preload 여부와 무관하게 BGE-M3 임베더부터 생성해버려서(약 2GB 다운로드) '비어있는지 확인'하는
    데 드는 비용이 실제 검색보다 커진다. 그래서 이미 존재하는 config.db.db_path를 딱 한 번,
    읽기 전용 COUNT 쿼리로만 직접 찔러본다 — 새 DB 의존성을 만드는 게 아니라 MCT가 이미 쓰는
    설정값을 재사용하는 것이다.

    DB 파일이 없거나(현재 이 제출 레포엔 DB 파일 자체가 없다 — 데이터는 네이버클라우드로 이전
    예정) 테이블이 없어도 예외를 삼키지 않고 로그만 남기고 False(= 미준비)로 처리한다.
    (qp_spec.md §6: "예외를 삼키지 말고 로그로 남긴다. 조용히 실패하면 나중에 원인을 못 찾는다")
    이는 하드코딩된 비활성화가 아니라 런타임 조건 검사이므로, meta_schema_table이 채워지는
    순간(로컬이든, 추후 네이버클라우드 DB로 config.db.db_path가 교체되든) 자동으로 정상 경로를 탄다.
    """
    db_path = config.db.db_path
    if not db_path or not os.path.exists(db_path):
        logger.info(f"meta_schema_table 확인 불가 — DB 파일 없음: {db_path!r}. search_finance_metadata를 건너뜁니다.")
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM meta_schema_table")
            count = cur.fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(f"meta_schema_table 건수 조회 실패, 검색 인덱스가 없다고 간주합니다: {e}")
        return False

    ready = count > 0
    if not ready:
        logger.info("meta_schema_table이 0건입니다 (BGE-M3 임베딩 미실행). search_finance_metadata를 건너뜁니다.")
    return ready


class QueryParser(Agent):
    def __init__(self, mm):
        super().__init__(mm)
        self.prompt = PROMPT
        # rdflib 로드 + 인덱스 구축은 여기서 한 번만 (get_ontology_validator가 프로세스당
        # 싱글톤을 보장 — endpoint/meta_column_table.py 싱글톤 관습과 동일)
        self.validator = get_ontology_validator()
        # Step 3(qp_task12_unanswerable.md §5) — JSON 모드 지원 여부를 프로세스 생애주기
        # 동안 한 번만 확인한다. QueryParser 인스턴스는 main.Agent가 프로세스당 하나만
        # 만들어 재사용하므로(agents/agent.py의 get_agents), 최초 1회 실패하면 그 뒤로는
        # 다시 시도하지 않고 바로 일반 모드로 호출한다 — 매번 재시도하면 지원이 안 될 때
        # 매 호출마다 실패+폴백 두 번을 호출하게 되어 "케이스당 정확히 1회" 전제가 깨진다.
        self._json_mode_supported = True

    def _call_llm(self, prompt: str) -> dict:
        """Agent.query()(agents/agent.py)와 동일한 {"content", "id"} 반환 계약을 유지하면서,
        가능하면 response_format={"type":"json_object"}로 호출해 JSON 파싱 실패/재시도를
        줄인다 (qp_task12_unanswerable.md §5).

        super().query()를 쓰지 않는 이유: Agent.query()는 mm.generate_text(query)만
        호출해서 response_format을 넘길 수 없다. 이 작업은 query_parser.py 한 파일만
        고치라는 제약이 있어 agents/agent.py는 건드리지 않는다.

        LLM 호출은 항상 self.mm.generate_text를 경유한다 — 회귀 테스트가 이 메서드를
        래핑해서 호출 횟수를 센다(다른 경로로 우회하면 호출 횟수가 0으로 세어져 테스트가
        깨진다). Gemini의 OpenAI 호환 레이어가 response_format을 지원하지 않아 400을
        내면 예외를 잡아 일반 모드로 폴백하고, 폴백 사실을 로그로 남긴다. 지원 여부를
        코드로 가정하지 않는다.
        """
        if self._json_mode_supported:
            try:
                content = self.mm.generate_text(prompt, response_format={"type": "json_object"})
            except Exception as e:
                logger.warning(
                    f"QueryParser: response_format(JSON 모드) 호출 실패, 일반 모드로 폴백합니다 "
                    f"(이번 프로세스에서는 이후 JSON 모드를 다시 시도하지 않음): "
                    f"{type(e).__name__}: {e}"
                )
                self._json_mode_supported = False
                content = self.mm.generate_text(prompt)
        else:
            content = self.mm.generate_text(prompt)

        query_id = str(time.time() + random.random())
        self.query_history[query_id] = {prompt: content}
        return {"content": content, "id": query_id}

    def _build_filters(self, triples, tables, think_trace):
        """검증을 통과한 트리플들을 SQL 필터로 조립한다. LLM 없이 단위 테스트할 수
        있도록 query()에서 분리했다 (qp_task10_multicategory.md §2.6).

        반환: (filters, excluded_categories)

        다중 카테고리 버그(§0/§1 버그 A) 수정: 트리플의 주어가 이미 상품군을 지목하고
        있으면(fp:Bond/fp:DomesticETF/fp:ForeignETF/fp:Fund) 그 카테고리에만 적용한다.
        예전에는 주어를 버리고 tables의 모든 카테고리와 교차 검사해서, 채권 신용등급이
        ETF 테이블에도 던져지며 엉뚱하게 걸러졌다. fp:Product/fp:ETF처럼 상품군을
        특정하지 않는 주어만 모든 카테고리를 순회하는 폴백 경로를 탄다.

        exclusion 판정(qp_task12_unanswerable.md §2~3으로 갱신) — resolve_filter_value()가
        구분해주는 reason 중 subject_scoped(주어가 카테고리를 명확히 지목)일 때:
          - value_not_in_table: 개념은 있는데 "이 값"만 이 테이블에 없다(원자재@펀드).
            카테고리를 제외한다 (Task 8 회귀 테스트 케이스 5).
          - not_in_table: 개념 자체가 이 테이블에 없다(자산군@채권, 위험등급@해외ETF).
            **이것도 카테고리를 제외한다.** Task 10 시점엔 "이 값이 없다"와 다르다고
            보고 제외하지 않았는데, 실제로는 더 심각한 쪽(개념 자체가 없음)이 더 관대하게
            처리되는 역전이었다 — 조건만 증발하고 카테고리는 남아서 해당 상품군 전체가
            무필터로 후보에 남는 문제가 있었다(qp_task12_unanswerable.md §2 실측).
          - 폴백 경로(subject_scoped=False)에서는 reason과 무관하게 어떤 카테고리도
            제외하지 않는다 — 안 맞는 조합은 조용히 건너뛰고 think_trace에만 남긴다.
            어느 카테고리를 겨냥한 조건인지 알 수 없기 때문이다.
        """
        filters = []
        excluded_categories = set()

        for triple in triples:
            split = _split_triple(triple)
            if split is None:
                continue
            subj, pred, obj = split
            subj_local = subj.split(":", 1)[-1] if ":" in subj else subj
            pred_local = pred.split(":", 1)[-1] if ":" in pred else pred

            own_category = _CLASS_TO_CATEGORY.get(subj_local)
            if own_category is None:
                # 주어가 4개 클래스 중 어디에도 안 걸린다(fp:Product/fp:ETF 등 상품군을
                # 특정하지 않는 클래스) — 이때만 모든 카테고리를 순회하는 폴백을 탄다.
                targets = dict(tables)
                subject_scoped = False
            elif own_category in tables:
                targets = {own_category: tables[own_category]}   # 주어가 지목한 카테고리에만 적용
                subject_scoped = True
            else:
                # 주어가 특정 카테고리(예: fp:Bond → domestic_bond)를 명확히 지목했는데
                # 그 카테고리가 tables(LLM이 뽑은 category 배열)에 없다 — LLM이 category
                # 배열과 트리플 주어를 서로 안 맞게 뽑아낸 경우다. 이걸 dict(tables) 폴백으로
                # 흘리면(예전 버그) 채권 전용 값(신용등급 등)이 엉뚱하게 다른 상품군(예: 펀드)
                # 테이블 필터로 새어나간다 — fp:Product 같은 진짜 무지목 주어와는 성격이
                # 다르므로 폴백하지 않고 이 트리플 자체를 건너뛴다.
                think_trace.append(
                    f"'{subj}'는 '{own_category}'를 지목하지만 이번 질의 카테고리에 없어 이 조건은 건너뜀"
                )
                targets = {}
                subject_scoped = True

            if pred_local == "hasBondCategory":
                # hasBondCategory의 domain은 fp:Bond뿐이라 정상 흐름(validate_triples를
                # 통과한 트리플)에서 subj_local은 항상 "Bond"다. 하지만 이 메서드 자체는
                # (단위 테스트나 방어적 관점에서) 임의의 주어로 호출될 수 있고, 그 경우
                # own_category가 None인 폴백 분기(targets=dict(tables))를 타면 targets에
                # "domestic_bond" 키가 아예 없을 수도 있다 — targets[category]로 직접
                # 참조하면 KeyError로 크래시한다(교차검증에서 지적됨). targets.get()으로
                # 안전하게 조회하고 없으면 건너뛴다.
                category = "domestic_bond"
                table = targets.get(category)
                if table is None:
                    think_trace.append(
                        f"'{obj}'는 채권분류 값이지만 이번 질의 카테고리에 'domestic_bond'가 "
                        f"없어 이 조건은 건너뜀"
                    )
                    continue
                info = self.validator.resolve_category(obj)
                if info is None:
                    continue
                think_trace.append(
                    f"채권분류 컬럼 라우팅: '{obj}' → {info['column']} = '{info['value']}' (level={info['level']})"
                )
                filters.append({
                    "column": info["column"], "op": "=", "values": [info["value"]],
                    "source": "ontology", "concept": "BondCategory",
                    "category": category, "table": table,
                })
                if info.get("ambiguous"):
                    think_trace.append(
                        f"'{obj}'는 레벨 간 동명이인이 있음(후보: {info['ambiguous']}) — "
                        f"상위 레벨({info['level']})을 기본값으로 사용"
                    )
                continue

            if pred_local == "isSellable":
                # isSellable은 통제 어휘가 아니라 xsd:boolean이라 resolve_filter_value 대상이
                # 아니다(Sellable을 타면 unknown_concept이 나온다) — hasBondCategory처럼
                # 전용 분기로 처리한다(qp_task16_parser_fixes.md §3.2).
                truthy = str(obj).strip().lower() in ("true", "y", "yes", "1", "예")
                if not truthy:
                    # "판매 불가"를 묻는 질의는 범위 밖이다 — 조건을 반영하지 않고
                    # 흔적만 남긴다(§5 "하지 말 것": 판매 불가 질의 지원은 이 작업 범위 밖).
                    think_trace.append("'판매 불가' 조건은 지원하지 않아 반영하지 않음")
                    continue
                # op을 "="이 아니라 "SELLABLE"이라는 전용 값으로 두는 이유 — 테이블마다
                # 실제 SQL이 다르다(국내채권만 buyable_quantity > 0, 나머지는 <컬럼> = <값>이고
                # 그 값조차 테이블마다 다르다 — 실측: tb_fund는 'Y'가 아니라 '판매중', §3.4).
                # "="로 두면 build_filter_clauses가 이 구분을 할 방법이 없다. values의 "Y"는
                # think_trace/디버깅용 표시일 뿐 실제 SQL 리터럴은 text_to_sql_agent.py가
                # 테이블별로 따로 정한다.
                for category, table in targets.items():
                    filters.append({
                        "column": None, "op": "SELLABLE", "values": ["Y"],
                        "source": "ontology", "concept": "Sellable",
                        "category": category, "table": table,
                    })
                    think_trace.append(f"판매가능 조건: {table}")
                continue

            concept = _PREDICATE_TO_CONCEPT.get(pred_local)
            if concept is None:
                # Step 2(qp_task16_parser_fixes.md §2, 버그 E) — 예전에는 여기서 조용히
                # continue해서 "판매 가능한" 같은 조건이 흔적 없이 사라졌다(신용등급 필터만
                # 남고 isSellable 조건이 증발). think_trace에 남겨야 사용자/평가자가 이
                # 조건이 반영되지 않았음을 알 수 있다(대회 응답 스키마의 think_trace는
                # "사고·추론·도구 사용 과정"이다).
                logger.info(f"필터로 변환할 수 없는 술어라 건너뜀: {pred_local} (트리플: {triple})")
                think_trace.append(f"'{pred_local}' 조건은 필터로 반영되지 않음")
                continue

            for category, table in targets.items():
                res = self.validator.resolve_filter_value(concept, obj, table)
                reason = res["reason"]

                if reason == "as_is":
                    think_trace.append(
                        f"값 사용: '{res['value']}' ({concept}) → {table}:{res['value']} "
                        f"(라벨이 원천값, 변환 불필요)"
                    )
                    filters.append({
                        "column": None, "op": "=", "values": [res["value"]],
                        "source": "ontology", "concept": concept,
                        "category": category, "table": table,
                    })
                elif reason == "normalized":
                    think_trace.append(f"값 정규화: '{obj}' ({concept}) → {table}:{res['value']}")
                    filters.append({
                        "column": None, "op": "=", "values": [res["value"]],
                        "source": "ontology", "concept": concept,
                        "category": category, "table": table,
                    })
                elif reason == "not_in_table":
                    # value_not_in_table과 같은 조건(subject_scoped)으로 카테고리를 제외한다
                    # (qp_task12_unanswerable.md §1 Step 1) — 개념 자체가 없는 쪽이 값만 없는
                    # 쪽보다 더 심각한데 예전엔 더 관대하게(제외 안 함) 처리되고 있었다.
                    think_trace.append(
                        f"{table}({category})에는 {concept} 정보 자체가 제공되지 않아 "
                        f"'{category}' 카테고리를 제외"
                    )
                    if subject_scoped:
                        excluded_categories.add(category)
                elif reason == "value_not_in_table":
                    think_trace.append(
                        f"'{obj}'는 {table}({category})에 없는 값이라 '{category}' 카테고리를 결과에서 제외"
                    )
                    if subject_scoped:
                        excluded_categories.add(category)
                elif reason == "unknown_value":
                    # 방어용 경로. 정상 흐름에서는 validate_triples()가 먼저 걸러낸다.
                    think_trace.append(f"'{obj}'는 {concept}의 알려진 값이 아님 (예상치 못한 경로)")

        return filters, excluded_categories

    def query(self, query):
        think_trace = []

        # 사용자 입력({{query}})은 항상 마지막에 치환한다. {{query}} -> {{retry_hint}}
        # 순서로 치환하면, 사용자 입력 텍스트 안에 우연히(또는 악의적으로) "{{retry_hint}}"
        # 라는 문자열이 들어있을 때 그다음 {{retry_hint}} 치환이 사용자 입력 내용까지
        # 건드려버린다. 그래서 온톨로지/재시도용 플레이스홀더를 전부 먼저 치환해
        # 더 이상 남은 플레이스홀더가 없는 상태를 만든 뒤, 사용자 입력을 맨 마지막에 넣는다.
        # json.dumps로 이스케이프해서 넣는 이유는 그대로다 — query에 큰따옴표나 줄바꿈이
        # 섞여 있으면 f'"{query}"' 단순 치환은 프롬프트 안의 JSON 예시 블록 문법을 깨뜨린다.
        query_json = json.dumps(query, ensure_ascii=False)

        # 1) LLM 응답 JSON 파싱 + 실패 시 재시도 (LLM 출력은 깨질 수 있음)
        parsed = None
        last_result = None
        for attempt in range(1, MAX_PARSE_RETRIES + 1):
            # {{retry_hint}}는 1회차엔 반드시 빈 문자열로 치환한다 — 빠뜨리면 플레이스홀더
            # 문자열이 그대로 LLM에 들어간다 (qp_task9_json.md §4).
            cp_query = self.prompt.replace('{{retry_hint}}', '' if attempt == 1 else RETRY_HINT)
            cp_query = cp_query.replace('{{query}}', query_json)
            last_result = self._call_llm(cp_query)
            # dict.get(key, default)는 키가 "없을 때"만 default를 쓴다 — 키는 있는데 값이
            # None(예: {"content": None})이면 그대로 None이 나온다. 아래 로깅에서
            # len(content)/content[:800]를 쓰므로 content는 항상 str이어야 한다.
            content = last_result.get('content') if isinstance(last_result, dict) else last_result
            content = content if isinstance(content, str) else ('' if content is None else str(content))
            parsed = _extract_json(content)
            if not isinstance(parsed, dict):
                # 최상위가 배열/문자열 등 dict가 아니면 이후 parsed.get(...) 호출이 전부
                # 깨지므로, 형식이 안 맞는 응답으로 간주하고 재시도 루프로 넘긴다.
                parsed = None
            if parsed is not None:
                # Step 4(qp_task12_unanswerable.md §6) — 파싱 성공 시에도 응답 길이를
                # 남긴다. 500자 안팎이면 서버 변동, 3000자 이상이면 장문 출력 문제로 본다
                # (지연이 모델 장문 출력 때문인지 서버 변동 때문인지 구분하는 근거).
                logger.info(f"QueryParser 응답 {len(content)}자 ({attempt}회차)")
                if attempt > 1:
                    logger.info(f"QueryParser JSON 파싱 성공 ({attempt}회차)")
                break
            # Step 1(qp_task9_json.md §2) — 실패 원문을 그대로 남긴다. 이게 없으면 왜
            # 깨졌는지(여러 JSON 객체? 코드펜스 중첩? 설명 붙임?) 눈으로 확인할 수 없다.
            logger.warning(
                f"QueryParser JSON 파싱 실패 ({attempt}/{MAX_PARSE_RETRIES}회), 재시도합니다. "
                f"원문[{len(content)}자]: {content[:800]!r}"
            )

        # Agent 기본 클래스(agents/agent.py)의 반환 계약({"content", "id"})은 이후 모든
        # 반환 경로에서 유지한다 — main.py의 Agent.query()가 result['content']를 그대로 읽는다.
        call_id = last_result.get('id') if isinstance(last_result, dict) else None

        if parsed is None:
            reason = f"질의 분석 결과를 {MAX_PARSE_RETRIES}회 시도에도 JSON으로 해석할 수 없습니다."
            suggestion = "질문을 조금 더 구체적으로 다시 말씀해 주시겠어요?"
            think_trace.append(f"LLM 응답을 {MAX_PARSE_RETRIES}회 시도에도 JSON으로 파싱하지 못함 (PROMPT/LLM 출력 문제)")
            return {
                "content": f"{reason} {suggestion}",
                "id": call_id,
                "answerable": False,
                "reason": reason,
                "suggestion": suggestion,
                "think_trace": think_trace,
            }

        categories = _ensure_str_list(parsed.get("category"))
        triples = _ensure_str_list(parsed.get("logical_verification"))
        numerical = parsed.get("numerical_verification")
        numerical = numerical if isinstance(numerical, dict) else {}
        # unsupported_conditions는 선택 필드다(qp_task13_comparison.md §4.1) — 키가 없어도
        # _ensure_str_list(None) == [] 라 기존 동작이 그대로 유지된다.
        unsupported = _ensure_str_list(parsed.get("unsupported_conditions"))
        # product_name도 선택 필드다(qp_task14_productname.md §3) — 상품명·종목명 등
        # 고유명사는 온톨로지 통제 어휘가 아니라 개방형 텍스트라 트리플로 만들지 않고
        # 여기서 별도 필드로 받아 LIKE 필터로 승격한다(아래 5.1).
        names = _ensure_str_list(parsed.get("product_name"))

        think_trace.append(f"LLM 카테고리 추출: {categories}")
        think_trace.append(
            f"LLM 논리 검증 트리플: {triples}" if triples
            else "논리 검증 트리플 없음 (값 가정이 없는 질의로 LLM이 판단함)"
        )
        if unsupported:
            # 이 필드만으로 컷하지 않는다 — 다른 조건이 유효하면 아래에서 그대로 부분
            # 답변을 진행한다. 유효 조건이 하나도 없어 아래 5.5) 가드가 컷하는 경우에만
            # 이 목록을 suggestion에 반영해 정확한 사유를 제시한다.
            think_trace.append(f"제공 데이터에 없는 조건: {unsupported}")
        if names:
            think_trace.append(f"LLM 상품명 추출: {names}")

        # 2) logical_verification 트리플을 validate_triples()에 통과
        if triples:
            verification = self.validator.validate_triples(triples)
            if not verification.get("valid", True):
                violations = verification["violations"]
                # Step 1(qp_task14_productname.md §2) — "커버리지 밖"(온톨로지가 모르는
                # 속성/클래스 — 예: LLM이 지어낸 fp:hasName)과 "진짜 모순"(허용값 위반,
                # domain 위반 등)을 구분한다. **모순 위반 판정을 반드시 먼저 한다** — 커버리지
                # 위반부터 걸러내고 나면(트리플을 지우고 다시 검증하는 식) 혼합 케이스에서
                # 컷 사유가 뒤바뀐다. 예를 들어 [hasName, hasCreditRating "AAAA"] 두 트리플이
                # 섞이면 반드시 AAAA(모순) 사유로 컷해야지 hasName(커버리지) 사유로 컷하면
                # 안 된다 — 여기서는 validate_triples를 한 번만 호출한 뒤 그 violations
                # 전체에서 모순만 먼저 골라내는 방식으로 이 순서를 지킨다.
                contradiction_violations = [v for v in violations if not _is_coverage_violation(v)]
                # 3) 모순 위반이 하나라도 있으면 지금처럼 즉시 컷 (답변 불가 5문항 방어 유지)
                if contradiction_violations:
                    violation = contradiction_violations[0]
                    reason = violation.get("reason", "질문에 논리적 모순이 있습니다.")
                    suggestion = violation.get("suggestion") or "질문을 다시 확인해 주세요."
                    think_trace.append(f"온톨로지 검증: 위반 — {reason}")
                    return {
                        "content": f"{reason} {suggestion}",
                        "id": call_id,
                        "answerable": False,
                        "reason": reason,
                        "suggestion": suggestion,
                        "think_trace": think_trace,
                    }
                # 4) 커버리지 위반만 있으면 컷하지 않는다 — 해당 트리플만 제외하고 나머지로
                # 계속 진행한다(§2.1 Step 4). 위에서 모순 위반이 있었다면 이미 반환했으므로
                # 이 시점의 violations는 전부 커버리지 위반뿐이다.
                coverage_triples = {v["triple"] for v in violations}
                for v in violations:
                    think_trace.append(f"온톨로지가 모르는 속성이라 검증에서 제외: {v['triple']}")
                triples = [t for t in triples if t not in coverage_triples]
                think_trace.append("온톨로지 검증: 나머지 트리플은 온톨로지와 모순되지 않음")
            else:
                think_trace.append("온톨로지 검증: 통과 (트리플이 온톨로지와 모순되지 않음)")

        # 4) 통과 시 카테고리 -> 소스 테이블 라우팅
        tables = {}
        for category in categories:
            table_name = self.validator.resolve_source_table(category)
            if table_name:
                tables[category] = table_name
            else:
                logger.warning(f"알 수 없는 카테고리라 소스 테이블을 해석하지 못했습니다: '{category}'")
                think_trace.append(f"카테고리 '{category}'의 소스 테이블을 찾지 못해 제외")

        # 5) 트리플별 값 정규화 / 채권분류 컬럼 라우팅 (qp_task10_multicategory.md §2, §3)
        filters, excluded_categories = self._build_filters(triples, tables, think_trace)

        # 5.1) 상품명 필터 승격 (qp_task14_productname.md §3.2) — 상품명·종목명은 통제
        # 어휘가 아니라 개방형 텍스트라 온톨로지가 보증할 수 없다. source를 "ontology"가
        # 아니라 "user_text"로 표시해 하류(TextToSQLAgent)가 이 필터의 근거를 구분할 수
        # 있게 한다. column/table/category를 비워두는 이유는 아래 §3.3 처리와도 관련된다 —
        # 이 필터는 특정 상품군에 묶이지 않고 조회 대상 테이블 전부에 적용돼야 한다.
        for name in names:
            filters.append({
                "column": None, "op": "LIKE", "values": [name],
                "source": "user_text", "concept": "ProductName",
                "category": None, "table": None,
            })
            think_trace.append(f"상품명 텍스트 검색: '{name}' (LIKE)")

        # excluded_categories를 tables에서 실제로 제거한다 — 그렇지 않으면 table_results/
        # search_finance_metadata 호출만 건너뛸 뿐 resolved['tables']엔 그대로 남아서,
        # 이 결과를 읽는 다음 단계(Text-to-SQL)가 제외됐어야 할 테이블을 계속 조회 대상으로
        # 본다(교차검증에서 지적됨). 이 시점 이후로는 tables가 "실제로 조회할 카테고리"다.
        if excluded_categories:
            tables = {k: v for k, v in tables.items() if k not in excluded_categories}

        # 5.2) §3.3 정책 — 상품명 필터가 있으면 컷하지 않는다. "국민성장펀드 알려줘"처럼
        # 상품군을 명시하지 않은 질의는 LLM이 category를 못 뽑거나(구멍 A), 다른 조건 때문에
        # tables가 전부 제외될 수 있다(구멍 B). 두 정책(4개 테이블 전체를 기본값으로 채우기 /
        # "어느 상품군인지" 역질문) 중 전자를 택했다 — 역질문 왕복 없이 즉시 이름 검색을
        # 시도하는 편이 사용자 경험이 낫고, 존재하지 않는 상품명이면 검색 결과가 0건으로
        # 나와 자연스럽게 걸러진다(§6 완료 후 보고에서 이 선택과 이유를 다시 밝힌다).
        if names and not tables:
            for category in _CLASS_TO_CATEGORY.values():
                table_name = self.validator.resolve_source_table(category)
                if table_name:
                    tables[category] = table_name
            think_trace.append(
                "상품명 조건이 있어 상품군을 특정하지 못해도 컷하지 않고 "
                "4개 테이블 전체를 대상으로 이름 검색"
            )

        # 5.5) 답할 대상이 하나도 안 남으면 컷한다 (qp_task12_unanswerable.md §1 Step 2).
        # "구멍 A" — LLM이 category 자체를 못 뽑음(예: "외계채권ㅋㅋ") — triples가 비어
        # 온톨로지 검증(2단계)까지 통째로 건너뛰고도 answerable: True로 새어나갔다.
        # "구멍 B" — category는 있었으나 이 시점 위의 not_in_table/value_not_in_table
        # 판정으로 전부 제외됨(예: "위험등급 1등급인 해외ETF"). 두 경우 다 "답할 재료가
        # 하나도 없는데 answerable: True"라는 같은 모순이라 여기서 한 번에 막는다.
        # 반드시 excluded_categories를 tables에 반영한 *뒤*에 검사해야 Step 1의 효과가 걸린다.
        if not tables:
            if not categories:
                # 구멍 A
                reason = "질문에서 조회할 수 있는 상품군을 찾지 못했습니다."
                suggestion = ("국내채권, 국내ETF, 해외ETF, 공모펀드 중 어느 상품군을 찾으시나요? "
                              "해당 상품군에 없는 조건(예: 해외ETF의 위험등급)이 포함됐을 수도 있습니다.")
            else:
                # 구멍 B — 어떤 조건 때문에 제외됐는지가 이미 위 think_trace에 남아 있으므로
                # 그대로 재사용해 역질문 품질을 높인다(§4.1, 선택사항이지만 비용이 적어 적용).
                reason = "질문에 포함된 조건이 요청하신 상품군에는 적용되지 않습니다."
                # not_in_table 로그는 "...카테고리를 제외", value_not_in_table 로그는
                # "...카테고리를 결과에서 제외"라 문구가 살짝 달라서 "제외"만으로 넓게 잡는다.
                exclusion_notes = [t for t in think_trace if "제외" in t]
                if exclusion_notes:
                    suggestion = f"{exclusion_notes[-1]}. 다른 상품군으로 다시 질문해 주시겠어요?"
                else:
                    suggestion = ("해당 상품군에 없는 조건이 포함됐을 수도 있습니다. "
                                  "다른 상품군으로 다시 질문해 주시겠어요?")
            if unsupported:
                # 버그 C(qp_task13_comparison.md §0) — 테마·기업명·기업관계·구성종목처럼
                # 온톨로지 어떤 속성에도 안 걸리는 조건이 유효 조건 전무의 실제 원인일 때,
                # "AssetClass 값이 아님" 류의 엉뚱한 사유 대신 정확한 사유를 댄다.
                reason = "질문에 포함된 조건 중 일부가 제공된 데이터에 없습니다."
                suggestion = (
                    "'" + "', '".join(unsupported) + "' 정보는 "
                    "제공된 데이터에 포함되어 있지 않아 확인이 어렵습니다."
                )
            think_trace.append("조회 가능한 카테고리가 없어 답변 불가로 판정")
            return {
                "content": f"{reason} {suggestion}",
                "id": call_id,
                "answerable": False,
                "reason": reason,
                "suggestion": suggestion,
                "think_trace": think_trace,
            }

        # 6) 부등호 질의 전개 (qp_spec.md §4.3) — best-effort.
        # numerical_verification 키는 LLM이 자유롭게 붙이므로 개념명을 알 수 없다.
        # 값이 ">=AA-"(기호) 또는 "AA- 이상"(한국어) 형태면 서열이 있는 개념
        # (CreditRating/RiskGrade) 순으로 expand_rank_filter를 시도해서, 매칭되는
        # 첫 개념의 결과만 채택한다. 어느 쪽 표현으로도 해석하지 못하면 예전에는
        # 조용히 버려졌다(qp_task13_comparison.md §1.2) — 이제 로그로 남긴다.
        for key, val in numerical.items():
            if not isinstance(val, str):
                continue
            comparison = _parse_rank_comparison(val)
            if comparison is None:
                logger.info(
                    f"numerical_verification.{key}='{val}' — 부등호 표현으로 해석하지 못해 전개를 건너뜀"
                )
                continue
            op, label = comparison
            for concept in _RANK_CONCEPTS:
                try:
                    expanded = self.validator.expand_rank_filter(concept, label, op)
                except ValueError:
                    expanded = []
                if expanded:
                    think_trace.append(
                        f"부등호 질의 전개: numerical_verification.{key}='{val}' → {concept} IN {expanded}"
                    )
                    # 이 경로는 개념만 알고 테이블을 모른다(qp_task10_multicategory.md §2.4) —
                    # category/table을 명시적으로 None으로 채워서 다른 필터와 구조를 맞추되
                    # 동작은 바꾸지 않는다.
                    filters.append({
                        "column": None, "op": "IN", "values": expanded,
                        "source": "ontology", "concept": concept,
                        "category": None, "table": None,
                    })
                    break

        # 7) meta_search 안전장치 (qp_spec.md §6) — meta_schema_table이 비어 있으면
        # search_finance_metadata를 호출하지 않는다 (BGE-M3 다운로드/무의미한 빈 검색 방지).
        table_results = {}
        if _meta_index_ready():
            # excluded_categories는 이미 위에서 tables 자체를 걸러내는 데 반영했으므로
            # 여기서 다시 확인할 필요가 없다.
            for category, table_name in tables.items():
                table_results[category] = search_finance_metadata(query=query, table_name=table_name)
        else:
            think_trace.append(
                "meta_schema_table이 비어 있어(BGE-M3 임베딩 미실행) search_finance_metadata 호출을 건너뜀"
            )

        return {
            "content": json.dumps(parsed, ensure_ascii=False),
            "id": call_id,
            "answerable": True,
            "reason": None,
            "suggestion": None,
            "parsed": parsed,
            "resolved": {"tables": tables, "filters": filters},
            "table_results": table_results,
            "think_trace": think_trace,
        }
