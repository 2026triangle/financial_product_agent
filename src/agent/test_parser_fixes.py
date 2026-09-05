"""
================================================================================
test_parser_fixes.py — 작은따옴표 리터럴 파싱 + 미등록 술어 유실 방지 단위 테스트
(Task 16, qp_task16_parser_fixes.md §4.1/§4.2/§4.3)
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는다.

§4.1 — _split_triple() 리터럴 파싱(버그 D): 큰따옴표/작은따옴표/따옴표 없음/언어태그/
       따옴표 불일치 5가지 케이스. OntologyValidator._parse_triple()에 같은 문제가
       있는지도 실제 validate_triples() 호출로 확인한다(검증기는 수정하지 않는다 —
       발견 사실만 보고. §6 참고).
§4.2 — isSellable → SELLABLE 필터 승격(버그 E) + build_filter_clauses()의 테이블별
       WHERE절(국내채권 buyable_quantity>0, 나머지 컬럼=실측 참값).
§4.3 — 미등록 술어(_PREDICATE_TO_CONCEPT에 없는 predicate)가 think_trace에 남는지,
       컷되지 않는지(Task 14 정책 유지).

실행: cd src/agent && python test_parser_fixes.py
================================================================================
"""
import json
import sys

from tools.ontology_tool import get_ontology_validator
from agents.query_parser import QueryParser, _split_triple
from agents.text_to_sql_agent import build_filter_clauses


class _FakeModelManager:
    def __init__(self, canned_response: str):
        self.canned_response = canned_response
        self.call_count = 0

    def generate_text(self, *args, **kwargs):
        self.call_count += 1
        return self.canned_response


# --------------------------------------------------------------------------
# §4.1 — _split_triple() 리터럴 파싱 (버그 D)
# --------------------------------------------------------------------------
_SPLIT_TRIPLE_CASES = [
    ('fp:ForeignETF fp:investsInRegion "중국"', "중국", "큰따옴표"),
    ("fp:ForeignETF investsInRegion '중국'", "중국", "작은따옴표"),
    ("fp:Bond fp:hasCreditRating AAAA", "AAAA", "따옴표 없음(기존 동작 유지)"),
    ('fp:Bond fp:hasCreditRating "AA-"@ko', "AA-", "언어태그"),
    ("fp:X fp:y \"값'", "\"값'", "따옴표 불일치 — 리터럴로 안 봄, 원문 그대로"),
]


def run_split_triple_checks() -> bool:
    print("=" * 60)
    print(f"§4.1 _split_triple() 리터럴 파싱 — {len(_SPLIT_TRIPLE_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for triple, expected_obj, desc in _SPLIT_TRIPLE_CASES:
        split = _split_triple(triple)
        ok = split is not None and split[2] == expected_obj
        got = split[2] if split else None
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}: {triple!r} → obj={got!r} (기대 {expected_obj!r})")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


def run_validator_single_quote_check(v) -> None:
    """§4.1 마지막 항목 — 작은따옴표 트리플이 validate_triples()를 통과하는지.

    이 체크는 전체 통과 여부(exit code)에 반영하지 않는다 — OntologyValidator._parse_triple도
    query_parser.py와 동일한 큰따옴표 전용 _LITERAL_RE를 쓰고 있어(실측 확인함) 이 트리플이
    현재 valid=False로 잘못 컷된다. 검증기는 이 작업에서 수정하지 않기로 했으므로(§5 "하지
    말 것") 이 결과는 "현재 상태 보고"용으로만 출력한다 — §6 완료 후 보고에 그대로 옮긴다.
    """
    print("=" * 60)
    print("참고: OntologyValidator._parse_triple도 같은 작은따옴표 문제를 갖는지 확인")
    print("(이 작업 범위에서 검증기는 수정하지 않는다 — 결과만 보고, exit code에 미반영)")
    print("=" * 60)
    r = v.validate_triples(["fp:ForeignETF investsInRegion '중국'"])
    print(f"validate_triples([\"fp:ForeignETF investsInRegion '중국'\"]) = {r!r}")
    if r["valid"] is True:
        print("[OK] 검증기 쪽은 이미 정상 동작 — 작은따옴표를 올바르게 벗겨낸다.")
    else:
        print("[미해결] 검증기가 여전히 작은따옴표를 못 벗겨내 '중국'을 '''중국''' 형태로 "
              "취급하고 있다 — query_parser.py 쪽 파서를 고쳐도 validate_triples() 단계에서 "
              "먼저 컷되므로 이 케이스(중-3 유사 질의)는 여전히 답변 불가로 샌다. "
              "온톨로지 검증기(ontology_validator.py)의 _LITERAL_RE도 같은 방식으로 "
              "고쳐야 완전히 해결되는데, 이번 작업 범위에서는 검증기를 건드리지 않기로 "
              "했으므로 후속 작업으로 남긴다.")
    print()


