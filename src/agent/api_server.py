"""
api_server.py — 평가용 GET /answer REST API 서버 (제출 양식 규격 준수)

GET /answer?question_id={id}&question={질의}
응답(JSON, 전부 string): question_id, question, retrieved_context, think_trace, answer

규격 준수 사항:
- 인증 헤더 / POST 바디 사용 안 함 (GET 쿼리 파라미터만)
- question_id/question이 없거나 비어 있어도 422/500 없이 200 OK + 동일 스키마로 응답
  (Query 파라미터를 required로 선언하지 않는 이유: 대회 측이 예상 밖 호출을 하더라도
   422 Unprocessable Entity가 나면 안 되므로, 누락 시 빈 문자열로 받고 answer에서 처리)
- 확인 불가능한 질의도 200 OK + 동일 스키마로 응답 (answerable=False 경로)
- 파이프라인 내부에서 어떤 예외가 나도 절대 500을 내지 않고, 그 사유를 answer에 담아 200 OK로 반환

실행 (개발 확인용):
    uvicorn api_server:app --host 0.0.0.0 --port 80

배포는 systemd로 상시 구동 (README/배포 가이드 참고).
"""
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from main import Agent, _filters_for_table

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent.api_server")

app = FastAPI()

# 무거운 초기화(BGE-M3, 온톨로지, DB 연결 등)는 서버 기동 시 딱 한 번만
# (모듈 임포트 시점이 아니라 startup 이벤트에서 — import만으로 블로킹되지 않도록)
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
    return " → ".join(steps) if steps else "추가 추론 없음"


@app.get("/answer")
def get_answer(question_id: str = "", question: str = ""):
    """
    question_id / question 을 required(Query(...))로 선언하지 않는다:
    누락 시 FastAPI가 422를 내면 스펙 위반(200 OK 보장 불가)이므로
    기본값 ""로 받아서 아래 로직에서 "질문 없음" 케이스로 처리한다.
    """
    try:
        if _agent is None:
            return JSONResponse({
                "question_id": question_id,
                "question": question,
                "retrieved_context": "",
                "think_trace": "",
                "answer": "서버 초기화 중입니다. 잠시 후 다시 시도해 주세요.",
            })

        if not question:
            return JSONResponse({
                "question_id": question_id,
                "question": question,
                "retrieved_context": "",
                "think_trace": "",
                "answer": "확인할 수 없습니다. 질문이 비어 있습니다.",
            })

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

        return JSONResponse({
            "question_id": question_id,
            "question": question,
            "retrieved_context": _build_context_summary(sql_results),
            "think_trace": _build_think_trace_summary(parse_result, sql_results),
            "answer": final_text,
        })

    except Exception as e:
        logger.exception("[/answer] 처리 중 예외 발생")
        # 스펙상 500 금지 — 무조건 200 + 동일 스키마로 반환
        return JSONResponse(status_code=200, content={
            "question_id": question_id,
            "question": question,
            "retrieved_context": "",
            "think_trace": f"내부 오류: {e}",
            "answer": "확인할 수 없습니다. 처리 중 오류가 발생했습니다.",
        })


@app.get("/health")
def health():
    """참고용 자체 헬스체크 (대회 규격에는 없음, 우리 쪽 배포 확인용)."""
    return {"status": "ok", "agent_ready": _agent is not None}
