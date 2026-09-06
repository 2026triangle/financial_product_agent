'''
team triangle main agent loop
v 2.1 - 답변 정제(컬럼/행 축소) 추가
'''

import re

# model
from core.model_manager import get_model_manager
from agents.answer_agent import AnswerAgent

# agents
from agents.query_parser import QueryParser
from agents.text_to_sql_agent import TextToSQLAgent

# tools
from core.tool_manager import get_tool_manager
from tools.meta_search_tool import register_meta_search_tool

_CATEGORY_KOREAN = {
    "tb_domestic_bond": "국내채권",
    "tb_domestic_etf": "국내ETF",
    "tb_foreign_etf": "해외ETF",
    "tb_fund": "공모펀드",
}

def _build_condition_summary(tables: dict, all_filters: list) -> str:
    """실제 적용된 조회 조건을 LLM 없이 코드로 그대로 요약한다.
    AnswerAgent가 조건을 스스로 서술하다 지어내는 문제(카테고리 오기재,
    존재하지 않는 회사명/날짜 등)를 막기 위해 조건 설명 자체를
    결정론적으로 생성한다."""
    parts = []
    for category, table_name in tables.items():
        table_filters = _filters_for_table(all_filters, table_name)
        label = _CATEGORY_KOREAN.get(table_name, table_name)
        if table_filters:
            cond_desc = ", ".join(
                f"{f.get('concept') or f.get('column')}={f.get('values')}"
                for f in table_filters
            )
            parts.append(f"{label}({cond_desc})")
        else:
            parts.append(f"{label}(추가 조건 없음)")
    return ", ".join(parts)


SHOW_THOUTH_PROCESS = True
def _extract_skipped_conditions(parse_result: dict) -> list:
    """QueryParser가 구조화해서 내려주는 unsupported_conditions 필드를 그대로 읽는다."""
    conditions = (
        parse_result.get("unsupported_conditions")
        or (parse_result.get("parsed") or {}).get("unsupported_conditions")
        or []
    )
    return list(conditions)

def user_say(this):
    return {'user': this}

def agent_say(this):
    return {'agent': this}

def serious_error(msg):
    print("="*10, "ERROR!!", "*"*10)
    print(msg)
    print("="*27)
    exit(msg)

def get_agents(mm):
    agents = {}
    if "QueryParser" in globals():
        agents["QueryParser"] = QueryParser(mm)
    else:
        serious_error('get_agent : 필수 에이전트 QueryParser 로드에 실패')

    if "TextToSQLAgent" in globals():
        agents["TextToSQL"] = TextToSQLAgent(mm)
    else:
        serious_error('get_agent : 필수 에이전트 TextToSQLAgent 로드에 실패')

    if "AnswerAgent" in globals():
        agents["Answer"] = AnswerAgent(mm)
    else:
        serious_error('get_agent : 필수 에이전트 AnswerAgent 로드에 실패')

    if SHOW_THOUTH_PROCESS:
        print("에이전트 목록 :\n{}".format("\n".join(agents.keys())))
    return agents

def _filters_for_table(all_filters: list, table_name: str) -> list:
    """resolved.filters 중 특정 테이블에 해당하는 것만 골라낸다."""
    result = []
    for f in all_filters:
        f_table = f.get("table")
        if f_table is None or f_table == table_name:
            result.append(f)
    return result


def _guess_identity_columns(columns):
    """컬럼명 패턴으로 상품코드/상품명 컬럼을 추정 (테이블마다 하드코딩 불필요)."""
    id_cols = []
    for c in columns:
        cl = c.lower()
        if cl.endswith("no") or cl == "itm_no":
            id_cols.append(c)
            break
    for c in columns:
        cl = c.lower()
        if cl.endswith("nm") and c not in id_cols:
            id_cols.append(c)
            break
    return id_cols


def _columns_used_in_sql(sql, columns):
    """SELECT * 라서, SQL 텍스트(주로 WHERE절)에 실제 언급된 컬럼만 추출."""
    if not sql:
        return []
    used = []
    for c in columns:
        if re.search(rf"\b{re.escape(c)}\b", sql):
            used.append(c)
    return used


