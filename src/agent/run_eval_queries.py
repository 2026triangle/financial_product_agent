'''
run_eval_queries.py
Task 15(qp_task15_ratelimit.md §3 Step 2, §4 Step 3) — 평가 질의 배치 실행 스크립트.

용도:
- 대회 공유 평가 질의를 여러 개 연속으로 돌려 429(RateLimitError) 없이 전부 완주하는지,
  문항당 소요 시간이 60초 예산(대회 권장치, 배점 40%에 포함)을 넘지 않는지 확인한다.
- 한 질의가 실패해도(429 재시도 소진, 그 외 예외 등) 다음 질의로 넘어간다 — 배치 전체가
  통째로 멈추면 후반부 문항이 전부 유실된다(§0 증상 재현: 8개 중 2개가 실행조차 못 됨).
- 문항 사이에 딜레이(기본 1.5초)를 둔다 — 평가 서버(주최 측이 호출) 코드에는 넣을 수
  없으므로 이 배치 스크립트에만 적용한다.

이 스크립트는 실제 LLM을 호출한다(문항당 2~3회 — QueryParser + TextToSQL + Answer).
에이전트가 직접 실행하지 않는다 — 사용자가 아래 명령으로 직접 실행할 것.

실행:
    cd financial_product_agent/src/agent
    python run_eval_queries.py
'''
import time

from main import Agent

# §6 완료 조건 — 대회 공유 평가 질의 8개(난이도 태그, 질의문).
QUERIES = [
    ("하-1",   "현재 판매 가능한 원화채권 중 AA- 이상 종목 알려줘"),
    ("중-2",   "국민성장펀드의 구조와 투자전략 동향 등 찾아서 알려줘"),
    ("중-3",   "캠브리콘을 편입한 중국 반도체 ETF 알려줘"),
    ("상-4",   "최근 6개월 우주항공 테마 이력 알려줘"),
    ("상-5",   "에코프로의 자회사를 편입한 ETF 알려줘"),
    ("불가-1", "신용등급 AAAA인 채권 찾아줘"),
    ("불가-2", "Kimi 관련 투자상품 알려줘"),
    ("불가-3", "KODEX AI로봇 ETF 정보 알려줘"),
]

# 문항 간 간격(초) — 평가 서버가 아니라 이 배치 스크립트에만 적용한다(§3 Step 2).
INTER_QUERY_DELAY_SECONDS = 1.5

# 대회 권장 문항당 응답 시간(초, 배점 40%에 포함) — 초과 문항을 눈에 띄게 표시한다(§5 Step 4).
TIME_BUDGET_SECONDS = 60.0


def run(queries=QUERIES, delay_seconds: float = INTER_QUERY_DELAY_SECONDS):
    """질의 목록을 순차 실행하고 (tag, query, elapsed_seconds, error) 결과 리스트를 반환한다.
    한 질의의 예외는 다음 질의 실행을 막지 않는다(§4 Step 3)."""
    agent = Agent()
    results = []

    for i, (tag, query) in enumerate(queries):
        print("=" * 60)
        print(f"[{tag}] {query}")
        start = time.monotonic()
        error = None
        try:
            answer = agent.query(query)
            print(answer)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            print(f"[실패] {error}")
        elapsed = time.monotonic() - start
        over_budget = " ⚠️ 60초 초과" if elapsed > TIME_BUDGET_SECONDS else ""
        print(f"[{tag}] 소요 시간: {elapsed:.2f}초{over_budget}")
        results.append((tag, query, elapsed, error))

        if i < len(queries) - 1:
            time.sleep(delay_seconds)

    print("=" * 60)
    print("요약")
    print("=" * 60)
    for tag, query, elapsed, error in results:
        status = "실패" if error else "완주"
        over_budget = " ⚠️ 60초 초과" if elapsed > TIME_BUDGET_SECONDS else ""
        print(f"[{tag}] {status} — {elapsed:.2f}초{over_budget}" + (f" ({error})" if error else ""))

    failed = [r for r in results if r[3] is not None]
    print(f"\n총 {len(results)}문항 중 {len(results) - len(failed)}문항 완주, {len(failed)}문항 실패")
    return results


if __name__ == "__main__":
    run()
