'''
query_parser.py
v2.0 (Task 8 — 온톨로지 런타임 연동 완성본)
A1
가장 처음 사용자의 질의를 받고, 질의의 카테고리를 나누고 적절한 쿼리 데이터 첨부 에이전트를 호출하는 에이전트임

이 파일은 2026triangle/agent(개발 레포) 174줄 버전을 이식한 뒤, query_parser_instructions/qp_spec.md
Step 2~4를 반영해 확장했다:
  - PROMPT: 주어/술어 명시적 열거 + "값 가정이 있으면 반드시 트리플화" 강화 + 채권분류 예시 추가
  - normalize_value로 값 정규화, resolve_category로 채권분류 컬럼 라우팅, expand_rank_filter로 부등호 전개
  - meta_schema_table이 비어 있으면 search_finance_metadata를 호출하지 않는 런타임 가드
'''

import json
import logging
import os
import re
import sqlite3

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

* 수치 검증 - 사용자가 수치적으로 확인하고자 하는 주제를 분리하세요.

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

query :
{
    "query" : {{query}}
}
answer :

'''

# 트리플 목적어에서 술어 로컬명 → OntologyValidator 통제 어휘 개념명 매핑.
# fp:hasBondCategory는 라벨마다 컬럼(대/중/소분류)이 달라 resolve_category()로 별도 처리한다.
# fp:isSellable은 통제 어휘 클래스가 아니라 xsd:boolean이라 normalize_value 대상이 아니다
# (판매가능 판정은 상품군마다 다른 컬럼/문자열이며 common.ttl 주석 참고 — 이 작업 범위 밖).
_PREDICATE_TO_CONCEPT = {
    "hasCreditRating": "CreditRating",
    "hasRiskGrade": "RiskGrade",
    "investsInAssetClass": "AssetClass",
    "investsInRegion": "Region",
}

# 서열이 있는(부등호 전개가 되는) 개념만 대상으로 한다 (qp_spec.md §4.3).
_RANK_CONCEPTS = ("CreditRating", "RiskGrade")

# "주어 술어 목적어" 형태의 단순 트리플 문자열 파서 (OntologyValidator._parse_triple과 동일한
# 규칙을 독립적으로 구현 — validator의 파서는 private이라 여기서 재사용하지 않는다).
_TRIPLE_RE = re.compile(r'^\s*(\S+)\s+(\S+)\s+(.+?)\s*\.?\s*$')
_LITERAL_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"(?:@[A-Za-z][A-Za-z0-9-]*|\^\^\S+)?$')

# numerical_verification 값에서 "AA- 이상"류 부등호 표현을 잡아낸다.
# LLM에게 영어 부등호 기호(>=, <=, >, <)로 표현하도록 유도하는 편이 한국어 "이상/이하"
# 파싱보다 결정론적이라 이 형태를 우선 지원한다.
_RANK_INEQUALITY_RE = re.compile(r'^\s*(>=|<=|>|<|=)\s*(.+?)\s*$')


def _extract_json(text):
    """LLM 응답 텍스트에서 JSON 객체를 최대한 관대하게 뽑아낸다.
    코드펜스(```json ... ```)로 감싸져 오거나 앞뒤에 설명 문장이 붙어 오는 경우를 모두 처리한다.
    파싱에 실패하면 None을 반환한다 (예외를 던지지 않음 — 호출부에서 재시도로 이어감)."""
    if not text:
        return None
    text = text.strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


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
        obj = lit_m.group(1)
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

    def query(self, query):
        think_trace = []

        # json.dumps로 이스케이프해서 넣는다 — query에 큰따옴표나 줄바꿈이 섞여 있으면
        # f'"{query}"' 단순 치환은 프롬프트 안의 JSON 예시 블록 문법을 깨뜨린다.
        base_query = self.prompt.replace('{{query}}', json.dumps(query, ensure_ascii=False))

        # 1) LLM 응답 JSON 파싱 + 실패 시 재시도 (LLM 출력은 깨질 수 있음)
        parsed = None
        last_result = None
        cp_query = base_query
        for attempt in range(1, MAX_PARSE_RETRIES + 1):
            last_result = super().query(cp_query)
            content = last_result.get('content', '') if isinstance(last_result, dict) else str(last_result)
            parsed = _extract_json(content)
            if not isinstance(parsed, dict):
                # 최상위가 배열/문자열 등 dict가 아니면 이후 parsed.get(...) 호출이 전부
                # 깨지므로, 형식이 안 맞는 응답으로 간주하고 재시도 루프로 넘긴다.
                parsed = None
            if parsed is not None:
                break
            logger.warning(f"QueryParser JSON 파싱 실패 ({attempt}/{MAX_PARSE_RETRIES}회), 재시도합니다.")
            cp_query = base_query + (
                "\n\n[재시도 안내] 방금 응답이 유효한 JSON이 아니었습니다. "
                "다른 설명 없이 JSON 객체 하나만 출력하세요."
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

        think_trace.append(f"LLM 카테고리 추출: {categories}")
        think_trace.append(
            f"LLM 논리 검증 트리플: {triples}" if triples
            else "논리 검증 트리플 없음 (값 가정이 없는 질의로 LLM이 판단함)"
        )

        # 2) logical_verification 트리플을 validate_triples()에 통과
        if triples:
            verification = self.validator.validate_triples(triples)
            # 3) 위반이면 즉시 컷 — LLM 추가 호출 없이 반환
            if not verification.get("valid", True):
                violation = verification["violations"][0]
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

        # 5) 트리플별 값 정규화 / 채권분류 컬럼 라우팅 (qp_spec.md §4.1, §4.2)
        filters = []
        excluded_categories = set()
        for triple in triples:
            split = _split_triple(triple)
            if split is None:
                continue
            _subj, pred, obj = split
            pred_local = pred.split(":", 1)[-1] if ":" in pred else pred

            if pred_local == "hasBondCategory":
                info = self.validator.resolve_category(obj)
                if info is None:
                    continue
                think_trace.append(
                    f"채권분류 컬럼 라우팅: '{obj}' → {info['column']} = '{info['value']}' (level={info['level']})"
                )
                filters.append({
                    "column": info["column"], "op": "=", "values": [info["value"]],
                    "source": "ontology", "concept": "BondCategory",
                })
                if info.get("ambiguous"):
                    think_trace.append(
                        f"'{obj}'는 레벨 간 동명이인이 있음(후보: {info['ambiguous']}) — "
                        f"상위 레벨({info['level']})을 기본값으로 사용"
                    )
                continue

            concept = _PREDICATE_TO_CONCEPT.get(pred_local)
            if concept is None:
                continue

            for category, table in tables.items():
                normalized = self.validator.normalize_value(concept, obj, table)
                if normalized is None:
                    # 해당 상품군 테이블에 이 개념 자체가 없는 경우다(예: 원자재는 tb_fund에 없음).
                    # 컷하지 않고 이 카테고리만 결과에서 제외한다 (qp_spec.md §4.1).
                    excluded_categories.add(category)
                    think_trace.append(
                        f"'{obj}'는 {table}({category})에 해당 개념({concept})이 없어 "
                        f"'{category}' 카테고리를 결과에서 제외"
                    )
                else:
                    think_trace.append(f"값 정규화: '{obj}' ({concept}) → {table}:{normalized}")
                    filters.append({
                        "column": None, "op": "=", "values": [normalized],
                        "source": "ontology", "concept": concept, "table": table,
                    })

        # 6) 부등호 질의 전개 (qp_spec.md §4.3) — best-effort.
        # numerical_verification 키는 LLM이 자유롭게 붙이므로 개념명을 알 수 없다.
        # 값이 ">=AA-" 처럼 부등호로 시작하면 서열이 있는 개념(CreditRating/RiskGrade) 순으로
        # expand_rank_filter를 시도해서, 매칭되는 첫 개념의 결과만 채택한다.
        for key, val in numerical.items():
            if not isinstance(val, str):
                continue
            m = _RANK_INEQUALITY_RE.match(val)
            if not m:
                continue
            op, label = m.group(1), m.group(2).strip()
            for concept in _RANK_CONCEPTS:
                try:
                    expanded = self.validator.expand_rank_filter(concept, label, op)
                except ValueError:
                    expanded = []
                if expanded:
                    think_trace.append(
                        f"부등호 질의 전개: numerical_verification.{key}='{val}' → {concept} IN {expanded}"
                    )
                    filters.append({
                        "column": None, "op": "IN", "values": expanded,
                        "source": "ontology", "concept": concept,
                    })
                    break

        # 7) meta_search 안전장치 (qp_spec.md §6) — meta_schema_table이 비어 있으면
        # search_finance_metadata를 호출하지 않는다 (BGE-M3 다운로드/무의미한 빈 검색 방지).
        table_results = {}
        if _meta_index_ready():
            for category, table_name in tables.items():
                if category in excluded_categories:
                    continue
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
