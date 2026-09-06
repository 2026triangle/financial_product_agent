"""
agents/text_to_sql_agent.py

③ Text-to-SQL 에이전트 (A/B 통합).
query_parser(①)가 넘겨준 확정 조건(resolved.filters) + MCT 검색 결과(table_results)를
받아서, 하나의 테이블에 대한 SELECT SQL을 생성하고 가드레일 도구로 실행한다.
실패하면 에러 메시지를 다음 프롬프트에 넣어 재시도한다.
"""

import glob
import json
import logging
import os
import re

from agents.agent import Agent
from config import config, SRC_DIR
from tools.concept_column_map import CONCEPT_COLUMN_MAP, PRODUCT_NAME_COLUMNS
from tools.sql_guard_tool import execute_sql_query

logger = logging.getLogger("agent.agents.text_to_sql_agent")

MAX_SQL_RETRIES = 3

PROMPT_TEMPLATE = '''
당신은 금융 상품 데이터베이스에 대한 SQL(SQLite) 전문가입니다.
아래 사용자 질의에 맞는 SELECT SQL을 정확히 하나만 작성하세요.

[사용자 질의]
{query}

[대상 테이블]
{table}

[이미 확정된 조건 — 반드시 WHERE절에 그대로 포함]
{confirmed_clauses}

[질의와 관련된 후보 컬럼 — 의미 기반 검색 결과, 참고용]
{candidate_columns}

[테이블의 전체 컬럼 목록 — 이 안에 있는 컬럼명만 사용]
{all_columns}

지침:
- SELECT 문 하나만 출력하세요. 설명, 마크다운, 코드펜스 없이 SQL 텍스트만 출력합니다.
- WHERE절에는 [이미 확정된 조건]에 명시된 것만 사용하세요. 그 외의 조건은
  원칙적으로 추가하지 마세요.
- 사용자 질의에는 여러 상품 유형(채권, ETF, 펀드 등)이 함께 언급될 수 있습니다.
  지금 조회 대상은 오직 위 [대상 테이블] 하나뿐입니다. 질의 문장 중 다른 상품
  유형이나 다른 테이블에만 해당하는 단어(예: 대상이 채권 테이블인데 질의에
  "ETF", "펀드", "주식형" 같은 단어가 섞여 있는 경우, 또는 대상이 ETF 테이블인데
  "신용등급", "AAA" 같은 채권 전용 단어가 섞여 있는 경우)는 이 테이블과 무관하니
  절대로 조건에 반영하지 말고 완전히 무시하세요.
- 상품명/종목명(펀드명, ETF명, 브랜드명 등 고유명사)에 대한 조건은 반드시
  [이미 확정된 조건]에 이미 명시되어 있을 때만 반영하세요. 확정된 조건에
  상품명 조건이 없다면, 질의문에 상품명처럼 보이는 단어가 있어도 절대로
  스스로 등호(=)나 LIKE 조건을 만들어 추가하지 마세요. 상품명 매칭은 이
  프롬프트 밖의 별도 검증 로직에서만 처리합니다.
- [이미 확정된 조건]에 없는 새 조건을 스스로 추가하고 싶다면, 그 조건이
  명백히 [대상 테이블]의 상품 자체 특성(다른 카테고리 언급이 전혀 섞이지 않은
  표현)에 대한 것일 때만, 그리고 후보 컬럼의 설명/샘플값으로 근거가 확실할
  때만 추가하세요(단, 위 상품명 조건은 예외 없이 적용됩니다). 조금이라도
  애매하면 추가하지 말고 확정된 조건만 사용하세요.
- 존재하지 않는 컬럼명을 사용하지 마세요.
- LIMIT은 넣지 않아도 됩니다 (실행 시 자동으로 붙습니다).
'''


def _sql_literal(v) -> str:
    return "'{}'".format(str(v).replace("'", "''"))


def _resolve_column(f: dict, table: str) -> str:
    if f.get("column"):
        return f["column"]
    concept = f.get("concept")
    return CONCEPT_COLUMN_MAP.get(table, {}).get(concept)


