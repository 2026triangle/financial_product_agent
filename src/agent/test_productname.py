"""
================================================================================
test_productname.py — 상품 고유명사 질의 컷 문제(미정의 술어 정책) 단위 테스트
(Task 14, qp_task14_productname.md §4.1/§4.2)
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는다(§4.1b/§4.2는 가짜 mm으로 QueryParser.query()를 끝까지
돌리지만 실제 LLM 호출은 없다).

§4.1a — _is_coverage_violation()이 실제 OntologyValidator.validate_triples() 출력을
        "커버리지 밖"/"진짜 모순"으로 올바르게 나누는지 확인한다.
§4.1b — QueryParser.query() 전체(가짜 LLM)로 컷/컷아님이 실제로 그렇게 되는지 확인한다.
        혼합 케이스(커버리지 위반 + 모순 위반)에서 컷 사유가 모순 쪽으로 나오는지가 핵심.
§4.2  — product_name → LIKE 필터 승격 + Task 12 빈 카테고리 가드와의 상호작용(§3.3 정책).
        build_filter_clauses()의 LIKE 처리(테이블별 이름 컬럼 라우팅)도 함께 확인한다.

실행: cd src/agent && python test_productname.py
================================================================================
"""
import json
import sys

from tools.ontology_tool import get_ontology_validator
from agents.query_parser import QueryParser, _is_coverage_violation
from agents.text_to_sql_agent import build_filter_clauses


class _FakeModelManager:
    """다른 test_*.py와 동일한 가짜 mm — QueryParser.query()를 실제 LLM 없이 끝까지
    결정론적으로 돌린다."""

    def __init__(self, canned_response: str):
        self.canned_response = canned_response
        self.call_count = 0

    def generate_text(self, *args, **kwargs):
        self.call_count += 1
        return self.canned_response


# --------------------------------------------------------------------------
# §4.1a — _is_coverage_violation() 분류 단위 테스트 (실제 validate_triples 출력 사용)
# --------------------------------------------------------------------------
_CLASSIFICATION_CASES = [
    # (트리플, 기대: True=커버리지 밖(컷 아님) / False=진짜 모순(컷))
    ('fp:Bond fp:hasCreditRating "AAAA"', False),          # 허용값 위반
    ('fp:Fund fp:hasCreditRating "AAA"', False),           # domain 위반
    ('fp:Fund fp:hasName "국민성장펀드"', True),            # 미정의 속성
    ('fp:Unknown fp:hasRiskGrade "5등급 낮은위험"', True),   # 존재하지 않는 클래스
]


