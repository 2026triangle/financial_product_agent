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
from tools.concept_column_map import CONCEPT_COLUMN_MAP
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
- [이미 확정된 조건]에 없는 새 조건을 스스로 추가하고 싶다면, 그 조건이
  명백히 [대상 테이블]의 상품 자체 특성(다른 카테고리 언급이 전혀 섞이지 않은
  표현)에 대한 것일 때만, 그리고 후보 컬럼의 설명/샘플값으로 근거가 확실할
  때만 추가하세요. 조금이라도 애매하면 추가하지 말고 확정된 조건만 사용하세요.
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
        if f.get("op") == "LIKE":
            # 상품명 텍스트 검색(qp_task14_productname.md §3.2) — 온톨로지가 보증하지 않는
            # 값이라 정확히 일치("=")도 목록 포함("IN")도 아닌 부분 일치로 찾는다.
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


class TextToSQLAgent(Agent):
    def __init__(self, mm):
        super().__init__(mm)

    def query(self, original_query: str, table: str, filters: list, table_results: dict):
        clauses = build_filter_clauses(filters, table)
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