# --------------------------------------------------------------------------
# §4.2 — isSellable → SELLABLE 필터 승격 + build_filter_clauses() 테이블별 WHERE절
# --------------------------------------------------------------------------
def case_sellable_filter_via_build_filters(qp):
    """_build_filters() 직접 호출 — Sellable 필터가 만들어지는지."""
    triples = ['fp:Bond fp:isSellable "true"']
    tables = {"domestic_bond": "tb_domestic_bond"}
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)

    hit = next((f for f in filters if f.get("concept") == "Sellable"), None)
    ok = hit is not None and hit.get("op") == "SELLABLE" and hit.get("table") == "tb_domestic_bond"
    ok = ok and excluded == set()
    ok = ok and any("판매가능 조건" in t for t in think_trace)
    if not ok:
        print(f"  filters={filters!r}\n  think_trace={think_trace!r}")
    return ok


def case_sellable_false_not_supported(qp):
    """isSellable이 false류 값이면(판매 불가를 묻는 질의) 필터를 만들지 않고 흔적만 남긴다."""
    triples = ['fp:Bond fp:isSellable "false"']
    tables = {"domestic_bond": "tb_domestic_bond"}
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)

    ok = filters == [] and excluded == set()
    ok = ok and any("판매 불가" in t for t in think_trace)
    if not ok:
        print(f"  filters={filters!r}\n  think_trace={think_trace!r}")
    return ok


def case_end_to_end_query_ha1_sellable_and_credit_rating():
    """하-1 질의 재현 — "현재 판매 가능한 원화채권 중 AA- 이상 종목 알려줘". 가짜 LLM으로
    query() 전체를 돌려 isSellable + 부등호 전개(CreditRating) 필터가 둘 다 만들어지는지,
    build_filter_clauses(tb_domestic_bond)에 두 조건이 모두 SQL 조각으로 나오는지 확인한다
    (버그 E — "판매 가능한" 조건이 통째로 빠지던 문제)."""
    canned = json.dumps({
        "category": ["domestic_bond"],
        "logical_verification": ['fp:Bond fp:isSellable "true"'],
        "numerical_verification": {"credit_rating": ">=AA-"},
    }, ensure_ascii=False)
    qp = QueryParser(_FakeModelManager(canned))
    result = qp.query("현재 판매 가능한 원화채권 중 AA- 이상 종목 알려줘")

    filters = result.get("resolved", {}).get("filters", [])
    ok = result.get("answerable") is True
    ok = ok and any(f.get("concept") == "Sellable" for f in filters)
    ok = ok and any(f.get("concept") == "CreditRating" for f in filters)

    clauses = build_filter_clauses(filters, "tb_domestic_bond")
    ok = ok and "buyable_quantity > 0" in clauses
    ok = ok and any(c.startswith("crd_grd IN") for c in clauses)
    if not ok:
        print(f"  filters={filters!r}\n  clauses={clauses!r}")
    else:
        print(f"  최종 WHERE절 조각: {clauses!r}")
    return ok


