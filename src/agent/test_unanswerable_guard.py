"""
================================================================================
test_unanswerable_guard.py — 답변 불가 판정 구멍 막기 단위 테스트
(Task 12, qp_task12_unanswerable.md §7.1/§7.2)
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는다. §7.2는 실제 LLM 대신 _FakeModelManager(test_filter_resolution.py
와 동일한 패턴)로 QueryParser.query()를 결정론적으로 끝까지 돌린다.

구멍 A — LLM이 category/logical_verification을 아예 못 뽑으면 검증이 통째로
        건너뛰어져 답할 재료가 없는데도 answerable: True로 새는 문제.
구멍 B — not_in_table(개념 자체가 없음, 예: 위험등급@해외ETF)이 카테고리를 제외하지
        않아, 조건만 증발하고 해당 상품군 전체가 무필터로 후보에 남는 문제.

실행: cd src/agent && python test_unanswerable_guard.py
================================================================================
"""
import json
import sys

from agents.query_parser import QueryParser
from test_filter_resolution import _FakeModelManager  # 가짜 LLM 재사용 (qp_task12 §7.2 지시)


def _qp():
    # _build_filters는 self.mm을 안 쓰므로 mm=None으로 충분하다.
    return QueryParser(None)


# --------------------------------------------------------------------------
# §7.1 — _build_filters(): not_in_table도 카테고리를 제외하는지
# --------------------------------------------------------------------------
def case_not_in_table_excludes_single_category():
    qp = _qp()
    triples = ['fp:ForeignETF fp:hasRiskGrade "1등급 매우높은위험"']
    tables = {"foreign_etf": "tb_foreign_etf"}
    filters, excluded = qp._build_filters(triples, tables, [])
    ok = filters == [] and excluded == {"foreign_etf"}
    if not ok:
        print(f"  filters={filters!r} excluded={excluded!r}")
    return ok


def case_not_in_table_excludes_asset_class_on_bond():
    qp = _qp()
    triples = ['fp:Bond fp:investsInAssetClass "주식"']
    tables = {"domestic_bond": "tb_domestic_bond"}
    filters, excluded = qp._build_filters(triples, tables, [])
    ok = filters == [] and excluded == {"domestic_bond"}
    if not ok:
        print(f"  filters={filters!r} excluded={excluded!r}")
    return ok


def case_composite_keeps_only_applicable_category():
    """복합 질의 — domestic_etf는 위험등급이 있고 foreign_etf는 없다.
    domestic_etf만 필터가 남고 foreign_etf는 제외되어야 한다."""
    qp = _qp()
    triples = [
        'fp:DomesticETF fp:hasRiskGrade "5등급 낮은위험"',
        'fp:ForeignETF fp:hasRiskGrade "5등급 낮은위험"',
    ]
    tables = {"domestic_etf": "tb_domestic_etf", "foreign_etf": "tb_foreign_etf"}
    filters, excluded = qp._build_filters(triples, tables, [])
    ok = len(filters) == 1 and filters[0].get("category") == "domestic_etf"
    ok = ok and excluded == {"foreign_etf"}
    if not ok:
        print(f"  filters={filters!r} excluded={excluded!r}")
    return ok


def case_fallback_still_does_not_exclude():
    """회귀 — 폴백 경로(주어가 4개 클래스에 없음)는 not_in_table이어도 여전히
    제외하지 않는다. 어느 카테고리를 겨냥한 조건인지 알 수 없기 때문이다."""
    qp = _qp()
    triples = ['fp:Product fp:hasRiskGrade "1등급 매우높은위험"']
    tables = {"domestic_bond": "tb_domestic_bond", "foreign_etf": "tb_foreign_etf"}
    filters, excluded = qp._build_filters(triples, tables, [])
    ok = len(filters) == 1 and filters[0].get("category") == "domestic_bond"
    ok = ok and excluded == set()
    if not ok:
        print(f"  filters={filters!r} excluded={excluded!r}")
    return ok


_BUILD_FILTERS_CASES = [
    ("not_in_table이 단일 카테고리를 제외함 (위험등급@해외ETF)", case_not_in_table_excludes_single_category),
    ("not_in_table이 단일 카테고리를 제외함 (자산군@채권)", case_not_in_table_excludes_asset_class_on_bond),
    ("복합 질의: 적용 가능한 카테고리만 남음", case_composite_keeps_only_applicable_category),
    ("회귀: 폴백 경로는 여전히 제외 안 함", case_fallback_still_does_not_exclude),
]


def run_build_filters_checks() -> bool:
    print("=" * 60)
    print(f"§7.1 _build_filters() — not_in_table 카테고리 제외, {len(_BUILD_FILTERS_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, fn in _BUILD_FILTERS_CASES:
        try:
            ok = bool(fn())
        except Exception as e:
            ok = False
            name = f"{name}  [예외: {type(e).__name__}: {e}]"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


# --------------------------------------------------------------------------
# §7.2 — 빈 카테고리 가드 (가짜 LLM으로 query() 전체를 결정론적으로 검증)
# --------------------------------------------------------------------------
def case_hole_a_empty_everything():
    """구멍 A — LLM이 category/logical_verification을 아예 못 뽑음."""
    canned = json.dumps({
        "category": [], "logical_verification": [], "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("외계채권ㅋㅋ 찾아줘")

    ok = result.get("answerable") is False
    ok = ok and "parsed" not in result and "resolved" not in result and "table_results" not in result
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_hole_b_only_category_excluded():
    """구멍 B — 유일한 카테고리가 not_in_table로 제외되어 tables가 빈다."""
    canned = json.dumps({
        "category": ["foreign_etf"],
        "logical_verification": ['fp:ForeignETF fp:hasRiskGrade "1등급 매우높은위험"'],
        "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("위험등급 1등급인 해외ETF 찾아줘")

    ok = result.get("answerable") is False
    ok = ok and "parsed" not in result and "resolved" not in result and "table_results" not in result
    ok = ok and bool(result.get("reason")) and bool(result.get("suggestion"))
    if not ok:
        print(f"  result={result!r}")
    return ok


_GUARD_CASES = [
    ("구멍 A: category/트리플 전부 빈 배열 → 컷", case_hole_a_empty_everything),
    ("구멍 B: 유일한 카테고리가 제외되어 tables 빔 → 컷", case_hole_b_only_category_excluded),
]


def run_guard_checks() -> bool:
    print("=" * 60)
    print(f"§7.2 빈 카테고리 가드 (가짜 LLM) — {len(_GUARD_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, fn in _GUARD_CASES:
        try:
            ok = bool(fn())
        except Exception as e:
            ok = False
            name = f"{name}  [예외: {type(e).__name__}: {e}]"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


def main():
    ok1 = run_build_filters_checks()
    ok2 = run_guard_checks()
    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == "__main__":
    main()
