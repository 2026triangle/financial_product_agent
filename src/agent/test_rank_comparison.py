"""
================================================================================
test_rank_comparison.py — 등급 비교(이상/이하) 전개 + 테마·기업명 오분류 수정 단위 테스트
(Task 13, qp_task13_comparison.md §5.1/§5.2/§5.3)
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는다(§5.3은 가짜 mm으로 QueryParser.query()를 끝까지 돌리지만
실제 LLM 호출은 없다). 이 파일은 버그 A(부등호 표현이 조용히 무시됨) 수정과
버그 C(테마·기업관계가 AssetClass로 오분류됨) 수정 중 unsupported_conditions 처리를
검증한다.

§5.1 — _parse_rank_comparison() 단위 테스트: 기호/한국어 표현 파싱, 해석 불가 시 None.
§5.2 — OntologyValidator.expand_rank_filter() 회귀 (Task 11에서 이미 검증됨, 여기서 다시 확인).
§5.3 — QueryParser.query() 전체를 가짜 LLM으로 돌려 numerical_verification의 부등호
       표현이 실제로 resolved['filters']에 전개되는지, unsupported_conditions가
       think_trace/suggestion에 반영되는지 확인한다.

실행: cd src/agent && python test_rank_comparison.py
================================================================================
"""
import json
import sys

from tools.ontology_tool import get_ontology_validator
from agents.query_parser import QueryParser, _parse_rank_comparison


class _FakeModelManager:
    """test_filter_resolution.py와 동일한 가짜 mm — QueryParser.query()를 실제 LLM
    없이 끝까지 결정론적으로 돌린다."""

    def __init__(self, canned_response: str):
        self.canned_response = canned_response
        self.call_count = 0

    def generate_text(self, *args, **kwargs):
        self.call_count += 1
        return self.canned_response


# --------------------------------------------------------------------------
# §5.1 — _parse_rank_comparison() 단위 테스트
# --------------------------------------------------------------------------
_PARSE_RANK_CASES = [
    (">=AA-", (">=", "AA-")),
    ("<= BBB0", ("<=", "BBB0")),
    ("AA- 이상", (">=", "AA-")),
    ("3등급 이하", ("<=", "3등급")),
    ("BBB0 초과", (">", "BBB0")),
    ("5등급 미만", ("<", "5등급")),
    ("우량한 것", None),
]


