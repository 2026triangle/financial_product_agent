"""
================================================================================
test_ontology_cut.py — 답변 불가능 질의 컷 회귀 테스트 (Task 8, qp_spec.md §7)
--------------------------------------------------------------------------------
온톨로지 런타임 컷(agents/query_parser.py + src/ontology/endpoint/ontology_validator.py)이
실제로 "LLM 재호출 없이" 답변 불가능 질문을 걸러내는지, 그리고 값 정규화·채권분류 컬럼
라우팅이 정상 동작하는지 결정론적으로 확인한다. 제출 시 "온톨로지를 런타임 통제에 쓴다"는
증거물로 쓴다.

실행: cd src/agent && python test_ontology_cut.py
기대 출력: 정규화 단위 테스트(LLM 미사용) 전부 통과 + "6 passed, 0 failed"(LLM 케이스)

구성 (qp_spec.md §7):
  §7.2 정규화 단위 테스트 — OntologyValidator만으로 도는 LLM 불필요 테스트. 별도 함수로 두되
       같은 실행에서 먼저 돈다(파일을 새로 만들지 않고 이 파일 안에 구획을 분리).
       트리플 총 개수는 계속 늘어나므로 assert하지 않는다.
  §7.1 컷 회귀 테스트 — LLM 호출이 실제로 발생하는 6케이스. 케이스마다 성격이 달라 종류로 나눈다:
       - "cut"          : 반드시 answerable=False로 컷되어야 하는 케이스 (1~4, 5)
                          5번은 Task 12(qp_task12_unanswerable.md §7.3)에서 기대값이
                          바뀌었다 — 원래 "컷 아님"이었으나, 이제 원자재 조건 하나 때문에
                          fund 카테고리 전체가 제외되면 답할 대상이 없어 컷되는 게 옳다.
                          "expect_trace_substring"으로 제외 판단이 think_trace에 남는지도 검사한다.
       - "pass_through" : 컷되면 안 되고, 특정 카테고리 제외 판단이 think_trace에 남아야 하는
                          케이스용 (현재 CASES에는 없음 — 여러 카테고리 중 일부만 제외되는
                          시나리오가 생기면 재사용)
       - "diagnostic"   : 두 경로(허용값 위반 컷 / 트리플 미추출) 중 무엇이 일어났는지 구분해
                          출력하는 것 자체가 목적인 케이스 (6)

주의:
- 케이스마다 실제 LLM 호출이 1회(약 18초) 발생한다. 6케이스 순차 실행 — 병렬화하지 않는다.
- 답변 가능한(진짜 검색까지 가는) 질의는 절대 넣지 않는다. search_finance_metadata까지
  도달하면 MCT가 BGE-M3(약 2GB)를 내려받으려 시도하는데 meta_schema_table이 현재 0건이라
  무의미하다 — 단, query_parser.py의 §6 가드가 이미 이걸 막으므로 실제로는 호출되지 않는다.
================================================================================
"""
import sys

from main import Agent
from core.model_manager import get_model_manager
from tools.ontology_tool import get_ontology_validator


# --------------------------------------------------------------------------
# §7.2 — 정규화 단위 테스트 (LLM 불필요, 결정론적, 빠름)
# --------------------------------------------------------------------------
def run_normalization_checks() -> bool:
    v = get_ontology_validator()
    checks = [
        ("normalize_value(RiskGrade, 매우높은위험(1등급), tb_domestic_etf) == PD_RISK_GCD_11",
         lambda: v.normalize_value('RiskGrade', '매우높은위험(1등급)', 'tb_domestic_etf') == 'PD_RISK_GCD_11'),
        ("normalize_value(AssetClass, 주식, tb_foreign_etf) == Equity",
         lambda: v.normalize_value('AssetClass', '주식', 'tb_foreign_etf') == 'Equity'),
        ("normalize_value(AssetClass, 주식, tb_fund) == 주식형",
         lambda: v.normalize_value('AssetClass', '주식', 'tb_fund') == '주식형'),
        ("resolve_category(국고채)['column'] == std_pd_scls_nm",
         lambda: v.resolve_category('국고채')['column'] == 'std_pd_scls_nm'),
        ("resolve_category(국공채)['column'] == std_pd_mcls_nm",
         lambda: v.resolve_category('국공채')['column'] == 'std_pd_mcls_nm'),
        ("expand_rank_filter(CreditRating, AA-, >=) == [AAA,AA+,AA0,AA-]",
         lambda: v.expand_rank_filter('CreditRating', 'AA-', '>=') == ['AAA', 'AA+', 'AA0', 'AA-']),
        ("len(list_allowed_values(BondCategory)) == 61",
         lambda: len(v.list_allowed_values('BondCategory')) == 61),
    ]

    print("=" * 60)
    print("§7.2 정규화 단위 테스트 (LLM 미사용, 온톨로지 트리플 총수는 assert하지 않음)")
    print("=" * 60)
    results = []
    for label, check in checks:
        try:
            ok = bool(check())
        except Exception as e:
            ok = False
            label = f"{label}  [예외: {type(e).__name__}: {e}]"
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