# 판매가능(Sellable) 판정 — 테이블마다 컬럼뿐 아니라 "판매 가능"을 뜻하는 값 자체도
# 다르다(qp_task16_parser_fixes.md §3.4, ot_spec.md §2.6). DB 실측 결과(§3.4 명령으로
# 확인함):
#   tb_domestic_etf.pd_sale_yn ∈ {'Y', 'N'}      → 'Y'
#   tb_foreign_etf.pd_sale_yn  ∈ {'Y', None}     → 'Y'
#   tb_fund.sale_yn            ∈ {'판매중','판매완료'} → 'Y'가 아니라 '판매중'
# 값을 'Y'로 가정하지 말라는 지시(§5 "하지 말 것")에 따라 실측값을 그대로 쓴다.
# tb_domestic_bond는 값 비교가 아니라 buyable_quantity > 0 판정이라 이 맵에 없다.
_SELLABLE_TRUE_VALUE = {
    "tb_fund": "판매중",
}
_DEFAULT_SELLABLE_TRUE_VALUE = "Y"


# --------------------------------------------------------------------------
# 상품명 토큰 분해 (qp_task17_name_matching.md §1~§2, 버그: "국민성장펀드" 0건)
# --------------------------------------------------------------------------
# 진단(§1, 실제 DB 조회) 결과: "국민성장펀드"로는 0건이지만 접미사 "펀드"를 뗀
# "국민성장"만으로는 4건이 걸린다(미래에셋국민참여형국민성장혼합자산투자신탁...).
# DB 정식명은 회사명+상품특징이 전부 이어 붙은 풀네임이라, 사용자가 흔히 쓰는 통칭
# 뒤에 붙이는 상품유형 접미사가 오히려 매칭을 막는다. 길게 매칭되는 접미사부터
# 먼저 시도해야 한다("증권투자신탁"이 "투자신탁"을 부분 포함하므로 순서가 중요).
_PRODUCT_NAME_SUFFIXES = ("상장지수펀드", "증권투자신탁", "투자신탁", "펀드", "ETF")
_PRODUCT_NAME_SUFFIXES_UPPER = {s.upper() for s in _PRODUCT_NAME_SUFFIXES}

# 공백으로 쪼갠 토큰 끝에 붙는 흔한 조사만 보수적으로 제거한다(한 글자, 토큰이 조사
# 하나만 남지 않을 때만) — 형태소 분석기 없이 쓸 수 있는 최소한의 처리다.
_TRAILING_JOSA = ("은", "는", "이", "가", "을", "를", "의", "와", "과", "도", "만")

# 토큰이 너무 많으면 AND로 다 묶었을 때 오히려 0건이 되기 쉽다(§2.1) — 앞 3개만 쓴다.
_MAX_DECOMPOSED_TOKENS = 3


def _strip_product_name_suffix(name: str) -> str:
    """상품명 끝에 붙은 상품유형 접미사를 하나 제거한다(있으면). "ETF"는 영문이라
    대소문자를 가리지 않는다. 원문 전체가 접미사 하나와 완전히 같으면(예: "상장지수펀드"
    자체, "증권투자신탁" 자체) 아예 제거하지 않고 그대로 둔다 — 검색어가 통째로
    사라지면 안 된다. 이 전체 일치 검사를 루프보다 먼저 해야 한다(교차검증에서
    지적됨): 루프 안에서 "각 접미사마다 len(name) > len(suf)"만 확인하면, 가장 긴
    접미사(예: "상장지수펀드", 6글자)와는 원문 전체 길이가 같아 건너뛰고도, 그
    접미사에 포함된 더 짧은 접미사("펀드", 2글자)에는 걸려버려 "상장지수"처럼
    엉뚱하게 잘렸다."""
    if name.upper() in _PRODUCT_NAME_SUFFIXES_UPPER:
        return name
    for suf in _PRODUCT_NAME_SUFFIXES:
        if len(name) > len(suf) and name.upper().endswith(suf.upper()):
            return name[: -len(suf)]
    return name


