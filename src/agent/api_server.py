'''
api_server.py
평가용 GET /answer 엔드포인트
'''

import logging
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse


from main import Agent, _filters_for_table, _build_condition_summary, _extract_skipped_conditions

logger = logging.getLogger("agent.api_server")
app = FastAPI()

# 무거운 초기화(BGE-M3, 온톨로지, DB 연결 등)는 서버 기동 시 딱 한 번만
_agent = None

@app.on_event("startup")
def _init_agent():
    global _agent
    logger.info("Agent 초기화 시작...")
    _agent = Agent()
    logger.info("Agent 초기화 완료.")


def _build_context_summary(sql_results: dict) -> str:
    """retrieved_context: 답변 근거로 참조한 테이블 요약"""
    if not sql_results:
        return ""
    parts = []
    for table_name, r in sql_results.items():
        if r.get("error"):
            continue
        parts.append(f"{table_name}({r.get('row_count', 0)}건)")
    return ", ".join(parts)


def _build_think_trace_summary(parse_result: dict, sql_results: dict) -> str:
    """think_trace: 사고/추론/도구 사용 과정 요약"""
    steps = []
    for t in (parse_result.get("think_trace") or []):
        steps.append(t)
    for table_name, r in (sql_results or {}).items():
        if r.get("sql"):
            steps.append(f"[{table_name}] SQL 실행: {r['sql']} → {r.get('row_count', 0)}건")
        elif r.get("error"):
            steps.append(f"[{table_name}] SQL 실행 실패: {r['error']}")

    skipped = _extract_skipped_conditions(parse_result.get("think_trace") or [])
    if skipped:
        steps.append(f"[검증 요약] 다음 조건은 데이터 미비로 검증하지 못함: {', '.join(skipped)}")

    return " → ".join(steps) if steps else "추가 추론 없음"

@app.get("/answer")
def answer(question_id: str = Query(...), question: str = Query(...)):
    try:
        parse_result = _agent.agents["QueryParser"].query(question)

        if not parse_result.get("answerable", False):
            return JSONResponse({
                "question_id": question_id,
                "question": question,
                "retrieved_context": "",
                "think_trace": _build_think_trace_summary(parse_result, {}),
                "answer": f"확인할 수 없습니다. {parse_result.get('content', '')}",
            })

        resolved = parse_result.get("resolved", {}) or {}
        table_results = parse_result.get("table_results", {}) or {}
        tables = resolved.get("tables", {}) or {}
        all_filters = resolved.get("filters", []) or []

        if not tables:
            return JSONResponse({
                "question_id": question_id,
                "question": question,
                "retrieved_context": "",
                "think_trace": _build_think_trace_summary(parse_result, {}),
                "answer": "확인할 수 없습니다. 조회할 상품 카테고리를 특정하지 못했습니다.",
            })

        sql_results = {}
        for category, table_name in tables.items():
            table_filters = _filters_for_table(all_filters, table_name)
            cat_table_results = table_results.get(category, {})
            result = _agent.agents["TextToSQL"].query(
                original_query=question,
                table=table_name,
                filters=table_filters,
                table_results=cat_table_results,
            )
            sql_results[table_name] = result

        curated = _agent._curate_sql_results(sql_results)
        answer_result = _agent.agents["Answer"].query(question, curated)
        final_text = answer_result["content"]

        condition_summary = _build_condition_summary(tables, all_filters)
        final_text += f"\n\n[조회 조건] {condition_summary}"

        return JSONResponse({
            "question_id": question_id,
            "question": question,
            "retrieved_context": _build_context_summary(sql_results),
            "think_trace": _build_think_trace_summary(parse_result, sql_results),
            "answer": final_text,
        })

    except Exception as e:
        logger.exception("answer 처리 중 예외 발생")
        # 스펙상 500 금지 — 무조건 200 + 동일 스키마로 반환
        return JSONResponse({
            "question_id": question_id,
            "question": question,
            "retrieved_context": "",
            "think_trace": f"내부 오류: {e}",
            "answer": "확인할 수 없습니다. 처리 중 오류가 발생했습니다.",
        })