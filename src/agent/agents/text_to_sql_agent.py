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
- 확정된 조건 외에 질의에 남아있는 모호한 표현(예: "저렴한", "수익률 좋은")은
  후보 컬럼의 설명/샘플값을 근거로 합리적인 조건을 스스로 판단해서 추가하세요.
  근거가 부족하면 무리하게 조건을 추가하지 말고 SELECT 대상 컬럼만으로 반환하세요.
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
        # concept 필터는 'table' 키가 있고, 채권분류(hasBondCategory) 필터는 'table'이 없다 —
        # 후자는 fp:Bond 전용 개념이라 tb_domestic_bond를 다룰 때만 적용한다.
        f_table = f.get("table") or ("tb_domestic_bond" if f.get("concept") == "BondCategory" else None)
        if f_table != table:
            continue
        col = _resolve_column(f, table)
        if not col:
            logger.warning(f"[{table}] concept '{f.get('concept')}'에 대한 컬럼 매핑을 찾지 못해 필터를 건너뜀: {f}")
            continue
        vals = f.get("values") or []
        if not vals:
            continue
        if f.get("op") == "IN" or len(vals) > 1:
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