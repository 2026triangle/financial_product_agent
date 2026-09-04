"""
================================================================================
test_vocab_coverage.py — 자연어 표현 커버리지 단위 테스트
(Task 11, qp_task11_vocab.md §4.1/§4.2/§4.3)
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는다. OntologyValidator만으로 도는 결정론적 테스트다.

§4.1 — 동의어 충돌 검사: 개념별 "라벨+동의어" 전체에 중복이 없는지 확인한다.
       BondCategory는 레벨 간 동명이인(특수은행채/개인투자용국채) 2건만 화이트리스트로 뺀다.
§4.2 — 자연어 커버리지: 이번에 추가한 동의어로 통과해야 하는 질의와, 여전히
       컷되어야 하는(진짜 없는 값) 질의를 함께 확인한다 — 답변 불가 5문항 방어가 핵심.
§4.3 — 부등호 전개가 여전히 정상 동작하는지(회귀).

실행: cd src/agent && python test_vocab_coverage.py
================================================================================
"""
import sys

from tools.ontology_tool import get_ontology_validator

# BondCategory 레벨 간 동명이인 — common.ttl 자체가 문서화한 기존 사실이며 새로 생긴 게 아니다.
_BOND_CATEGORY_HOMONYM_WHITELIST = {"특수은행채", "개인투자용국채"}

_CONCEPTS = ["RiskGrade", "CreditRating", "AssetClass", "Region", "BondCategory"]


def run_collision_checks(v) -> bool:
    print("=" * 60)
    print("§4.1 동의어 충돌 검사 — 개념별 라벨+동의어 중복 여부")
    print("=" * 60)
    all_ok = True
    for concept in _CONCEPTS:
        seen = {}
        dupes = []
        for inst in v.instances_by_concept[concept]:
            for token in [inst["label"]] + inst["synonyms"]:
                if concept == "BondCategory" and token in _BOND_CATEGORY_HOMONYM_WHITELIST:
                    continue
                if token in seen and seen[token] != inst["local"]:
                    dupes.append((token, seen[token], inst["local"]))
                    continue
                seen[token] = inst["local"]
        ok = not dupes
        all_ok = all_ok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {concept}: {'중복 없음' if ok else dupes}")
    print()
    return all_ok


# 이번 작업(Task 11)에서 추가한 동의어로 통과해야 하는 질의 — (트리플, 설명)
_SHOULD_PASS = [
    ('fp:ForeignETF fp:investsInRegion "미국쪽"', "Region 구어체"),
    ('fp:DomesticETF fp:investsInAssetClass "주식 위주"', "AssetClass 구어체"),
    ('fp:Bond fp:hasRiskGrade "5등급"', "RiskGrade 숫자 표기"),
    ('fp:Bond fp:hasBondCategory "국채"', "BondCategory 기존 동의어(국고채권) 유지"),
    ('fp:ForeignETF fp:investsInRegion "차이나"', "Region 구어체"),
    ('fp:Bond fp:hasCreditRating "AAA등급"', "CreditRating 표기 변형"),
    ('fp:Bond fp:hasBondCategory "나라채권"', "BondCategory 대분류 구어체(국공채)"),
    ('fp:DomesticETF fp:investsInAssetClass "리츠형"', "AssetClass 구어체(부동산)"),
]

# 진짜 없는 값 — 동의어를 늘리다 실수로 통과시키면 안 된다(답변 불가 5문항 방어).
_SHOULD_CUT = [
    'fp:Bond fp:hasCreditRating "AAAA"',
    'fp:Bond fp:hasRiskGrade "7등급"',
    'fp:Bond fp:hasBondCategory "없는분류"',
    'fp:ForeignETF fp:investsInRegion "화성"',
    # §1 원칙 위반 방지 회귀 — 범위 표현은 동의어로 들어가면 안 된다(부등호로만 처리해야 함).
    'fp:Bond fp:hasCreditRating "우량등급"',
    'fp:Bond fp:hasCreditRating "투자등급"',
    'fp:Bond fp:hasRiskGrade "낮음"',
]


def run_coverage_checks(v) -> bool:
    print("=" * 60)
    print("§4.2 자연어 커버리지")
    print("=" * 60)
    results = []

    print("-- 통과해야 하는 것 --")
    for triple, desc in _SHOULD_PASS:
        r = v.validate_triples([triple])
        ok = r["valid"] is True
        print(f"[{'PASS' if ok else 'FAIL'}] {triple!r} ({desc}) valid={r['valid']}")
        if not ok:
            print(f"  violations={r['violations']!r}")
        results.append(ok)

    print("-- 여전히 컷되어야 하는 것 --")
    for triple in _SHOULD_CUT:
        r = v.validate_triples([triple])
        ok = r["valid"] is False
        print(f"[{'PASS' if ok else 'FAIL'}] {triple!r} valid={r['valid']} (기대 False)")
        results.append(ok)

    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --")
    print()
    return failed == 0


def run_rank_expansion_checks(v) -> bool:
    print("=" * 60)
    print("§4.3 부등호 전개 (회귀)")
    print("=" * 60)
    checks = [
        ("RiskGrade, 5등급 낮은위험, <=", lambda: v.expand_rank_filter("RiskGrade", "5등급 낮은위험", "<=")
         == ["5등급 낮은위험", "6등급 매우낮은위험"]),
        ("RiskGrade, 2등급 높은위험, >=", lambda: v.expand_rank_filter("RiskGrade", "2등급 높은위험", ">=")
         == ["1등급 매우높은위험", "2등급 높은위험"]),
        ("CreditRating, AA-, >=", lambda: v.expand_rank_filter("CreditRating", "AA-", ">=")
         == ["AAA", "AA+", "AA0", "AA-"]),
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


def main():
    v = get_ontology_validator()
    ok1 = run_collision_checks(v)
    ok2 = run_coverage_checks(v)
    ok3 = run_rank_expansion_checks(v)
    sys.exit(0 if (ok1 and ok2 and ok3) else 1)


if __name__ == "__main__":
    main()
