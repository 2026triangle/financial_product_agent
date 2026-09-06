"""
================================================================================
test_entity_category.py — 편입종목명 오분류(항목 A) + 국내 ETF 브랜드 카테고리
오분류(항목 B) 단위 테스트 (Task 18, qp_task18_entity_category.md §4.1)
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는다. 이번 작업은 PROMPT 문구만 바꿨으므로(로직 무변경),
검증할 수 있는 결정론적 코드 경로는 "LLM이 새 PROMPT 지시를 정확히 따랐다고
가정했을 때 그 결과(parsed)가 query()의 나머지 파이프라인을 제대로 통과하는지"
뿐이다 — 실제로 LLM이 캠브리콘/KODEX를 올바르게 분류하는지 자체는 §4.2(사용자가
직접 실행)로 확인해야 한다.

§1 — 항목 A 재현: 편입종목("캠브리콘")이 unsupported_conditions로 오고 나머지
     유효 조건(Region)은 살아있는 가짜 LLM 응답 → 부분 답변(answerable True) +
     Region 필터 존재 + think_trace에 "제공 데이터에 없는 조건" 포함
§2 — think_trace 문구 보존 확인(§3 필수 요구사항) — 팀원 caveat 로직이 의존하는
     두 문구가 코드에 여전히 그대로 있는지 소스 레벨로 확인

실행: cd src/agent && python test_entity_category.py
================================================================================
"""
import inspect
import json
import sys

from agents.query_parser import QueryParser
import agents.query_parser as qp_module


class _FakeModelManager:
    def __init__(self, canned_response: str):
        self.canned_response = canned_response
        self.call_count = 0

    def generate_text(self, *args, **kwargs):
        self.call_count += 1
        return self.canned_response


# --------------------------------------------------------------------------
# §1 — 항목 A: 편입종목 → unsupported_conditions 경로가 살아 있는지 (§4.1 그대로)
# --------------------------------------------------------------------------
def case_holdings_condition_partial_answer():
    """qp_task18_entity_category.md §4.1의 케이스를 그대로 재현한다. LLM이 새
    PROMPT 지시를 따라 "캠브리콘"을 product_name이 아니라 unsupported_conditions로
    보내고, Region 조건("중국")은 정상 트리플로 뽑았다고 가정한 응답이다."""
    canned = json.dumps({
        "category": ["foreign_etf"],
        "logical_verification": ['fp:ForeignETF fp:investsInRegion "중국"'],
        "numerical_verification": {},
        "unsupported_conditions": ["캠브리콘 편입 여부(구성종목 정보)"],
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("캠브리콘이 편입된 중국 반도체 ETF를 알려줘")

    ok = result.get("answerable") is True
    filters = result.get("resolved", {}).get("filters", [])
    ok = ok and any(f.get("concept") == "Region" for f in filters)
    trace = result.get("think_trace", [])
    ok = ok and any("제공 데이터에 없는 조건" in t and "캠브리콘" in t for t in trace)
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_holdings_and_theme_both_unsupported_still_partial():
    """§0 기대 동작 예시(수정된 형태) — 편입종목 조건과 테마 조건이 동시에
    unsupported_conditions에 담겨도, 유효한 Region 조건으로 부분 답변이 유지돼야
    한다(전체를 포기하지 않는다). 다중 카테고리(domestic_etf + foreign_etf)일 때는
    두 카테고리 각각을 지목하는 트리플이 모두 있어야 한다 — 하나만 있으면(교차검증
    라운드 1에서 지적) _build_filters()가 트리플 주어로 카테고리를 스코프하므로,
    트리플이 없는 카테고리는 필터 없이 전체 조회로 샌다. PROMPT의 few-shot 예시를
    이렇게 고쳤으므로 이 테스트도 같은 패턴으로 맞춘다."""
    canned = json.dumps({
        "category": ["domestic_etf", "foreign_etf"],
        "logical_verification": [
            'fp:DomesticETF fp:investsInRegion "중국"',
            'fp:ForeignETF fp:investsInRegion "중국"',
        ],
        "numerical_verification": {},
        "product_name": [],
        "unsupported_conditions": ["캠브리콘 편입 여부(구성종목 정보)", "반도체 테마"],
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("캠브리콘이 편입된 중국 반도체 ETF를 알려줘")

    ok = result.get("answerable") is True
    tables = result.get("resolved", {}).get("tables", {})
    ok = ok and set(tables.keys()) >= {"domestic_etf", "foreign_etf"}
    trace = result.get("think_trace", [])
    ok = ok and any("제공 데이터에 없는 조건" in t for t in trace)

    # 핵심 확인 — 두 카테고리 모두 Region 필터를 실제로 받는지(어느 한쪽만 받고
    # 다른 쪽은 무필터 전체 조회로 새면 안 된다).
    filters = result.get("resolved", {}).get("filters", [])
    region_categories = {f.get("category") for f in filters if f.get("concept") == "Region"}
    ok = ok and region_categories == {"domestic_etf", "foreign_etf"}
    if not ok:
        print(f"  result={result!r}")
    return ok


_CASES = [
    ("§4.1 그대로 — 편입종목 unsupported + Region 필터로 부분 답변", case_holdings_condition_partial_answer),
    ("편입종목+테마 동시 unsupported여도 category/Region은 그대로 유지", case_holdings_and_theme_both_unsupported_still_partial),
]


def run_case_checks() -> bool:
    print("=" * 60)
    print(f"§1 항목 A — 편입종목 unsupported_conditions 부분 답변, {len(_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, fn in _CASES:
        try:
            ok = bool(fn())
        except Exception as e:
            ok = False
            name = f"{name}  [예외: {type(e).__name__}: {e}]"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# §2 — think_trace 문구 보존 확인 (§3 필수) — 소스 레벨로 두 문구가 그대로인지 확인
# --------------------------------------------------------------------------
_REQUIRED_TRACE_PHRASES = [
    "온톨로지가 모르는 속성이라 검증에서 제외:",
    "제공 데이터에 없는 조건:",
]


def run_trace_phrase_checks() -> bool:
    print("=" * 60)
    print("§2 think_trace 문구 보존 확인 (팀원 main.py._extract_skipped_conditions 의존)")
    print("=" * 60)
    source = inspect.getsource(qp_module)
    results = []
    for phrase in _REQUIRED_TRACE_PHRASES:
        ok = phrase in source
        print(f"[{'PASS' if ok else 'FAIL'}] 문구 보존: {phrase!r}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


def main():
    ok1 = run_case_checks()
    ok2 = run_trace_phrase_checks()
    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == "__main__":
    main()
