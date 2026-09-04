'''
team triangle main agent loop
v 2.0 - query_parser -> text_to_sql_agent 실루프 연결
'''

# model
from core.model_manager import get_model_manager

# agents
from agents.query_parser import QueryParser
from agents.text_to_sql_agent import TextToSQLAgent

# tools
from core.tool_manager import get_tool_manager
from tools.meta_search_tool import register_meta_search_tool


SHOW_THOUTH_PROCESS = True


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

    if SHOW_THOUTH_PROCESS:
        print("에이전트 목록 :\n{}".format("\n".join(agents.keys())))
    return agents


def _filters_for_table(all_filters: list, table_name: str) -> list:
    """resolved.filters 중 특정 테이블에 해당하는 것만 골라낸다.
    'table' 키가 있으면 그걸로 매칭하고, 없으면(예: hasBondCategory처럼
    카테고리 자체를 이미 결정하는 필터) 모든 테이블에 공통 적용되는
    걸로 간주해 포함시킨다."""
    result = []
    for f in all_filters:
        f_table = f.get("table")
        if f_table is None or f_table == table_name:
            result.append(f)
    return result


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

    def query(self, query):
        massage = []
        massage.append(user_say(query))

        parse_result = self.agents["QueryParser"].query(query)
        if SHOW_THOUTH_PROCESS:
            print("[QueryParser 결과]", parse_result)

        if not parse_result.get("answerable", False):
            return parse_result.get("content")

        resolved = parse_result.get("resolved", {}) or {}
        table_results = parse_result.get("table_results", {}) or {}
        tables = resolved.get("tables", {}) or {}
        all_filters = resolved.get("filters", []) or []

        if not tables:
            return "질의에서 조회할 테이블(카테고리)을 확정하지 못했습니다."

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
            return f"죄송합니다, 조회 중 오류가 발생했습니다: {sql_results[failed_tables[0]]['error']}"

        # TODO(⑥ 답변합성 에이전트 완성되면 아래 한 줄을 그걸로 교체)
        return self._format_results_plain(query, sql_results)

if __name__ == "__main__":
    agent = Agent()
    print(agent.query('신용등급 AAAA인 채권 찾아줘'))