def _strip_trailing_josa(token: str) -> str:
    """토큰 끝의 조사 한 글자를 뗀다. 뗀 뒤 남는 길이가 최소 2는 돼야 한다(교차검증
    라운드 3에서 지적) — "인도"/"대만"/"시가"처럼 조사와 우연히 같은 글자로 끝나는
    2글자 고유명사를 "인"/"대"/"시" 한 글자로 잘라버리면, 그 한 글자가 "인버스"/
    "인프라" 같은 무관한 상품명에도 부분 일치해 LIKE 검색의 정밀도가 완전히 무너진다.
    len(token) > len(josa)만으로는 이 경우를 못 막는다(2 > 1이라 통과해버린다)."""
    for josa in _TRAILING_JOSA:
        if len(token) - len(josa) >= 2 and token.endswith(josa):
            return token[: -len(josa)]
    return token


def decompose_product_name_tokens(name: str) -> list:
    """상품명을 접미사 제거 + 공백/조사 분해로 AND 매칭용 토큰 목록을 만든다
    (qp_task17_name_matching.md §2.1). 원문에 공백이 없으면(예: "국민성장펀드") 접미사만
    떼어낸 하나의 토큰을 그대로 반환한다 — "토큰이 1개면 지금처럼 단일 LIKE로 둔다"는
    요구사항이 자연히 지켜진다(형태소 분석기 없이 임의로 더 쪼개지 않는다). 공백이
    있으면 각 조각의 끝 조사만 떼고, 3개를 넘으면 앞 3개만 쓴다."""
    stripped = _strip_product_name_suffix(name.strip())
    raw_tokens = [t for t in stripped.split() if t]
    if not raw_tokens:
        return [stripped] if stripped else [name]
    tokens = [_strip_trailing_josa(t) for t in raw_tokens]
    tokens = [t for t in tokens if t] or raw_tokens
    return tokens[:_MAX_DECOMPOSED_TOKENS]


def _broaden_name_filters(filters: list):
    """filters 중 상품명(ProductName) LIKE 필터를 찾아 토큰 분해된 새 filters 리스트를
    만든다(qp_task17_name_matching.md §2.1 2단 재시도용). 분해해도 원래 검색어와 똑같은
    토큰 1개뿐인 필터(재시도할 이유가 없음)는 건너뛰고 다음 ProductName 필터를 계속
    찾는다 — 질의에 상품명이 여러 개 섞여 있으면(예: "삼성전자랑 국민성장펀드 알려줘")
    ProductName 필터가 두 개 이상일 수 있는데, 앞쪽 것이 분해 불가라고 바로 포기하면
    (예전에는 여기서 return None) 뒤쪽의 분해 가능한 필터를 놓친다(교차검증 라운드 2에서
    지적됨). 분해 가능한 필터를 하나라도 찾으면 그 즉시 반환한다 — 이 함수 자체가
    "재시도 1회분"을 위한 것이라 여러 개를 동시에 넓히지는 않는다. 어떤 ProductName
    필터도 없거나 전부 분해 불가면 None을 반환한다.

    반환: (원래 상품명, 토큰 목록, 새 filters) 또는 None.
    """
    for i, f in enumerate(filters or []):
        if f.get("concept") != "ProductName" or f.get("op") != "LIKE":
            continue
        values = f.get("values") or []
        if not values:
            continue
        name = values[0]
        tokens = decompose_product_name_tokens(name)
        if tokens == [name]:
            continue  # 이 필터는 분해해도 원래 문구와 동일 — 다음 ProductName 필터를 본다
        new_filters = list(filters)
        new_filters[i] = dict(f, values=tokens)
        return name, tokens, new_filters
    return None