# --------------------------------------------------------------------------
# §7.1 — LLM 컷 회귀 테스트 6케이스
# --------------------------------------------------------------------------
CASES = [
    {
        "kind": "cut",
        "name": "신용등급 허용값 위반 (CreditRating)",
        "query": "신용등급 AAAA인 채권 찾아줘",
    },
    {
        "kind": "cut",
        "name": "채권분류 허용값 위반 (BondCategory)",
        "query": "외계채권ㅋㅋ 찾아줘",
    },
    {
        "kind": "cut",
        "name": "domain 위반 (hasCreditRating은 fp:Bond 전용)",
        "query": "신용등급 AAA인 펀드 찾아줘",
    },
    {
        "kind": "cut",
        "name": "위험등급 허용값 위반 (RiskGrade, 1~6등급뿐)",
        "query": "7등급 위험 상품 알려줘",
    },
    {
        # Task 12(qp_task12_unanswerable.md §7.3)에서 기대값이 바뀌었다. 원자재는
        # tb_fund에 없는 값(value_not_in_table)이고 카테고리가 fund 하나뿐이라, 이 조건을
        # 제외하면 답할 수 있는 상품군이 하나도 안 남는다 — 이제는 이게 "컷 아님"이 아니라
        # 정확히 "확인할 수 없음 + 역질문"으로 컷되는 게 옳다(답변 불가 문항 정답 형태).
        # 가드를 약화시키지 않고 기대값을 고쳤다 — think_trace에 원자재 제외 판단이
        # 남아 있는지는 expect_trace_substring으로 계속 검사한다.
        "kind": "cut",
        "name": "원자재 펀드 → 조회 대상 없음으로 컷 (Task 12 Step 2)",
        "query": "원자재에 투자하는 공모펀드 알려줘",
        "expect_trace_substring": "제외",
    },
    {
        "kind": "diagnostic",
        "name": "존재하지 않는 분류 (컷 경로 구분)",
        "query": "존재하지않는분류 ETF 찾아줘",
    },
]


def _wrap_call_counter(mm):
    """mm.generate_text 호출 횟수를 세는 카운터로 인스턴스 메서드를 감싼다.
    인스턴스 속성으로 덮어써서 이 mm 싱글톤을 쓰는 모든 호출부(Agent 기반 클래스 전부)에
    자동으로 적용된다."""
    state = {"count": 0}
    original = mm.generate_text

    def counting_generate_text(*args, **kwargs):
        state["count"] += 1
        return original(*args, **kwargs)

    mm.generate_text = counting_generate_text
    return state


def _print_header(name, query, call_count):
    print(f"  질의: {query!r}")
    print(f"  LLM 호출: {call_count}회")


def _run_cut_case(query_parser, result, call_count, case=None):
    """1~4번, 5번: 반드시 answerable=False로 컷되어야 한다.
    case에 "expect_trace_substring"이 있으면(5번 — Task 12 Step 2) think_trace에
    그 부분 문자열이 남아 있는지도 검사한다 — 카테고리가 왜 제외됐는지 로그가
    실제로 남는지 확인하려는 것이다(qp_task12_unanswerable.md §7.3)."""
    failures = []

    if result.get("answerable") is not False:
        failures.append(f"answerable이 False가 아님 (실제: {result.get('answerable')!r})")

    reason = result.get("reason") or ""
    suggestion = result.get("suggestion") or ""
    content = result.get("content") or ""

    if not reason:
        failures.append("reason이 비어 있음")
    if not suggestion:
        failures.append("suggestion이 비어 있음")
    if reason and reason not in content:
        failures.append("content에 reason이 포함되지 않음")
    if suggestion and suggestion not in content:
        failures.append("content에 suggestion이 포함되지 않음")

    # table_results 키가 있다는 건 BGE-M3 경로까지 진행했다는 뜻 — 온톨로지 컷이 안 먹혔다는 증거다.
    if "table_results" in result:
        failures.append("table_results 키가 존재함 (BGE-M3 경로까지 진행됨)")
    if "resolved" in result or "parsed" in result:
        failures.append("resolved/parsed 키가 존재함 (컷된 경우엔 있으면 안 됨)")

    if call_count != 1:
        failures.append(f"LLM 호출 횟수가 1이 아님 (실제: {call_count}회)")

    expect_substr = (case or {}).get("expect_trace_substring")
    if expect_substr:
        think_trace = result.get("think_trace") or []
        if not any(expect_substr in t for t in think_trace):
            failures.append(f"think_trace에 '{expect_substr}' 관련 로그가 없음: {think_trace!r}")

    passed = not failures
    if passed:
        print(f"  reason: {reason}")
        print(f"  suggestion: {suggestion}")
    else:
        for f in failures:
            print(f"  - {f}")
        # 원인 분리는 "컷이 아예 안 걸린 경우"(answerable이 False가 아닌 경우)에만 의미가 있다.
        if result.get("answerable") is not False:
            parsed = result.get("parsed")
            logical_verification = parsed.get("logical_verification") if isinstance(parsed, dict) else None
            if logical_verification:
                print(f"  logical_verification (validator가 통과시킴 → 온톨로지 문제 의심): {logical_verification!r}")
            else:
                print(f"  logical_verification 없음/비어있음 → agents/query_parser.py PROMPT 문제 의심 (parsed={parsed!r})")
    return passed


