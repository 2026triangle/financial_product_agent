"""
================================================================================
test_filter_resolution.py — 다중 카테고리 필터 조립 단위 테스트
(Task 10, qp_task10_multicategory.md §4.1/§4.2)
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는다. OntologyValidator.resolve_filter_value()와
QueryParser._build_filters()를 직접 호출하는 결정론적 테스트다 — 빠르고 비용이 없다.

주 재현 버그: "신용등급 AAA인 국내채권이랑 주식형 국내ETF 알려줘" 처럼 트리플 주어가
이미 상품군을 구분하고 있는데도, 예전 구현은 주어를 버리고 모든 카테고리와 교차
검사해서 신용등급 필터가 통째로 사라졌다(§0/§1 버그 A). 여기에 겹쳐서
normalize_value()의 None이 "개념 없음"/"변환 불필요"/"이 값만 없음" 세 가지를
구분 못 해 사실과 반대인 think_trace까지 찍었다(버그 B). resolve_filter_value()가
이 셋을 구분하고, _build_filters()가 주어 스코프를 지킨다.

실행: cd src/agent && python test_filter_resolution.py
================================================================================
"""
import json
import sys

from tools.ontology_tool import get_ontology_validator
from agents.query_parser import QueryParser


class _FakeModelManager:
    """QueryParser.query()를 실제 LLM 없이 끝까지 결정론적으로 돌리기 위한 가짜 mm.
    Agent.query()(agents/agent.py)가 mm.generate_text(query)를 호출하고 그 반환값을
    그대로 {"content":...}에 담으므로, 미리 정해둔 JSON 문자열을 돌려주기만 하면 된다."""

    def __init__(self, canned_response: str):
        self.canned_response = canned_response
        self.call_count = 0

    def generate_text(self, *args, **kwargs):
        self.call_count += 1
        return self.canned_response


# --------------------------------------------------------------------------
# §4.1 — OntologyValidator.resolve_filter_value() 단위 테스트
# --------------------------------------------------------------------------
_RESOLVE_FILTER_VALUE_CASES = [
    ("CreditRating", "AAA", "tb_domestic_bond", "as_is", "AAA"),
    ("CreditRating", "AA0", "tb_domestic_bond", "as_is", "AA0"),
    ("CreditRating", "AAA", "tb_fund", "as_is", "AAA"),
    ("AssetClass", "주식형", "tb_domestic_etf", "normalized", "주식"),
    ("AssetClass", "주식", "tb_foreign_etf", "normalized", "Equity"),
    ("AssetClass", "주식", "tb_domestic_bond", "not_in_table", None),
    ("AssetClass", "원자재", "tb_fund", "value_not_in_table", None),
    ("RiskGrade", "1등급 매우높은위험", "tb_foreign_etf", "not_in_table", None),
    ("RiskGrade", "매우높은위험(1등급)", "tb_domestic_etf", "normalized", "PD_RISK_GCD_11"),
    ("BondCategory", "국고채", "tb_fund", "not_in_table", None),
    ("AssetClass", "없는값", "tb_fund", "unknown_value", None),
    # 회귀(교차검증에서 발견): 한 테이블에 원천값이 여럿 몰리는 경우(Region의 국가별
    # 값) raw 자체가 그중 하나면 그대로 반환해야 한다 — 첫 값(Europe)으로 뭉개면 안 됨.
    ("Region", "Germany", "tb_foreign_etf", "normalized", "Germany"),
    ("Region", "Greater China", "tb_foreign_etf", "normalized", "Greater China"),
    # 회귀(교차검증에서 발견): validate_triples는 인스턴스 IRI 로컬명(예: 'AssetClass_Equity')도
    # 유효한 목적어로 통과시키는데, resolve_filter_value가 그걸 못 알아보면 검증은 통과했는데
    # 필터는 못 만드는(unknown_value) 불일치가 생긴다.
    ("AssetClass", "AssetClass_Equity", "tb_foreign_etf", "normalized", "Equity"),
    ("CreditRating", "CreditRating_AAA", "tb_domestic_bond", "as_is", "AAA"),
]