def build_filter_clauses(filters: list, table: str) -> list:
    """resolved.filters 중 이 테이블에 해당하는 것만 골라 WHERE 조각으로 변환."""
    clauses = []
    for f in filters or []:
        # concept 필터는 'table' 키가 있고, 'table'이 없는 필터는 두 종류다:
        # - 채권분류(hasBondCategory)는 fp:Bond 전용 개념이라 tb_domestic_bond를 다룰 때만 적용한다.
        # - 그 외(상품명 ProductName, qp_task14_productname.md §3.2 / 등급 부등호 전개
        #   CreditRating·RiskGrade, qp_task13_comparison.md §6 6)단계)는 "개념만 알고
        #   테이블을 모르는" 필터다. 예전엔 이 경우 f_table을 None으로 둬서
        #   `f_table != table`이 항상 참이 되어 어느 테이블에도 매칭되지 않고 조용히
        #   버려졌다(교차검증에서 지적됨 — 등급 비교 질의가 WHERE절 없이 전량 조회로
        #   샜다). 지금 보고 있는 table로 일단 취급해 넘기고, 이 테이블에 그 개념의
        #   컬럼 매핑이 실제로 있는지는 아래 _resolve_column()이 CONCEPT_COLUMN_MAP으로
        #   판정한다 — 매핑이 없으면(예: CreditRating@tb_fund, RiskGrade@tb_foreign_etf)
        #   col=None이 되어 자연히 건너뛴다.
        concept = f.get("concept")
        if f.get("table"):
            f_table = f["table"]
        elif concept == "BondCategory":
            f_table = "tb_domestic_bond"
        else:
            f_table = table
        if f_table != table:
            continue
        col = _resolve_column(f, table)
        if not col:
            logger.warning(f"[{table}] concept '{concept}'에 대한 컬럼 매핑을 찾지 못해 필터를 건너뜀: {f}")
            continue
        vals = f.get("values") or []
        if not vals:
            continue
        if f.get("op") == "SELLABLE":
            # 판매가능 조건(qp_task16_parser_fixes.md §3.2/§3.4, 버그 E) — 국내채권만
            # 판매여부 컬럼이 없어 buyable_quantity > 0으로 판정하고, 나머지는
            # <컬럼> = <실측 참값>이다(참값 자체가 테이블마다 다르다 — 위 _SELLABLE_TRUE_VALUE).
            # values(["Y"])는 쓰지 않는다 — 실제 SQL 리터럴은 테이블별 실측값을 따로 쓴다.
            if table == "tb_domestic_bond":
                clauses.append(f"{col} > 0")
            else:
                true_value = _SELLABLE_TRUE_VALUE.get(table, _DEFAULT_SELLABLE_TRUE_VALUE)
                clauses.append(f"{col} = {_sql_literal(true_value)}")
        elif f.get("op") == "LIKE":
            # 상품명 텍스트 검색(qp_task14_productname.md §3.2) — 온톨로지가 보증하지 않는
            # 값이라 정확히 일치("=")도 목록 포함("IN")도 아닌 부분 일치로 찾는다.
            if concept == "ProductName":
                # 정식명 OR 약칭(qp_task17_name_matching.md §3) — ETF는 약칭으로 부르는
                # 경우가 압도적이라 정식명(col) 하나만 보면 대부분 실패한다. values가
                # 여러 개면(§2.1 토큰 분해 재검색) 각 값의 OR절을 AND로 묶는다 — 값이
                # 하나면(평소 경로) AND 없이 그 값의 OR절 하나만 남는다(동작 변화 없음).
                name_cols = PRODUCT_NAME_COLUMNS.get(table)
                if not name_cols:
                    # PRODUCT_NAME_COLUMNS는 CONCEPT_COLUMN_MAP과 별도 상수라 두 맵이
                    # 어긋날 수 있다(교차검증 라운드 3에서 지적) — 예를 들어 향후 새
                    # 테이블을 CONCEPT_COLUMN_MAP에만 추가하고 여기를 깜빡하면, 컬럼
                    # 매핑(col)은 있는데도 이 필터를 통째로 건너뛰어 조건 없는 전체
                    # 조회가 나가 버린다. col이 있으면 그 컬럼 하나만으로라도(약칭 OR
                    # 없이) 검색한다 — 완전히 건너뛰는 것보다 훨씬 낫다.
                    if col:
                        name_cols = (col,)
                    else:
                        logger.warning(f"[{table}] ProductName 컬럼 매핑을 찾지 못해 필터를 건너뜀: {f}")
                        continue
                and_parts = []
                for val in vals:
                    pattern = _sql_literal(f"%{val}%")
                    or_clause = " OR ".join(f"{c} LIKE {pattern}" for c in name_cols)
                    and_parts.append(f"({or_clause})")
                clauses.append(" AND ".join(and_parts) if len(and_parts) > 1 else and_parts[0])
            else:
                clauses.append(f"{col} LIKE {_sql_literal(f'%{vals[0]}%')}")
        elif f.get("op") == "IN" or len(vals) > 1:
            clauses.append(f"{col} IN ({', '.join(_sql_literal(v) for v in vals)})")
        else:
            clauses.append(f"{col} = {_sql_literal(vals[0])}")
    return clauses


