"""
================================================================================
test_extract_json.py — QueryParser._extract_json 단위 테스트 (Task 9, qp_task9_json.md §7.1)
--------------------------------------------------------------------------------
LLM을 전혀 호출하지 않는 순수 함수 테스트다. 결정론적이고 빠르다 — 실행해도 비용이 없다.

qp_task9_json.md §1이 지적한 문제(완결된 JSON 객체가 둘 이상이면 find/rfind·코드펜스
정규식 둘 다 그 사이 텍스트까지 삼켜서 반드시 깨짐)를 재현하기 위해, 수정 전 구현을
`_OLD_extract_json_for_comparison`으로 그대로 남겨두고 4번·6번 픽스처에서 실제로
실패하는 것을 먼저 보인 뒤, 새 구현(`agents.query_parser._extract_json`)이 통과시키는
것을 확인한다.

실행: cd src/agent && python test_extract_json.py
================================================================================
"""
import json
import re
import sys

from agents.query_parser import _extract_json


# --------------------------------------------------------------------------
# 비교용 — 수정 전 구현 (qp_task9_json.md §1 원문 그대로). 이 파일에서만 쓰고
# 실제 코드에는 반영하지 않는다. 4번·6번이 왜 이번 작업의 핵심인지 보여주는 용도.
# --------------------------------------------------------------------------
def _OLD_extract_json_for_comparison(text):
    if not text:
        return None
    text = text.strip()

    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


# --------------------------------------------------------------------------
# 픽스처 — qp_task9_json.md §7.1 표 12개 그대로
# --------------------------------------------------------------------------
_TWO_JSON_WITH_TEXT_BETWEEN = (
    '{"category": ["fund"], "logical_verification": [], "numerical_verification": {}}\n'
    '위 결과를 다시 검토해보니 다음이 맞습니다:\n'
    '{"category": ["domestic_bond"], "logical_verification": [], "numerical_verification": {}}'
)

_TWO_CODE_FENCES = (
    '```json\n'
    '{"category": ["fund"], "logical_verification": [], "numerical_verification": {}}\n'
    '```\n'
    '다시 정리하면:\n'
    '```json\n'
    '{"category": ["domestic_bond"], "logical_verification": [], "numerical_verification": {}}\n'
    '```'
)


def case_1_single_json():
    text = '{"category": ["domestic_bond"], "logical_verification": [], "numerical_verification": {}}'
    result = _extract_json(text)
    return isinstance(result, dict) and result.get("category") == ["domestic_bond"]


def case_2_leading_prose():
    text = (
        '네, 분석해보겠습니다.\n'
        '{"category": ["domestic_bond"], "logical_verification": [], "numerical_verification": {}}'
    )
    result = _extract_json(text)
    return isinstance(result, dict) and result.get("category") == ["domestic_bond"]


def case_3_trailing_prose():
    text = (
        '{"category": ["domestic_bond"], "logical_verification": [], "numerical_verification": {}}\n'
        '위와 같이 분석했습니다.'
    )
    result = _extract_json(text)
    return isinstance(result, dict) and result.get("category") == ["domestic_bond"]


def case_4_two_json_objects():
    result = _extract_json(_TWO_JSON_WITH_TEXT_BETWEEN)
    return isinstance(result, dict) and result.get("category") == ["domestic_bond"]


def case_5_single_code_fence():
    text = (
        '```json\n'
        '{"category": ["domestic_bond"], "logical_verification": [], "numerical_verification": {}}\n'
        '```'
    )
    result = _extract_json(text)
    return isinstance(result, dict) and result.get("category") == ["domestic_bond"]


def case_6_two_code_fences():
    result = _extract_json(_TWO_CODE_FENCES)
    return isinstance(result, dict) and result.get("category") == ["domestic_bond"]


def case_7_nested_numerical_verification():
    text = (
        '{"category": ["domestic_bond"], "logical_verification": [], '
        '"numerical_verification": {"rate_of_return": ">10%"}}'
    )
    result = _extract_json(text)
    return (
        isinstance(result, dict)
        and result.get("numerical_verification") == {"rate_of_return": ">10%"}
    )


def case_8_braces_inside_string_value():
    text = (
        '{"category": ["domestic_bond"], "logical_verification": [], '
        '"numerical_verification": {}, "description": "{인덱스}"}'
    )
    result = _extract_json(text)
    return isinstance(result, dict) and result.get("description") == "{인덱스}"


def case_9_escaped_quotes_preserved():
    text = (
        '{"category": ["domestic_bond"], '
        '"logical_verification": ["fp:Bond fp:hasCreditRating \\"AAAA\\""], '
        '"numerical_verification": {}}'
    )
    result = _extract_json(text)
    return (
        isinstance(result, dict)
        and result.get("logical_verification") == ['fp:Bond fp:hasCreditRating "AAAA"']
    )


def case_10_no_json_at_all():
    return _extract_json("이 질문은 금융상품과 무관한 것 같습니다.") is None


def case_11_empty_or_none():
    return _extract_json("") is None and _extract_json(None) is None


def case_12_unterminated_fragment():
    try:
        result = _extract_json('{"category": ["fund"], "logical_verification": [')
    except Exception:
        return False  # 예외를 던지면 안 된다 — 그 자체로 FAIL
    return result is None