def run_resolve_filter_value_checks(v) -> bool:
    print("=" * 60)
    print(f"§4.1 OntologyValidator.resolve_filter_value() — {len(_RESOLVE_FILTER_VALUE_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for concept, raw, table, exp_reason, exp_value in _RESOLVE_FILTER_VALUE_CASES:
        r = v.resolve_filter_value(concept, raw, table)
        ok = r["reason"] == exp_reason and r["value"] == exp_value
        label = f"resolve_filter_value({concept!r}, {raw!r}, {table!r})"
        print(f"[{'PASS' if ok else 'FAIL'}] {label} = {r} (기대 reason={exp_reason!r}, value={exp_value!r})")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


# --------------------------------------------------------------------------
# §4.2 — QueryParser._build_filters() 단위 테스트
# --------------------------------------------------------------------------
def case_main_reproduction(qp):
    """주 재현 케이스 — 다중 카테고리, 트리플 주어가 각각 다른 상품군을 지목."""
    triples = [
        'fp:Bond fp:hasCreditRating "AAA"',
        'fp:DomesticETF fp:investsInAssetClass "주식형"',
    ]
    tables = {"domestic_bond": "tb_domestic_bond", "domestic_etf": "tb_domestic_etf"}
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)

    ok = len(filters) == 2 and excluded == set()
    bond_filter = next((f for f in filters if f.get("concept") == "CreditRating"), None)
    etf_filter = next((f for f in filters if f.get("concept") == "AssetClass"), None)
    ok = ok and bond_filter is not None and etf_filter is not None
    ok = ok and bond_filter.get("values") == ["AAA"]
    ok = ok and bond_filter.get("category") == "domestic_bond"
    ok = ok and bond_filter.get("table") == "tb_domestic_bond"
    ok = ok and etf_filter.get("values") == ["주식"]
    ok = ok and etf_filter.get("category") == "domestic_etf"
    ok = ok and etf_filter.get("table") == "tb_domestic_etf"

    if not ok:
        print(f"  filters={filters!r}")
        print(f"  excluded={excluded!r}")
        print(f"  think_trace={think_trace!r}")
    return ok


def case_regression_commodity_fund(qp):
    """회귀 케이스 — Task 8 케이스 5(원자재 펀드 제외)는 반드시 유지되어야 한다."""
    triples = ['fp:Fund fp:investsInAssetClass "원자재"']
    tables = {"fund": "tb_fund"}
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)

    ok = filters == [] and excluded == {"fund"}
    if not ok:
        print(f"  filters={filters!r}")
        print(f"  excluded={excluded!r}")
        print(f"  think_trace={think_trace!r}")
    return ok


def case_bond_category_fallback_must_not_crash(qp):
    """회귀 케이스(교차검증에서 발견). 주어가 4개 클래스에 안 걸리는 폴백 경로
    (targets=dict(tables))에서 hasBondCategory 트리플이 들어오고 tables에
    "domestic_bond"가 없으면, targets["domestic_bond"]를 직접 참조해 KeyError로
    크래시했었다. 건너뛰기만 하고 크래시하면 안 된다."""
    triples = ['fp:Product fp:hasBondCategory "국고채"']
    tables = {"fund": "tb_fund"}  # domestic_bond가 없다
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)  # 예외가 나면 이 줄에서 실패

    ok = filters == []
    if not ok:
        print(f"  filters={filters!r}")
        print(f"  excluded={excluded!r}")
        print(f"  think_trace={think_trace!r}")
    return ok


def case_scoped_subject_missing_from_tables_must_not_leak(qp):
    """회귀 케이스(교차검증에서 발견). 주어가 특정 카테고리(fp:Bond → domestic_bond)를
    명확히 지목하는데 그 카테고리가 tables(이번 질의의 category 배열)에 없으면, 절대
    다른 카테고리(fund)의 폴백 대상으로 새어나가면 안 된다 — 채권 신용등급 값이
    펀드 테이블 필터로 둔갑하는 오염을 막는다."""
    triples = ['fp:Bond fp:hasCreditRating "AAA"']
    tables = {"fund": "tb_fund"}  # domestic_bond가 없다 — fund로 새면 안 됨
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)

    ok = filters == []  # fund 테이블에 신용등급 필터가 생기면 안 됨
    if not ok:
        print(f"  filters={filters!r}")
        print(f"  excluded={excluded!r}")
        print(f"  think_trace={think_trace!r}")
    return ok


def case_bond_category_guarded_by_tables(qp):
    """회귀 케이스(교차검증에서 발견). hasBondCategory의 domain은 fp:Bond뿐이라
    category가 항상 "domestic_bond" 고정값인데, tables에 "domestic_bond"가 없는데도
    (예: LLM이 category 배열엔 "fund"만 넣고 트리플은 fp:Bond로 뽑는 불일치) 필터를
    만들면 resolved['tables']/resolved['filters']가 어긋난다. tables에 없으면 건너뛴다."""
    triples = ['fp:Bond fp:hasBondCategory "국고채권"']
    tables = {"fund": "tb_fund"}  # domestic_bond가 없다
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)

    ok = filters == []
    if not ok:
        print(f"  filters={filters!r}")
        print(f"  excluded={excluded!r}")
        print(f"  think_trace={think_trace!r}")
    return ok


