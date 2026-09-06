"""
tools/sql_guard_tool.py

③ Text-to-SQL 에이전트가 생성한 SQL을 안전하게 실행하는 가드레일 도구.
⑥ 단계: SELECT-only 검증 + 읽기 전용 커넥션 + 결과 행 수 제한.
"""

import logging
import re
import sqlite3
from typing import Any, Dict, Optional

import os
from config import config, SRC_DIR
from core.tool_manager import Tool, ToolManager, get_tool_manager, generate_function_schema

logger = logging.getLogger("agent.tools.sql_guard_tool")

MAX_ROWS = 200

_SELECT_ONLY_RE = re.compile(r"^\s*(WITH\s+.+?\s+)?SELECT\s", re.IGNORECASE | re.DOTALL)
_FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX)\b",
    re.IGNORECASE,
)

_MULTI_STATEMENT_RE = re.compile(r";\s*\S")
_COMMENT_RE = re.compile(r"--|/\*")
_TABLE_REF_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)

ALLOWED_TABLES = {
    "tb_domestic_bond",
    "tb_domestic_etf",
    "tb_foreign_etf",
    "tb_fund",
    "tb_fund_attribute",
}


class SQLGuardError(Exception):
    """검증 실패 메시지 — Text-to-SQL 에이전트가 재시도 프롬프트에 그대로 넣을 문자열"""


def _validate_sql(sql: str) -> str:
    sql = sql.strip().rstrip(";")

    if not _SELECT_ONLY_RE.match(sql):
        raise SQLGuardError("SELECT 문만 허용됩니다.")
    if _FORBIDDEN_RE.search(sql):
        raise SQLGuardError("데이터 변경/스키마 조작 키워드가 포함되어 실행이 거부되었습니다.")
    if _MULTI_STATEMENT_RE.search(sql):
        raise SQLGuardError("한 번에 하나의 SELECT 문만 실행할 수 있습니다.")

    if _COMMENT_RE.search(sql):
        raise SQLGuardError("SQL 주석(--, /* */)은 허용되지 않습니다.")

    referenced_tables = {m.group(1).lower() for m in _TABLE_REF_RE.finditer(sql)}
    if not referenced_tables:
        raise SQLGuardError("조회 대상 테이블을 확인할 수 없습니다.")
    disallowed = referenced_tables - ALLOWED_TABLES
    if disallowed:
        raise SQLGuardError(f"허용되지 않은 테이블 참조: {', '.join(disallowed)}")

    if not re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
        sql = f"{sql} LIMIT {MAX_ROWS}"
    else:
        sql = re.sub(
            r"LIMIT\s+(\d+)",
            lambda m: f"LIMIT {min(int(m.group(1)), MAX_ROWS)}",
            sql,
            flags=re.IGNORECASE,
        )
    return sql


def execute_sql_query(sql: str) -> Dict[str, Any]:
    """
    생성된 SQL(SELECT 문)을 검증 후 읽기 전용으로 실행합니다.

    :param sql: 실행할 SQL SELECT 문 (한 개의 statement만 허용)
    :return: 성공 시 {"ok": True, "columns": [...], "rows": [[...],...], "row_count": N}
             실패 시 {"ok": False, "error": "..."}
    """
    try:
        safe_sql = _validate_sql(sql)
    except SQLGuardError as e:
        logger.warning(f"SQL 가드레일 거부: {e} | sql={sql!r}")
        return {"ok": False, "error": str(e)}

    db_path = config.db.db_path
    if not os.path.isabs(db_path):
        db_path = os.path.join(str(SRC_DIR), db_path)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only = ON;")
        try:
            cur = conn.cursor()
            cur.execute(safe_sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(f"SQL 실행 실패: {e} | sql={safe_sql!r}")
        return {"ok": False, "error": f"SQL 실행 오류: {e}"}

    return {"ok": True, "columns": columns, "rows": rows, "row_count": len(rows)}


def register_sql_guard_tool(tool_manager: Optional[ToolManager] = None) -> Tool:
    """execute_sql_query를 ToolManager에 등록합니다."""
    tm = tool_manager or get_tool_manager()

    schema = generate_function_schema(
        func=execute_sql_query,
        name="execute_sql_query",
        description=execute_sql_query.__doc__,
    )

    guard_tool = Tool(
        name="execute_sql_query",
        func=execute_sql_query,
        description=execute_sql_query.__doc__,
        schema=schema,
    )

    tm.register(guard_tool)
    logger.info("✅ 'execute_sql_query' 도구가 ToolManager에 성공적으로 등록되었습니다.")
    return guard_tool


if __name__ == "__main__":
    print(execute_sql_query("SELECT pd_no, pd_nm FROM tb_domestic_bond LIMIT 5"))
    print(execute_sql_query("DELETE FROM tb_domestic_bond"))
    print(execute_sql_query("SELECT * FROM tb_domestic_bond; DROP TABLE tb_domestic_bond;"))
    print(execute_sql_query("SELECT pd_no FROM tb_domestic_bond"))
    print(execute_sql_query("SELECT * FROM tb_domestic_bond -- DROP TABLE tb_domestic_bond"))
    print(execute_sql_query("SELECT * FROM sqlite_master"))
    print(execute_sql_query("SELECT a.pd_no FROM tb_domestic_bond a JOIN tb_fund_attribute b ON a.pd_no=b.itm_no LIMIT 5"))