def case_13_unmatched_brace_in_preamble():
    """회귀 픽스처(교차검증에서 발견). 선행 해설 문장에 짝이 안 맞는 '{'가 하나 섞여도
    그 뒤에 오는 완결된 JSON 객체는 정상적으로 후보에 잡혀야 한다 — 전역 depth 카운터
    하나로만 훑으면 이 '{' 때문에 기준선이 밀려서 뒤따르는 정상 객체를 통째로 놓친다."""
    text = (
        '분석 결과를 말씀드리면 이 질문은 { 라고 표현할 수 있는 애매한 지점이 있는데 '
        '무시하고 계속 설명하자면\n'
        '{"category": ["domestic_bond"], "logical_verification": [], "numerical_verification": {}}'
    )
    result = _extract_json(text)
    return isinstance(result, dict) and result.get("category") == ["domestic_bond"]


def case_14_nested_child_must_not_shadow_parent():
    """회귀 픽스처(교차검증 라운드 3). 최상위 객체에 "category" 키가 없는 비정상 응답에서,
    중첩된 하위 객체(numerical_verification의 값)가 [-1] 선택 로직에 의해 전체 대신
    잘못 뽑히면 안 된다 — 최상위(비중첩) 객체가 우선이어야 한다."""
    text = (
        '{"logical_verification": ["fp:Bond fp:hasCreditRating \\"AAA\\""], '
        '"numerical_verification": {"rate_of_return": ">10%"}}'
    )
    result = _extract_json(text)
    return isinstance(result, dict) and "logical_verification" in result


def case_15_unmatched_quote_in_preamble():
    """회귀 픽스처(교차검증 라운드 3). 선행 해설 문장에 짝이 안 맞는 큰따옴표가 하나
    있어도, 문자열 판정을 전역으로 미리 계산하지 않으므로 그 뒤의 진짜 JSON은
    정상적으로 후보에 잡혀야 한다."""
    text = (
        '이 질문은 소위 "안전한 채권을 찾는 것 같습니다 (따옴표가 안 닫힘)\n'
        '{"category": ["domestic_bond"], "logical_verification": [], "numerical_verification": {}}'
    )
    result = _extract_json(text)
    return isinstance(result, dict) and result.get("category") == ["domestic_bond"]


CASES = [
    ("1. 정상 JSON 하나", case_1_single_json),
    ("2. 앞에 해설 문장 + JSON", case_2_leading_prose),
    ("3. JSON + 뒤에 해설 문장", case_3_trailing_prose),
    ("4. JSON 두 개 (사이에 텍스트) → 마지막 것", case_4_two_json_objects),
    ("5. 코드펜스", case_5_single_code_fence),
    ("6. 코드펜스 두 개 → 마지막 것", case_6_two_code_fences),
    ("7. numerical_verification 중첩 객체", case_7_nested_numerical_verification),
    ("8. 문자열 값 안에 중괄호 포함", case_8_braces_inside_string_value),
    ("9. 이스케이프된 따옴표 보존", case_9_escaped_quotes_preserved),
    ("10. JSON 없는 순수 텍스트 → None", case_10_no_json_at_all),
    ("11. 빈 문자열 / None → None", case_11_empty_or_none),
    ("12. 중괄호 안 닫힌 조각 → None (예외 아님)", case_12_unterminated_fragment),
    ("13. 선행 해설에 짝 안 맞는 '{' 섞임 (회귀)", case_13_unmatched_brace_in_preamble),
    ("14. 중첩 하위 객체가 부모를 밀어내면 안 됨 (회귀)", case_14_nested_child_must_not_shadow_parent),
    ("15. 선행 해설에 짝 안 맞는 큰따옴표 섞임 (회귀)", case_15_unmatched_quote_in_preamble),
]


def main():
    # 먼저 "수정 전 구현이 4번·6번에서 실제로 실패하는지"를 보인다 (qp_task9_json.md:
    # "수정 전 코드로 돌려 실패하는 것을 먼저 확인하고, 수정 후 통과시킬 것").
    print("=" * 60)
    print("수정 전(_OLD_extract_json_for_comparison) 4번·6번 재현 — 실패해야 정상")
    print("=" * 60)
    old_4 = _OLD_extract_json_for_comparison(_TWO_JSON_WITH_TEXT_BETWEEN)
    old_6 = _OLD_extract_json_for_comparison(_TWO_CODE_FENCES)
    print(f"[구버전] 4번(JSON 두 개) 결과: {old_4!r}  {'(예상대로 실패/오작동)' if old_4 is None or (isinstance(old_4, dict) and old_4.get('category') != ['domestic_bond']) else '(어? 성공함 — 재확인 필요)'}")
    print(f"[구버전] 6번(코드펜스 두 개) 결과: {old_6!r}  {'(예상대로 실패/오작동)' if old_6 is None or (isinstance(old_6, dict) and old_6.get('category') != ['domestic_bond']) else '(어? 성공함 — 재확인 필요)'}")
    print()

    print("=" * 60)
    print("새 구현(agents.query_parser._extract_json) — 12개 픽스처")
    print("=" * 60)
    results = []
    for name, fn in CASES:
        try:
            ok = bool(fn())
        except Exception as e:
            ok = False
            name = f"{name}  [예외: {type(e).__name__}: {e}]"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")
        results.append(ok)

    passed, failed = sum(results), len(results) - sum(results)
    print("=" * 60)
    print(f"{passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