def _run_pass_through_case(query_parser, result, call_count, case=None):
    """5번: 컷되면 안 되고, 특정 카테고리 제외 판단이 think_trace에 남아야 한다."""
    failures = []

    if result.get("answerable") is not True:
        failures.append(f"answerable이 True가 아님 (컷되면 안 되는 케이스, 실제: {result.get('answerable')!r})")
    if "table_results" not in result:
        failures.append("table_results 키가 없음 (컷 아닌 경우엔 있어야 함 — 빈 dict라도)")
    if call_count != 1:
        failures.append(f"LLM 호출 횟수가 1이 아님 (실제: {call_count}회)")

    think_trace = result.get("think_trace") or []
    excluded_evidence = [t for t in think_trace if "제외" in t]
    if not excluded_evidence:
        failures.append("think_trace에 카테고리 제외 판단('원자재는 tb_fund에 없음' 류)이 없음")

    passed = not failures
    if passed:
        print(f"  카테고리 제외 판단: {excluded_evidence}")
        print(f"  resolved: {result.get('resolved')}")
    else:
        for f in failures:
            print(f"  - {f}")
        print(f"  parsed: {result.get('parsed')!r}")
        print(f"  think_trace: {think_trace!r}")
    return passed


def _run_diagnostic_case(query_parser, result, call_count, case=None):
    """6번: 허용값 위반 컷 / 트리플 미추출 중 어느 경로인지 구분해서 보여주는 게 목적이다.
    두 경로 모두 '정상'으로 본다 — 다만 검색까지 새어나간 경우(진짜 문제)만 FAIL 처리한다."""
    answerable = result.get("answerable")

    if answerable is False:
        reason = result.get("reason") or ""
        suggestion = result.get("suggestion") or ""
        print("  경로: 허용값 위반으로 컷됨")
        print(f"  reason: {reason}")
        print(f"  suggestion: {suggestion}")
        ok = bool(reason) and bool(suggestion) and "table_results" not in result
        if not ok:
            print("  - reason/suggestion이 비어 있거나 table_results가 남아 있음")
        return ok

    # answerable True — 트리플을 아예 안 뽑았는지, 뽑았는데 통과시켰는지 구분한다.
    parsed = result.get("parsed")
    logical_verification = parsed.get("logical_verification") if isinstance(parsed, dict) else None
    table_results = result.get("table_results") or {}
    leaked = {k: v for k, v in table_results.items() if v}  # 실제 검색 결과가 든 카테고리

    if leaked:
        print("  경로: 컷되지 않았고 실제 검색 결과까지 새어나감 — FAIL")
        print(f"  table_results: {table_results!r}")
        return False

    if logical_verification:
        print("  경로: 트리플은 뽑혔으나 validator가 통과시킴 → 온톨로지/개념 매핑 문제 의심")
        print(f"  logical_verification: {logical_verification!r}")
    else:
        print("  경로: logical_verification 트리플 미추출 (이 개념엔 PROMPT가 트리플을 안 만듦)")
        print(f"  parsed: {parsed!r}")
    return True


_RUNNERS = {
    "cut": _run_cut_case,
    "pass_through": _run_pass_through_case,
    "diagnostic": _run_diagnostic_case,
}


def run_case(query_parser, call_state, case):
    name, query, kind = case["name"], case["query"], case.get("kind", "cut")
    call_state["count"] = 0

    try:
        result = query_parser.query(query)
    except Exception as e:
        print(f"[FAIL] {name} — 예외 발생: {type(e).__name__}: {e}")
        print()
        return False

    call_count = call_state["count"]
    _print_header(name, query, call_count)
    passed = _RUNNERS[kind](query_parser, result, call_count, case)
    print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    print()
    return passed


def main():
    print("=" * 60)
    print("Task 8 회귀 테스트 — 온톨로지 런타임 컷 + 값 정규화 + 채권분류 라우팅")
    print("=" * 60)
    print()

    norm_ok = run_normalization_checks()

    print("=" * 60)
    print(f"§7.1 LLM 컷 회귀 테스트 — 케이스 {len(CASES)}개, 순차 실행 (병렬화 안 함, 케이스당 ~18초 소요 예상)")
    print("=" * 60)
    print()

    # Agent()는 한 번만 생성해 모든 케이스에서 공유한다 — rdflib 로드와 도구 등록을
    # 반복하지 않기 위함. main.py의 __main__ 가드 덕에 import만으로는 실행되지 않는다.
    app_agent = Agent()
    query_parser = app_agent.agents["QueryParser"]
    call_state = _wrap_call_counter(get_model_manager())

    results = []
    for case in CASES:
        results.append(run_case(query_parser, call_state, case))

    passed = sum(1 for r in results if r)
    failed = len(results) - passed
    print("=" * 60)
    print(f"{passed} passed, {failed} failed")
    print("=" * 60)

    sys.exit(0 if (failed == 0 and norm_ok) else 1)


if __name__ == "__main__":
    main()