def run_parse_rank_comparison_checks() -> bool:
    print("=" * 60)
    print(f"§5.1 _parse_rank_comparison() — {len(_PARSE_RANK_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for raw, expected in _PARSE_RANK_CASES:
        got = _parse_rank_comparison(raw)
        ok = got == expected
        print(f"[{'PASS' if ok else 'FAIL'}] _parse_rank_comparison({raw!r}) == {got!r} (기대 {expected!r})")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


# --------------------------------------------------------------------------
# §5.2 — OntologyValidator.expand_rank_filter() 회귀 (Task 11에서 이미 검증했지만
# 이번 작업이 호출부(PROMPT/정규식)를 건드리므로 전개 로직 자체는 그대로인지 다시 확인)
# --------------------------------------------------------------------------
def run_expand_rank_filter_checks(v) -> bool:
    print("=" * 60)
    print("§5.2 OntologyValidator.expand_rank_filter() 회귀")
    print("=" * 60)
    checks = [
        ("CreditRating, AA-, >=", lambda: v.expand_rank_filter("CreditRating", "AA-", ">=")
         == ["AAA", "AA+", "AA0", "AA-"]),
        ("RiskGrade, 5등급 낮은위험, <=", lambda: v.expand_rank_filter("RiskGrade", "5등급 낮은위험", "<=")
         == ["5등급 낮은위험", "6등급 매우낮은위험"]),
    ]
    results = []
    for label, check in checks:
        ok = bool(check())
        print(f"[{'PASS' if ok else 'FAIL'}] expand_rank_filter({label})")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


# --------------------------------------------------------------------------
# §5.3 — QueryParser.query() 전체를 가짜 LLM으로 돌려 부등호 전개 확인 (버그 A)
# --------------------------------------------------------------------------
def case_symbol_form_expands_to_credit_rating():
    """기호 표현(">=AA-") — 기존에도 되던 경로, 회귀 확인."""
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


def case_korean_form_expands_to_credit_rating():
    """한국어 표현("AA- 이상") — 버그 A의 핵심 재현. LLM이 부등호 기호 대신 한국어
    비교 표현을 그대로 내보내도(qp_task13_comparison.md §1.2) 전개가 조용히 무시되지
    않고 §5.2와 동일한 결과가 나와야 한다."""
    canned = json.dumps({
        "category": ["domestic_bond"],
        "logical_verification": ['fp:Bond fp:isSellable "true"'],
        "numerical_verification": {"credit_rating": "AA- 이상"},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("현재 판매 가능한 원화채권 중 AA- 이상 종목 알려줘")

    filters = result.get("resolved", {}).get("filters", [])
    hit = next((f for f in filters if f.get("concept") == "CreditRating"), None)
    ok = result.get("answerable") is True
    ok = ok and hit is not None
    ok = ok and hit.get("op") == "IN"
    ok = ok and hit.get("values") == ["AAA", "AA+", "AA0", "AA-"]
    # 신용등급 조건이 logical_verification이 아니라 numerical_verification을 거쳐 왔다는
    # 것 자체가 이 버그의 핵심(§2 example 주석) — logical_verification 트리플에서 나온
    # 필터가 하나도 없어야 한다(있었다면 "=" 단일값 필터로 잡혔을 것).
    ok = ok and not any(f.get("op") == "=" and f.get("concept") == "CreditRating" for f in filters)
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_unresolvable_comparison_falls_through_without_crash():
    """해석 불가("우량한 것") — 전개는 안 되지만 크래시하면 안 되고, 다른 유효 조건이
    있으면 그걸로 부분 답변을 계속 진행해야 한다."""
    canned = json.dumps({
        "category": ["domestic_bond"],
        "logical_verification": ['fp:Bond fp:isSellable "true"'],
        "numerical_verification": {"credit_rating": "우량한 것"},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("판매 가능한 우량한 채권 알려줘")

    filters = result.get("resolved", {}).get("filters", [])
    ok = result.get("answerable") is True
    ok = ok and not any(f.get("concept") == "CreditRating" for f in filters)
    if not ok:
        print(f"  result={result!r}")
    return ok


_QUERY_RANK_CASES = [
    ("기호 표현(>=AA-) — 회귀", case_symbol_form_expands_to_credit_rating),
    ("한국어 표현(AA- 이상) — 버그 A 재현 케이스", case_korean_form_expands_to_credit_rating),
    ("해석 불가(우량한 것) — 조용히 건너뛰되 크래시 없음", case_unresolvable_comparison_falls_through_without_crash),
]


def run_query_rank_expansion_checks() -> bool:
    print("=" * 60)
    print(f"§5.3 QueryParser.query() 통합 — 가짜 LLM, {len(_QUERY_RANK_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, fn in _QUERY_RANK_CASES:
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
# unsupported_conditions 처리 (버그 C, qp_task13_comparison.md §4.1) — 가짜 LLM
# --------------------------------------------------------------------------
def case_unsupported_does_not_cut_alone_when_valid_condition_exists():
    """unsupported_conditions만으로 컷하면 안 된다 — 유효 조건(신용등급 AAA)이 있으면
    그걸로 부분 답변을 계속 진행하고, think_trace에만 확인 불가 사실을 남긴다."""
    canned = json.dumps({
        "category": ["domestic_bond"],
        "logical_verification": ['fp:Bond fp:hasCreditRating "AAA"'],
        "numerical_verification": {},
        "unsupported_conditions": ["반도체 테마"],
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("반도체 테마이면서 신용등급 AAA인 국내채권 알려줘")

    ok = result.get("answerable") is True
    trace = result.get("think_trace", [])
    ok = ok and any("반도체 테마" in t and "제공 데이터에 없는 조건" in t for t in trace)
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_unsupported_only_cuts_with_accurate_reason():
    """유효 조건이 하나도 없이 unsupported_conditions만 있으면 Task 12의 빈 카테고리
    가드가 컷한다 — 이때 사유가 "AssetClass 값이 아님" 같은 엉뚱한 문구가 아니라
    unsupported_conditions를 반영한 정확한 문구여야 한다(qp_task13_comparison.md §0 버그 C)."""
    canned = json.dumps({
        "category": [],
        "logical_verification": [],
        "numerical_verification": {},
        "unsupported_conditions": ["반도체 테마", "에코프로의 자회사"],
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("반도체 테마이면서 에코프로의 자회사인 종목 알려줘")

    ok = result.get("answerable") is False
    suggestion = result.get("suggestion") or ""
    ok = ok and "반도체 테마" in suggestion and "에코프로의 자회사" in suggestion
    ok = ok and "제공된 데이터" in suggestion
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_unsupported_key_absent_keeps_existing_behavior():
    """unsupported_conditions 키 자체가 없어도(구버전 LLM 응답 등) 기존 동작이 깨지면
    안 된다 — 선택 필드다."""
    canned = json.dumps({
        "category": ["domestic_bond"],
        "logical_verification": ['fp:Bond fp:hasCreditRating "AAA"'],
        "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("신용등급 AAA인 국내채권 알려줘")

    ok = result.get("answerable") is True
    if not ok:
        print(f"  result={result!r}")
    return ok


_UNSUPPORTED_CASES = [
    ("유효 조건이 있으면 unsupported만으로 컷하지 않음", case_unsupported_does_not_cut_alone_when_valid_condition_exists),
    ("유효 조건 전무 + unsupported만 있으면 정확한 사유로 컷", case_unsupported_only_cuts_with_accurate_reason),
    ("unsupported_conditions 키 부재 시 기존 동작 유지", case_unsupported_key_absent_keeps_existing_behavior),
]


def run_unsupported_conditions_checks() -> bool:
    print("=" * 60)
    print(f"unsupported_conditions 처리 — 가짜 LLM, {len(_UNSUPPORTED_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, fn in _UNSUPPORTED_CASES:
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
    v = get_ontology_validator()
    ok1 = run_parse_rank_comparison_checks()
    ok2 = run_expand_rank_filter_checks(v)
    ok3 = run_query_rank_expansion_checks()
    ok4 = run_unsupported_conditions_checks()
    sys.exit(0 if (ok1 and ok2 and ok3 and ok4) else 1)


if __name__ == "__main__":
    main()