class Agent:
    def __init__(self):
        self.mm = get_model_manager()
        self.tm = get_tool_manager()
        register_meta_search_tool(self.tm)

        self.agents = get_agents(self.mm)

    def _format_results_plain(self, query, sql_results):
        """⑥ 답변합성 에이전트가 붙기 전까지 쓰는 임시 포맷터 (LLM 없이 규칙 기반)"""
        lines = [f"'{query}'에 대한 조회 결과입니다."]
        for table_name, r in sql_results.items():
            if r.get("error"):
                lines.append(f"- {table_name}: 조회 실패 ({r['error']})")
                continue
            count = r.get("row_count", 0)
            lines.append(f"- {table_name}: {count}건 조회됨")
            cols = r.get("columns", [])
            for row in r.get("rows", [])[:3]:
                preview = dict(zip(cols, row))
                lines.append(f"    · {preview}")
        return "\n".join(lines)

    def _curate_sql_results(self, sql_results, max_rows=5):
        """답변 생성 전, 관련 컬럼만 추리고 행 수를 제한한다."""
        curated = {}
        for table_name, r in sql_results.items():
            if r.get("error") or not r.get("rows"):
                curated[table_name] = r
                continue
            columns = r.get("columns", [])
            keep = list(dict.fromkeys(
                _guess_identity_columns(columns) + _columns_used_in_sql(r.get("sql"), columns)
            ))
            if not keep:
                keep = columns[:6]

            idxs = [columns.index(c) for c in keep]
            curated_rows = [tuple(row[i] for i in idxs) for row in r["rows"][:max_rows]]

            curated[table_name] = {
                **r,
                "columns": keep,
                "rows": curated_rows,
                "curated_row_count": len(curated_rows),
            }
        return curated

    def _run_pipeline(self, query):
        """QueryParser → TextToSQL → Answer 전체 파이프라인 실행.
        main.query()와 api_server.answer()가 동일 로직을 중복 구현하지 않도록
        이 메서드 하나로 통일한다 (dev_docs.md: "모든 쿼리는 main.agent.query()로 받는다")."""
        parse_result = self.agents["QueryParser"].query(query)
        if SHOW_THOUTH_PROCESS:
            print("[QueryParser 결과]", parse_result)

        if not parse_result.get("answerable", False):
            return {
                "content": None,
                "kind": "unanswerable",
                "early_return": parse_result.get("content"),
                "parse_result": parse_result,
                "sql_results": {},
                "tables": {},
                "all_filters": [],
            }

        resolved = parse_result.get("resolved", {}) or {}
        table_results = parse_result.get("table_results", {}) or {}
        tables = resolved.get("tables", {}) or {}
        all_filters = resolved.get("filters", []) or []

        if not tables:
            return {
                "content": None,
                "kind": "no_tables",
                "early_return": "질의에서 조회할 테이블(카테고리)을 확정하지 못했습니다.",
                "parse_result": parse_result,
                "sql_results": {},
                "tables": tables,
                "all_filters": all_filters,
            }

        sql_results = {}
        for category, table_name in tables.items():
            table_filters = _filters_for_table(all_filters, table_name)
            cat_table_results = table_results.get(category, {})
            if SHOW_THOUTH_PROCESS:
                print(f"[TextToSQL:{table_name}] filters={table_filters}")
            result = self.agents["TextToSQL"].query(
                original_query=query,
                table=table_name,
                filters=table_filters,
                table_results=cat_table_results,
            )
            sql_results[table_name] = result
            if SHOW_THOUTH_PROCESS:
                print(f"[TextToSQL:{table_name}] sql={result.get('sql')} "
                      f"row_count={result.get('row_count')} error={result.get('error')}")

        failed_tables = [t for t, r in sql_results.items() if r.get("error")]
        if failed_tables and len(failed_tables) == len(sql_results):
            return {
                "content": None,
                "kind": "sql_failed",
                "early_return": "확인할 수 없습니다. 질의 조건에 해당하는 상품을 정확히 특정할 수 없습니다.",
                "parse_result": parse_result,
                "sql_results": sql_results,
                "tables": tables,
                "all_filters": all_filters,
            }

        curated = self._curate_sql_results(sql_results)
        skipped = _extract_skipped_conditions(parse_result)
        answer_result = self.agents["Answer"].query(query, curated, skipped=skipped)
        content = answer_result["content"]

        condition_summary = _build_condition_summary(tables, all_filters)
        content += f"\n\n[조회 조건] {condition_summary}"

        if skipped:
            content += (
                "\n\n[참고] 다음 조건은 보유한 데이터로 검증하지 못했습니다: "
                + ", ".join(skipped)
                + ". 위 결과는 이 조건이 반영되지 않았을 수 있으니 참고용으로만 확인해 주세요."
            )

        return {
            "content": content,
            "kind": None,
            "early_return": None,
            "parse_result": parse_result,
            "sql_results": sql_results,
            "tables": tables,
            "all_filters": all_filters,
        }

    def query(self, query):
        """CLI/단발성 호출용 — 파이프라인 실행 후 최종 답변 문자열만 리턴."""
        result = self._run_pipeline(query)
        return result["content"] if result["content"] is not None else result["early_return"]

if __name__ == "__main__":
    # Step 3(qp_task15_ratelimit.md §4) — CLI 경로 예외 방어. 429 백오프가 다 소진되면
    # model_manager가 예외를 던지는데(§2), 여기서 안 잡으면 트레이스백만 남기고 죽는다.
    # 여러 질의를 연속 실행하는 배치 시나리오는 run_eval_queries.py 참고.
    agent = Agent()
    try:
        print(agent.query('신용등급 AAAA인 채권 찾아줘'))
    except Exception as e:
        print(f"[실패] 질의 처리 중 예외 발생: {type(e).__name__}: {e}")