def _format_candidates(table_results: dict) -> str:
    if not table_results or not table_results.get("columns"):
        return "(없음)"
    lines = []
    for c in table_results["columns"]:
        lines.append(
            f"- {c.get('column_name')} ({c.get('column_korean_name')}): "
            f"{c.get('description', '')} 샘플값: {c.get('sample_values')}"
        )
    return "\n".join(lines)


_column_cache: dict = {}


def _load_table_columns(table: str) -> list:
    if table in _column_cache:
        return _column_cache[table]
    meta_dir = os.path.join(str(SRC_DIR), "data", "db", "meta_columns")
    fp = os.path.join(meta_dir, f"{table}.json")
    cols = []
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        items = list(data.values()) if isinstance(data, dict) else data
        cols = [c.get("column_name") for c in items if c.get("column_name")]
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"{table} 컬럼 목록 로드 실패: {e}")
    _column_cache[table] = cols
    return cols


def _extract_sql(text: str):
    if not text:
        return None
    text = text.strip()
    # 일부 모델은 <thought>...</thought> 안에 추론 과정을 쓰고 그 뒤에 실제 SQL을 붙여서 준다.
    # 추론 문장 안에도 "SELECT"라는 단어가 섞여 나올 수 있어 통째로 제거한다.
    text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    # 설명 문장 속에도 SELECT가 섞여 나올 수 있으니 마지막 SELECT부터를 SQL로 간주한다.
    matches = list(re.finditer(r"SELECT\b", text, re.IGNORECASE))
    if not matches:
        return None
    sql = text[matches[-1].start():].strip()
    if ";" in sql:
        sql = sql.split(";")[0]
    return sql.strip()

def _violates_name_filter_policy(sql: str, table: str, clauses: list) -> bool:
    """[이미 확정된 조건](clauses)에 없는 상품명 컬럼 조건을 LLM이 스스로 추가했는지
    검사한다. 프롬프트 지침만으로는 HCX-005가 이를 무시하는 사례가 확인되어
    (2026-09-06, KODEX AI로봇 ETF 재현) 코드 레벨 가드를 추가한다."""
    name_cols = PRODUCT_NAME_COLUMNS.get(table) or ()
    if not name_cols:
        return False
    confirmed_text = " ".join(clauses)
    for col in name_cols:
        if col in confirmed_text:
            continue  # 이미 확정된 조건에 포함된 컬럼이면 정상
        pattern = re.compile(rf"\b{re.escape(col)}\s*(=|LIKE)", re.IGNORECASE)
        if pattern.search(sql):
            return True
    return False