def run_sellable_table_clause_checks() -> bool:
    """SELLABLE 필터 하나를 테이블별로 돌려 WHERE절이 실측값대로 나오는지 확인한다."""
    sellable_filter = {
        "column": None, "op": "SELLABLE", "values": ["Y"],
        "source": "ontology", "concept": "Sellable",
        "category": None, "table": None,
    }
    checks = [
        ("tb_domestic_bond → buyable_quantity > 0", "tb_domestic_bond", ["buyable_quantity > 0"]),
        ("tb_domestic_etf → pd_sale_yn = 'Y'", "tb_domestic_etf", ["pd_sale_yn = 'Y'"]),
        ("tb_foreign_etf → pd_sale_yn = 'Y'", "tb_foreign_etf", ["pd_sale_yn = 'Y'"]),
        ("tb_fund → sale_yn = '판매중' (실측값, 'Y' 아님)", "tb_fund", ["sale_yn = '판매중'"]),
    ]
    results = []
    for label, table, expected in checks:
        f = dict(sellable_filter, table=table)  # 이 테스트는 table을 직접 채워서 호출
        clauses = build_filter_clauses([f], table)
        ok = clauses == expected
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {clauses!r}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


_SELLABLE_CASES = [
    ("_build_filters() — isSellable true → SELLABLE 필터 생성", case_sellable_filter_via_build_filters),
    ("_build_filters() — isSellable false → 필터 없음, 흔적만", case_sellable_false_not_supported),
    ("query() 통합 — 하-1 재현(판매가능 + 신용등급 둘 다 반영)", case_end_to_end_query_ha1_sellable_and_credit_rating),
]


def run_sellable_checks(qp) -> bool:
    print("=" * 60)
    print(f"§4.2 isSellable → SELLABLE 필터 — {len(_SELLABLE_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, fn in _SELLABLE_CASES:
        try:
            ok = bool(fn(qp)) if fn.__code__.co_argcount == 1 else bool(fn())
        except Exception as e:
            ok = False
            name = f"{name}  [예외: {type(e).__name__}: {e}]"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    ok_table = run_sellable_table_clause_checks()
    return failed == 0 and ok_table


# --------------------------------------------------------------------------
# §4.3 — 미등록 술어 로깅 (버그 E)
# --------------------------------------------------------------------------
def run_unmapped_predicate_checks(qp) -> bool:
    print("=" * 60)
    print("§4.3 미등록 술어 로깅 — _build_filters() 직접 호출")
    print("=" * 60)
    triples = ['fp:Fund fp:someUnknownProp "x"']
    tables = {"fund": "tb_fund"}
    think_trace = []
    filters, excluded = qp._build_filters(triples, tables, think_trace)

    ok = filters == [] and excluded == set()
    ok = ok and any("'someUnknownProp' 조건은 필터로 반영되지 않음" in t for t in think_trace)
    print(f"[{'PASS' if ok else 'FAIL'}] think_trace에 흔적 남김, 컷 아님(filters=[], excluded=set())")
    if not ok:
        print(f"  filters={filters!r}\n  excluded={excluded!r}\n  think_trace={think_trace!r}")
    print(f"-- {1 if ok else 0} passed, {0 if ok else 1} failed --\n")
    return ok


def main():
    v = get_ontology_validator()
    qp = QueryParser(None)  # _build_filters는 self.mm을 쓰지 않는다(LLM 호출 없음)

    ok1 = run_split_triple_checks()
    run_validator_single_quote_check(v)  # 참고용 — exit code에 반영하지 않음
    ok2 = run_sellable_checks(qp)
    ok3 = run_unmapped_predicate_checks(qp)

    sys.exit(0 if (ok1 and ok2 and ok3) else 1)


if __name__ == "__main__":
    main()
