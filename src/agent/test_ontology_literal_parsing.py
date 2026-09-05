"""
================================================================================
test_ontology_literal_parsing.py — OntologyValidator._parse_triple 작은따옴표 파싱
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는다.

배경: query_parser.py의 _LITERAL_RE/_split_triple은 Task 16(qp_task16_parser_fixes.md)
에서 큰따옴표뿐 아니라 작은따옴표 리터럴도 인정하도록 고쳐졌다. 그런데 검증기
(src/ontology/endpoint/ontology_validator.py)의 _parse_triple은 독립적으로 구현된
같은 성격의 파서라 동일한 문제를 그대로 갖고 있었다 — LLM이 `fp:investsInRegion
'중국'`처럼 작은따옴표를 쓰면 값에 따옴표가 그대로 남아("'중국'") Region 허용값과
매칭되지 않고, "'중국'는 존재하지 않는 Region 값입니다" 같은 문구로 정상 값이
잘못 컷됐다(실측: qp_task16 보고, 이번 요청으로 검증기 쪽도 동일한 규칙으로 수정).

이 파일은 그 수정을 검증한다:
§1 — validate_triples()가 실제로 요구된 4가지 케이스를 만족하는지(작은따옴표 통과,
     큰따옴표 통과, 큰따옴표 허용값 위반 컷 유지, 따옴표 없는 값 허용값 위반 컷 유지)
§2 — _parse_triple()의 리터럴 파싱 자체(큰따옴표/작은따옴표/따옴표 없음/언어태그/
     따옴표 불일치) — query_parser.py의 qp_task16 §4.1 표와 동일한 케이스

실행: cd src/agent && python test_ontology_literal_parsing.py
================================================================================
"""
import sys

from tools.ontology_tool import get_ontology_validator


# --------------------------------------------------------------------------
# §1 — validate_triples() 요구 케이스 4개 (사용자 요청 그대로)
# --------------------------------------------------------------------------
_VALIDATE_TRIPLES_CASES = [
    (["fp:ForeignETF fp:investsInRegion '중국'"], True,
     "작은따옴표 '중국' — 통제 어휘 허용값에 있으므로 통과해야 함"),
    (['fp:ForeignETF fp:investsInRegion "중국"'], True,
     "큰따옴표 \"중국\" — 회귀 없이 계속 통과"),
    (['fp:Bond fp:hasCreditRating "AAAA"'], False,
     "안전선: 큰따옴표 AAAA(존재하지 않는 CreditRating 값) — 여전히 컷"),
    (["fp:Bond fp:hasCreditRating AAAA"], False,
     "안전선: 따옴표 없는 AAAA — 여전히 컷"),
]


def run_validate_triples_checks(v) -> bool:
    print("=" * 60)
    print(f"§1 validate_triples() — 사용자 요청 4개 케이스")
    print("=" * 60)
    results = []
    for triples, expected_valid, desc in _VALIDATE_TRIPLES_CASES:
        r = v.validate_triples(triples)
        ok = r["valid"] is expected_valid
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}")
        print(f"    validate_triples({triples!r}) = {r!r}")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# §2 — _parse_triple() 리터럴 파싱 자체 (qp_task16 §4.1과 동일한 케이스 구성)
# --------------------------------------------------------------------------
_PARSE_TRIPLE_CASES = [
    ('fp:ForeignETF fp:investsInRegion "중국"', "중국", "큰따옴표"),
    ("fp:ForeignETF fp:investsInRegion '중국'", "중국", "작은따옴표"),
    ("fp:Bond fp:hasCreditRating AAAA", "AAAA", "따옴표 없음(기존 동작 유지)"),
    ('fp:Bond fp:hasCreditRating "AA-"@ko', "AA-", "언어태그"),
    ("fp:X fp:y \"값'", "\"값'", "따옴표 불일치 — 리터럴로 안 봄, 원문 그대로"),
]


def run_parse_triple_checks(v) -> bool:
    print("=" * 60)
    print(f"§2 OntologyValidator._parse_triple() 리터럴 파싱 — {len(_PARSE_TRIPLE_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for triple, expected_obj, desc in _PARSE_TRIPLE_CASES:
        parsed = v._parse_triple(triple)
        got = parsed[2] if parsed else None
        ok = parsed is not None and got == expected_obj
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}: {triple!r} → obj={got!r} (기대 {expected_obj!r})")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


def main():
    v = get_ontology_validator()
    ok1 = run_validate_triples_checks(v)
    ok2 = run_parse_triple_checks(v)
    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == "__main__":
    main()