def case_single_category_previously_broken(qp):
    """단일 카테고리 케이스 — 수정 전에는 필터 0개 + domestic_bond 제외였다(온톨로지
    필터가 아니라 Text-to-SQL이 원문에서 우연히 SQL을 맞춰 겉으로만 정상처럼 보였다)."""
    triples = ['fp:Bond fp:hasCreditRating "AAA"']
    tables = {"domestic_bond": "tb_domestic_bond"}
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)

    ok = len(filters) == 1 and filters[0].get("values") == ["AAA"] and excluded == set()
    if not ok:
        print(f"  filters={filters!r}")
        print(f"  excluded={excluded!r}")
        print(f"  think_trace={think_trace!r}")
    return ok


def case_fallback_subject_not_scoped(qp):
    """폴백 케이스 — 주어가 4개 클래스 중 어디에도 없으면(fp:Product) 모든 카테고리를
    순회하되, 안 맞는 조합이 있어도 그 카테고리를 제외하지 않는다."""
    triples = ['fp:Product fp:hasRiskGrade "1등급 매우높은위험"']
    tables = {"domestic_bond": "tb_domestic_bond", "foreign_etf": "tb_foreign_etf"}
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)

    ok = len(filters) == 1
    ok = ok and filters[0].get("category") == "domestic_bond"
    ok = ok and filters[0].get("table") == "tb_domestic_bond"
    ok = ok and excluded == set()
    if not ok:
        print(f"  filters={filters!r}")
        print(f"  excluded={excluded!r}")
        print(f"  think_trace={think_trace!r}")
    return ok


_BUILD_FILTERS_CASES = [
    ("주 재현 케이스 (다중 카테고리, 주어 스코프)", case_main_reproduction),
    ("회귀 케이스 (원자재 펀드 제외 — Task 8 케이스 5 유지)", case_regression_commodity_fund),
    ("단일 카테고리 케이스 (수정 전엔 필터 0개였음)", case_single_category_previously_broken),
    ("폴백 케이스 (주어가 4개 클래스에 없음, 제외 안 함)", case_fallback_subject_not_scoped),
    ("회귀: hasBondCategory는 tables에 domestic_bond 있을 때만", case_bond_category_guarded_by_tables),
    ("회귀: 주어 스코프 카테고리가 tables에 없으면 다른 카테고리로 안 샘", case_scoped_subject_missing_from_tables_must_not_leak),
    ("회귀: hasBondCategory 폴백 경로에서 KeyError로 크래시하면 안 됨", case_bond_category_fallback_must_not_crash),
]


def run_build_filters_checks(qp) -> bool:
    print("=" * 60)
    print(f"§4.2 QueryParser._build_filters() — {len(_BUILD_FILTERS_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, fn in _BUILD_FILTERS_CASES:
        try:
            ok = bool(fn(qp))
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
# §4.3(추가) — QueryParser.query() 전체를 가짜 LLM으로 결정론적으로 검증
# (교차검증에서 발견된 회귀: excluded_categories가 resolved['tables']에 반영 안 됨)
# --------------------------------------------------------------------------
def case_excluded_category_removed_from_resolved_tables():
    """회귀 케이스. _build_filters()가 판정한 excluded_categories가 resolved['tables']/
    table_results에서 실제로 빠져야 한다 — 안 그러면 이 결과를 읽는 다음 단계
    (Text-to-SQL)가 제외됐어야 할 테이블을 계속 조회 대상으로 본다."""
    canned = json.dumps({
        "category": ["fund"],
        "logical_verification": ['fp:Fund fp:investsInAssetClass "원자재"'],
        "numerical_verification": {},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("원자재에 투자하는 공모펀드 알려줘")

    ok = result.get("answerable") is True
    ok = ok and result.get("resolved", {}).get("tables") == {}
    ok = ok and result.get("table_results") == {}
    if not ok:
        print(f"  result={result!r}")
    return ok


def run_end_to_end_checks() -> bool:
    print("=" * 60)
    print("§4.3(추가) QueryParser.query() — 가짜 LLM으로 결정론적 검증, 1개 케이스")
    print("=" * 60)
    cases = [
        ("회귀: excluded_categories가 resolved['tables']에서도 빠짐", case_excluded_category_removed_from_resolved_tables),
    ]
    results = []
    for name, fn in cases:
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
    # QueryParser(mm=None) — _build_filters는 self.mm을 전혀 쓰지 않는다(LLM 호출 없음).
    # __init__은 get_ontology_validator()만 호출하므로(위와 같은 싱글톤 재사용) 안전하다.
    qp = QueryParser(None)

    ok1 = run_resolve_filter_value_checks(v)
    ok2 = run_build_filters_checks(qp)
    ok3 = run_end_to_end_checks()

    sys.exit(0 if (ok1 and ok2 and ok3) else 1)


if __name__ == "__main__":
    main()
