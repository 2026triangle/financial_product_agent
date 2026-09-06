"""
================================================================================
test_regression_guard.py — 등급 비교 회귀(버그 G) + 미지원조건 단독 무필터 전체조회
(버그 H) 단위 테스트 (Task 19, qp_task19_regression.md §4.1)
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는다. 이번 작업은 PROMPT example 1개 교체 + query() 가드
1개 추가이므로, 가짜 LLM으로 query() 전체를 돌려 확인한다(§4.1 표 그대로).

§4.1 표 4케이스:
  H-1) 미지원 조건만 있고 필터 0개 → 컷 (에코프로) — parsed/resolved/table_results 키 없음
  H-2) 미지원 조건 있으나 유효 필터도 있음 → 진행 (캠브리콘)
  H-3) 미지원 조건 없고 필터 0개 → 진행 (정상 목록 조회, 회귀 방지)
  G)   부등호 경로가 여전히 동작 (AA- 이상 → CreditRating IN [...])

실행: cd src/agent && python test_regression_guard.py
================================================================================
"""
import json
import sys

from agents.query_parser import QueryParser


class _FakeModelManager:
    def __init__(self, canned_response: str):
        self.canned_response = canned_response
        self.call_count = 0

    def generate_text(self, *args, **kwargs):
        self.call_count += 1
        return self.canned_response


# --------------------------------------------------------------------------
# H-1 — 미지원 조건만 있고 필터 0개 → 컷 (에코프로 재현)
# --------------------------------------------------------------------------
def case_h_unsupported_only_cuts():
    canned = json.dumps({
        "category": ["domestic_etf", "foreign_etf"],
        "logical_verification": [],
        "numerical_verification": {},
        "unsupported_conditions": ["에코프로의 자회사"],
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("에코프로의 자회사를 편입한 ETF 알려줘")

    ok = result.get("answerable") is False
    # 컷 반환 경로 규칙(§2) — parsed/resolved/table_results 키가 없어야 한다.
    ok = ok and "parsed" not in result
    ok = ok and "resolved" not in result
    ok = ok and "table_results" not in result
    trace = result.get("think_trace", [])
    ok = ok and any("유효한 조회 조건이 없어" in t for t in trace)
    if not ok:
        print(f"  result={result!r}")
    return ok


# --------------------------------------------------------------------------
# H-2 — 미지원 조건 있으나 유효 필터도 있음 → 진행 (캠브리콘 재현)
# --------------------------------------------------------------------------
def case_h_unsupported_with_valid_filter_proceeds():
    canned = json.dumps({
        "category": ["foreign_etf"],
        "logical_verification": ['fp:ForeignETF fp:investsInRegion "중국"'],
        "numerical_verification": {},
        "unsupported_conditions": ["캠브리콘 편입 여부"],
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("캠브리콘이 편입된 중국 ETF 알려줘")

    ok = result.get("answerable") is True
    filters = result.get("resolved", {}).get("filters", [])
    ok = ok and any(f.get("concept") == "Region" for f in filters)
    if not ok:
        print(f"  result={result!r}")
    return ok


# --------------------------------------------------------------------------
# H-3 — 미지원 조건 없고 필터 0개 → 진행 (정상 목록 조회, 회귀 방지)
# --------------------------------------------------------------------------
def case_h_no_unsupported_no_filter_proceeds():
    canned = json.dumps({
        "category": ["domestic_etf"],
        "logical_verification": [],
        "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("국내ETF 알려줘")

    ok = result.get("answerable") is True
    if not ok:
        print(f"  result={result!r}")
    return ok


# --------------------------------------------------------------------------
# G — 부등호 경로가 여전히 동작
# --------------------------------------------------------------------------
def case_g_rank_comparison_still_works():
    canned = json.dumps({
        "category": ["domestic_bond"],
        "logical_verification": ['fp:Bond fp:isSellable "true"'],
        "numerical_verification": {"credit_rating": ">=AA-"},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("현재 판매 가능한 원화채권 중 AA- 이상 종목 알려줘")

    filters = result.get("resolved", {}).get("filters", [])
    hit = next((f for f in filters if f.get("concept") == "CreditRating"), None)
    ok = result.get("answerable") is True
    ok = ok and hit is not None
    ok = ok and hit.get("op") == "IN"
    ok = ok and hit.get("values") == ["AAA", "AA+", "AA0", "AA-"]
    if not ok:
        print(f"  result={result!r}")
    return ok


# --------------------------------------------------------------------------
# 추가 — 버그 H 가드가 unsupported + numerical(부등호)만 있는 조합에서 잘못
# 컷하지 않는지(6단계 부등호 전개 *이후*에 가드를 둔 이유를 직접 검증).
# --------------------------------------------------------------------------
def case_h_guard_after_rank_expansion_not_premature():
    """logical_verification 트리플이 하나도 없고 numerical_verification(부등호)과
    unsupported_conditions만 있는 질의(예: "반도체 테마이면서 AA- 이상인 채권")도
    부등호 전개가 CreditRating 필터를 만들어주면 컷되면 안 된다 — 가드를 5.5(부등호
    전개 이전)에 두면 이 시점엔 filters가 아직 비어 있어 잘못 컷된다."""
    canned = json.dumps({
        "category": ["domestic_bond"],
        "logical_verification": [],
        "numerical_verification": {"credit_rating": ">=AA-"},
        "unsupported_conditions": ["반도체 테마"],
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("반도체 테마이면서 AA- 이상인 채권 알려줘")

    ok = result.get("answerable") is True
    filters = result.get("resolved", {}).get("filters", [])
    ok = ok and any(f.get("concept") == "CreditRating" for f in filters)
    if not ok:
        print(f"  result={result!r}")
    return ok


_CASES = [
    ("H-1: 미지원 조건만 있고 필터 0개 → 컷 (에코프로)", case_h_unsupported_only_cuts),
    ("H-2: 미지원 조건 + 유효 필터 있음 → 진행 (캠브리콘)", case_h_unsupported_with_valid_filter_proceeds),
    ("H-3: 미지원 조건 없고 필터 0개 → 진행 (정상 목록 조회)", case_h_no_unsupported_no_filter_proceeds),
    ("G: 부등호 경로 여전히 동작 (AA- 이상 → CreditRating IN)", case_g_rank_comparison_still_works),
    ("추가: unsupported+부등호뿐이어도 부등호 전개 후엔 진행", case_h_guard_after_rank_expansion_not_premature),
]


def run_checks() -> bool:
    print("=" * 60)
    print(f"§4.1 버그 G/H 가짜 LLM 단위 테스트 — {len(_CASES)}개 케이스")
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


def main():
    sys.exit(0 if run_checks() else 1)


if __name__ == "__main__":
    main()