def run_classification_checks(v) -> bool:
    print("=" * 60)
    print(f"§4.1a _is_coverage_violation() — {len(_CLASSIFICATION_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for triple, expect_coverage in _CLASSIFICATION_CASES:
        verification = v.validate_triples([triple])
        ok = verification["valid"] is False and len(verification["violations"]) == 1
        got = _is_coverage_violation(verification["violations"][0]) if ok else None
        ok = ok and got == expect_coverage
        label = f"{triple!r} → coverage={got!r} (기대 {expect_coverage!r})"
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            print(f"  verification={verification!r}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


# --------------------------------------------------------------------------
# §4.1b — QueryParser.query() 전체(가짜 LLM) — 컷/컷아님이 실제로 그렇게 되는지
# --------------------------------------------------------------------------
def case_contradiction_value_violation_still_cuts():
    """진짜 모순(허용값 위반)은 여전히 컷되어야 한다 — 답변 불가 5문항 방어의 핵심 케이스."""
    canned = json.dumps({
        "category": ["domestic_bond"],
        "logical_verification": ['fp:Bond fp:hasCreditRating "AAAA"'],
        "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("신용등급 AAAA인 채권 알려줘")

    ok = result.get("answerable") is False
    ok = ok and "AAAA" in (result.get("reason") or "")
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_contradiction_domain_violation_still_cuts():
    """진짜 모순(domain 위반)도 여전히 컷되어야 한다."""
    canned = json.dumps({
        "category": ["fund"],
        "logical_verification": ['fp:Fund fp:hasCreditRating "AAA"'],
        "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("신용등급 AAA인 펀드 알려줘")

    ok = result.get("answerable") is False
    ok = ok and "적용할 수 없습니다" in (result.get("reason") or "")
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_coverage_undefined_predicate_does_not_cut():
    """버그 C 재현 — fp:hasName(미정의 속성)은 커버리지 밖이라 컷하면 안 되고, 해당
    트리플만 제외한 채 카테고리만으로 계속 진행해야 한다(재현 1, 답변 가능 케이스)."""
    canned = json.dumps({
        "category": ["fund"],
        "logical_verification": ['fp:Fund fp:hasName "국민성장펀드"'],
        "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("국민성장펀드의 구조와 투자전략 동향 등 찾아서 알려줘")

    ok = result.get("answerable") is True
    trace = result.get("think_trace", [])
    ok = ok and any("hasName" in t and "제외" in t for t in trace)
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_coverage_unknown_class_does_not_cut():
    """커버리지 밖(존재하지 않는 클래스)도 컷하면 안 된다."""
    canned = json.dumps({
        "category": ["domestic_bond"],
        "logical_verification": ['fp:Unknown fp:hasRiskGrade "5등급 낮은위험"'],
        "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("아무 채권이나 알려줘")

    ok = result.get("answerable") is True
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_mixed_coverage_and_contradiction_cuts_with_contradiction_reason():
    """혼합 케이스 — 커버리지 위반(hasName)과 모순 위반(AAAA)이 섞이면 모순 위반이
    하나라도 있으므로 컷하되, 사유는 반드시 AAAA(모순) 쪽이어야 한다 — hasName 사유로
    컷하면 순서가 잘못된 것이다(qp_task14_productname.md §2.1 경고)."""
    canned = json.dumps({
        "category": ["domestic_bond", "fund"],
        "logical_verification": [
            'fp:Fund fp:hasName "국민성장펀드"',
            'fp:Bond fp:hasCreditRating "AAAA"',
        ],
        "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("국민성장펀드랑 신용등급 AAAA인 채권 알려줘")

    ok = result.get("answerable") is False
    reason = result.get("reason") or ""
    ok = ok and "AAAA" in reason
    ok = ok and "hasName" not in reason  # hasName 사유가 아님을 명시적으로 확인
    if not ok:
        print(f"  result={result!r}")
    return ok


_QUERY_LEVEL_CASES = [
    ("진짜 모순(허용값 위반) — 여전히 컷", case_contradiction_value_violation_still_cuts),
    ("진짜 모순(domain 위반) — 여전히 컷", case_contradiction_domain_violation_still_cuts),
    ("커버리지 밖(미정의 속성 hasName) — 컷 아님", case_coverage_undefined_predicate_does_not_cut),
    ("커버리지 밖(존재하지 않는 클래스) — 컷 아님", case_coverage_unknown_class_does_not_cut),
    ("혼합(hasName + AAAA) — AAAA 사유로 컷", case_mixed_coverage_and_contradiction_cuts_with_contradiction_reason),
]


def run_query_level_checks() -> bool:
    print("=" * 60)
    print(f"§4.1b QueryParser.query() 통합 — 가짜 LLM, {len(_QUERY_LEVEL_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, fn in _QUERY_LEVEL_CASES:
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
# §4.2 — product_name → LIKE 필터 승격 + Task 12 가드 상호작용 (가짜 LLM)
# --------------------------------------------------------------------------
def case_name_filter_created_with_category():
    """카테고리가 있는 일반 케이스 — LIKE 필터가 생성되고 answerable True."""
    canned = json.dumps({
        "category": ["fund"],
        "logical_verification": [],
        "numerical_verification": {},
        "product_name": ["국민성장펀드"],
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("국민성장펀드 알려줘")

    filters = result.get("resolved", {}).get("filters", [])
    hit = next((f for f in filters if f.get("concept") == "ProductName"), None)
    ok = result.get("answerable") is True
    ok = ok and hit is not None
    ok = ok and hit.get("op") == "LIKE"
    ok = ok and hit.get("values") == ["국민성장펀드"]
    ok = ok and hit.get("source") == "user_text"
    if not ok:
        print(f"  result={result!r}")
    return ok


def case_name_filter_with_empty_category_does_not_cut():
    """§3.3 핵심 케이스 — category가 빈 배열이어도(재현 1과 동일하게 상품군을 명시하지
    않은 질의) 상품명 필터가 있으면 Task 12의 빈 카테고리 가드에 걸려 컷되면 안 된다.
    이번 구현이 택한 정책(4개 테이블 전체를 기본값으로 채움)대로 tables에 4종 전부가
    채워지는지도 함께 확인한다."""
    canned = json.dumps({
        "category": [],
        "logical_verification": [],
        "numerical_verification": {},
        "product_name": ["국민성장펀드"],
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("국민성장펀드의 구조와 투자전략 동향 등 찾아서 알려줘")

    ok = result.get("answerable") is True
    tables = result.get("resolved", {}).get("tables", {})
    ok = ok and set(tables.keys()) == {"domestic_bond", "domestic_etf", "foreign_etf", "fund"}
    filters = result.get("resolved", {}).get("filters", [])
    ok = ok and any(f.get("concept") == "ProductName" for f in filters)
    if not ok:
        print(f"  result={result!r}")
    return ok


_FILTER_LEVEL_CASES = [
    ("카테고리 있음 — LIKE 필터 생성, 컷 아님", case_name_filter_created_with_category),
    ("§3.3: 카테고리 빈 배열이어도 컷 아님(4개 테이블 기본값)", case_name_filter_with_empty_category_does_not_cut),
]


def run_filter_level_checks() -> bool:
    print("=" * 60)
    print(f"§4.2 상품명 필터 생성 — 가짜 LLM, {len(_FILTER_LEVEL_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, fn in _FILTER_LEVEL_CASES:
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
# TextToSQLAgent.build_filter_clauses() — LIKE 처리 + 테이블별 이름 컬럼 라우팅
# --------------------------------------------------------------------------
def run_build_filter_clauses_checks() -> bool:
    print("=" * 60)
    print("build_filter_clauses() — ProductName LIKE 처리 (테이블별 컬럼 라우팅)")
    print("=" * 60)
    name_filter = {
        "column": None, "op": "LIKE", "values": ["국민성장펀드"],
        "source": "user_text", "concept": "ProductName",
        "category": None, "table": None,
    }
    checks = [
        ("tb_fund → itm_nm", "tb_fund", "itm_nm LIKE '%국민성장펀드%'"),
        ("tb_domestic_bond → pd_nm", "tb_domestic_bond", "pd_nm LIKE '%국민성장펀드%'"),
        ("tb_domestic_etf → pd_nm", "tb_domestic_etf", "pd_nm LIKE '%국민성장펀드%'"),
        ("tb_foreign_etf → pd_nm", "tb_foreign_etf", "pd_nm LIKE '%국민성장펀드%'"),
    ]
    results = []
    for label, table, expected_clause in checks:
        clauses = build_filter_clauses([name_filter], table)
        ok = clauses == [expected_clause]
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {clauses!r} (기대 [{expected_clause!r}])")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


# --------------------------------------------------------------------------
# 회귀(교차검증 라운드 1에서 지적) — build_filter_clauses()가 ProductName뿐 아니라
# "개념만 알고 테이블을 모르는" 필터 전반(Task 13 부등호 전개 CreditRating/RiskGrade,
# table=None)도 처리하는지 확인한다. 예전 구현은 f_table을 None으로 둬서 이런 필터가
# 어느 테이블에도 매칭되지 않고 조용히 버려졌다 — 등급 비교 질의가 WHERE절 없는
# 전량 조회로 새는 치명적 결함이었다.
# --------------------------------------------------------------------------
def run_rank_expansion_filter_clause_checks() -> bool:
    print("=" * 60)
    print("build_filter_clauses() — 부등호 전개(table=None) 필터 회귀")
    print("=" * 60)
    credit_rating_filter = {
        "column": None, "op": "IN", "values": ["AAA", "AA+", "AA0", "AA-"],
        "source": "ontology", "concept": "CreditRating",
        "category": None, "table": None,
    }
    risk_grade_filter = {
        "column": None, "op": "IN", "values": ["5등급 낮은위험", "6등급 매우낮은위험"],
        "source": "ontology", "concept": "RiskGrade",
        "category": None, "table": None,
    }
    checks = [
        # (설명, 필터, 대상 테이블, 기대 clauses)
        ("CreditRating@tb_domestic_bond — 매핑 있음, WHERE절 생성",
         credit_rating_filter, "tb_domestic_bond",
         ["crd_grd IN ('AAA', 'AA+', 'AA0', 'AA-')"]),
        ("CreditRating@tb_fund — 매핑 없음, 조용히 건너뜀(크래시 없음)",
         credit_rating_filter, "tb_fund", []),
        ("RiskGrade@tb_domestic_etf — 매핑 있음, WHERE절 생성",
         risk_grade_filter, "tb_domestic_etf",
         ["pd_risk_cd IN ('5등급 낮은위험', '6등급 매우낮은위험')"]),
        ("RiskGrade@tb_foreign_etf — notAvailableIn, 조용히 건너뜀",
         risk_grade_filter, "tb_foreign_etf", []),
    ]
    results = []
    for label, f, table, expected in checks:
        clauses = build_filter_clauses([f], table)
        ok = clauses == expected
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {clauses!r} (기대 {expected!r})")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


def main():
    v = get_ontology_validator()
    ok1 = run_classification_checks(v)
    ok2 = run_query_level_checks()
    ok3 = run_filter_level_checks()
    ok4 = run_build_filter_clauses_checks()
    ok5 = run_rank_expansion_filter_clause_checks()
    sys.exit(0 if (ok1 and ok2 and ok3 and ok4 and ok5) else 1)


if __name__ == "__main__":
    main()
