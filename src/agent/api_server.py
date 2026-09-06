'''
api_server.py
평가용 GET /answer 엔드포인트
'''

import logging
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse


from main import Agent, _build_condition_summary, _extract_skipped_conditions
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
    # 수정
    skipped = _extract_skipped_conditions(parse_result)
    if skipped:
        steps.append(f"[검증 요약] 다음 조건은 데이터 미비로 검증하지 못함: {', '.join(skipped)}")
        
    return " → ".join(steps) if steps else "추가 추론 없음"

@app.get("/answer")
def answer(question_id: str = Query(...), question: str = Query(...)):
    try:
        result = _agent._run_pipeline(question)
        parse_result = result["parse_result"]
        sql_results = result["sql_results"]

        if result["content"] is None:
            if result["kind"] == "unanswerable":
                answer_text = f"확인할 수 없습니다. {result['early_return']}"
            elif result["kind"] == "no_tables":
                answer_text = "확인할 수 없습니다. 조회할 상품 카테고리를 특정하지 못했습니다."
            else:  # sql_failed
                answer_text = f"확인할 수 없습니다. {result['early_return']}"

            return JSONResponse({
                "question_id": question_id,
                "question": question,
                "retrieved_context": _build_context_summary(sql_results),
                "think_trace": _build_think_trace_summary(parse_result, sql_results),
                "answer": answer_text,
            })

        return JSONResponse({
            "question_id": question_id,
            "question": question,
            "retrieved_context": _build_context_summary(sql_results),
            "think_trace": _build_think_trace_summary(parse_result, sql_results),
            "answer": result["content"],
        })

    except Exception as e:
        logger.exception("answer 처리 중 예외 발생")
        return JSONResponse({
            "question_id": question_id,
            "question": question,
            "retrieved_context": "",
            "think_trace": f"내부 오류: {e}",
            "answer": "확인할 수 없습니다. 처리 중 오류가 발생했습니다.",
        })