class TextToSQLAgent(Agent):
    def __init__(self, mm):
        super().__init__(mm)

    def _generate_and_execute(self, original_query: str, table: str, clauses: list, table_results: dict) -> dict:
        """LLM으로 SQL을 생성하고 실행한다(최대 MAX_SQL_RETRIES회, SQL 자체가 깨지거나
        실행 실패할 때만 재시도). query()에서 그대로 옮긴 본문이다 — Step 2(2단 재시도,
        qp_task17_name_matching.md §2.1)가 이 메서드를 필터(따라서 clauses)만 바꿔
        두 번 호출할 수 있도록 분리했다."""
        prompt = PROMPT_TEMPLATE.format(
            query=original_query,
            table=table,
            confirmed_clauses="\n".join(clauses) if clauses else "(없음)",
            candidate_columns=_format_candidates(table_results),
            all_columns=", ".join(_load_table_columns(table)),
        )

        last_error = None
        for attempt in range(1, MAX_SQL_RETRIES + 1):
            cp_prompt = prompt
            if last_error:
                cp_prompt += (
                    f"\n\n[재시도 안내] 이전 SQL 실행 실패: {last_error}\n"
                    "다시 SELECT 문 하나만 출력하세요."
                )
            result = super().query(cp_prompt)
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            sql = _extract_sql(content)

            if not sql:
                last_error = "응답에서 SELECT 문을 추출하지 못했습니다."
                logger.warning(f"[{table}] SQL 추출 실패 ({attempt}/{MAX_SQL_RETRIES}) | raw={content!r}")
                continue

            if _violates_name_filter_policy(sql, table, clauses):
                last_error = (
                    "확정되지 않은 상품명 조건을 임의로 추가했습니다. "
                    "상품명 관련 조건 없이 다시 작성하세요."
                )
                logger.warning(
                    f"[{table}] 상품명 조건 무단 추가 감지, 재시도 "
                    f"({attempt}/{MAX_SQL_RETRIES}) | sql={sql!r}"
                )
                continue

            exec_result = execute_sql_query(sql)
            if exec_result.get("ok"):
                return {
                    "table": table,
                    "sql": sql,
                    "columns": exec_result["columns"],
                    "rows": exec_result["rows"],
                    "row_count": exec_result["row_count"],
                    "attempts": attempt,
                }

            last_error = exec_result.get("error")
            logger.warning(f"[{table}] SQL 실행 실패 ({attempt}/{MAX_SQL_RETRIES}): {last_error} | sql={sql!r}")

        return {
            "table": table,
            "sql": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "error": f"{MAX_SQL_RETRIES}회 시도 후에도 유효한 SQL을 만들지 못함: {last_error}",
        }

    def query(self, original_query: str, table: str, filters: list, table_results: dict):
        clauses = build_filter_clauses(filters, table)
        result = self._generate_and_execute(original_query, table, clauses, table_results)

        # Step 2(qp_task17_name_matching.md §2.1, §0 증상) — 정식 풀네임이 아니면(예:
        # "국민성장펀드") 원본 문구 그대로는 절대 안 걸린다(DB는 회사명+상품유형이 모두
        # 붙은 풀네임이다). SQL 실행 자체는 성공했는데(sql이 있음) row_count가 0일 때만,
        # 상품명 조건에 한해 토큰 분해 AND 매칭으로 딱 한 번 더 시도한다 — "실행 실패"와
        # "0건"은 다르므로 sql이 None(생성 자체를 실패한 경우)이면 재시도하지 않는다.
        # 정확한 이름을 준 질의는 애초에 여기 도달하지 않으므로(row_count > 0) 정밀도가
        # 떨어지지 않는다(§4.4).
        if result.get("sql") is not None and result.get("row_count") == 0:
            broadened = _broaden_name_filters(filters)
            if broadened is not None:
                name, tokens, broadened_filters = broadened
                note = f"정식명 매칭 0건 → 토큰 분해 재검색: {' AND '.join(tokens)}"
                logger.info(f"[{table}] {note}")
                retry_clauses = build_filter_clauses(broadened_filters, table)
                retry_result = self._generate_and_execute(original_query, table, retry_clauses, table_results)
                retry_result["name_match_retry"] = note
                if retry_result.get("row_count"):
                    return retry_result
                # 토큰 분해로도 0건이면 §2.2 — 데이터에 진짜 없는 것으로 본다. 단,
                # 재시도 자체가 SQL 생성/실행에 실패한 경우(retry_result["error"]가 있고
                # sql이 없음)는 "0건"이 아니라 "실행 실패"다(교차검증 라운드 3에서 지적)
                # — 그 경우 "찾지 못했습니다"라고 단정하면 안 되고, 이미 들어있는
                # error 메시지를 그대로 살려 호출부가 실패로 처리하게 둔다.
                if retry_result.get("sql") is not None and not retry_result.get("error"):
                    retry_result["name_not_found"] = (
                        f"'{name}'라는 이름의 상품을 제공된 데이터에서 찾지 못했습니다."
                    )
                return retry_result

        return result