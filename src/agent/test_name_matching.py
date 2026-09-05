"""
================================================================================
test_name_matching.py — 상품명 부분 매칭 실패(국민성장펀드 0건) 단위 테스트
(Task 17, qp_task17_name_matching.md §4.1/§4.2/§4.3)
--------------------------------------------------------------------------------
실제 LLM은 호출하지 않는다. §4.3(실제 DB 조회)만은 진짜 DB를 읽기 전용으로 연다 —
이 제출 레포(financial_product_agent)엔 아직 DB 파일이 없고(네이버클라우드 이전
예정), 실제 데이터는 개발 레포 쪽(2026triangle/data/db/finance_master.db)에 있다.
그 파일이 있는 경우에만 §4.3을 실행하고, 없으면 건너뛴다(크래시하지 않음) — 어느
경로든 이 파일을 옮겨 실행해도 안전하다.

§4.1 — decompose_product_name_tokens() / _broaden_name_filters() 순수 함수 테스트
       (§1 진단 결과 그대로 반영: "국민성장펀드" → 접미사 제거만으로 "국민성장" 1토큰)
§4.2 — build_filter_clauses()의 ProductName LIKE 분기: 정식명 OR 약칭(§3), 토큰
       분해 시 AND(§2.1)
§4.3 — TextToSQLAgent.query() 통합(가짜 LLM, 실제 DB 읽기 전용) — 0건 → 토큰 분해
       재검색 → 실제로 0건이 아님을 확인

실행: cd src/agent && python test_name_matching.py
================================================================================
"""
import os
import re
import sys

# §4.3 전용 — 실제 DB 위치를 찾아 FINANCE_DB_PATH를 세팅한다(있으면). config.py를
# import하기 전에 해야 하므로 다른 import보다 먼저 온다. 없으면 그냥 두고 §4.3에서
# 건너뛴다.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CANDIDATE_DB_PATHS = [
    os.path.normpath(os.path.join(_THIS_DIR, "..", "data", "db", "finance_master.db")),
    os.path.normpath(os.path.join(_THIS_DIR, "..", "..", "..", "data", "db", "finance_master.db")),
]
_REAL_DB_PATH = next((p for p in _CANDIDATE_DB_PATHS if os.path.exists(p)), None)
if _REAL_DB_PATH:
    os.environ.setdefault("FINANCE_DB_PATH", _REAL_DB_PATH)

from agents.text_to_sql_agent import (  # noqa: E402
    TextToSQLAgent,
    build_filter_clauses,
    decompose_product_name_tokens,
    _broaden_name_filters,
)


# --------------------------------------------------------------------------
# §4.1 — decompose_product_name_tokens() 순수 함수 테스트
# --------------------------------------------------------------------------
_DECOMPOSE_CASES = [
    # (입력, 기대 토큰 목록, 설명)
    ("국민성장펀드", ["국민성장"], "§1 진단 재현 — 접미사만 떼면 1토큰(AND 없이 단일 LIKE)"),
    ("삼성전자", ["삼성전자"], "접미사·공백 없음 — 그대로"),
    ("KODEX AI로봇 ETF", ["KODEX", "AI로봇"], "접미사(ETF) 제거 + 공백 분해"),
    ("가나다 라마바 사아자 차카타", ["가나다", "라마바", "사아자"], "토큰 4개 → 앞 3개만"),
    ("펀드", ["펀드"], "접미사=원문 전체 — 제거하면 빈 문자열이라 그대로 둔다"),
    # 교차검증 라운드 1에서 지적된 회귀 — 원문 전체가 "복합" 접미사와 완전히 같으면
    # (상장지수펀드/증권투자신탁 자체), 그 접미사 자체는 len(name)>len(suf) 조건에
    # 걸려 건너뛰면서도 그 안에 포함된 더 짧은 접미사(펀드/투자신탁)에는 걸려버려
    # "상장지수"/"증권"처럼 엉뚱하게 잘렸었다. 이제는 전체 일치를 먼저 검사해 그대로 둔다.
    ("상장지수펀드", ["상장지수펀드"], "회귀: 복합 접미사 자체 — '상장지수'로 잘리면 안 됨"),
    ("증권투자신탁", ["증권투자신탁"], "회귀: 복합 접미사 자체 — '증권'으로 잘리면 안 됨"),
    # 교차검증 라운드 3에서 지적된 회귀 — "인도"/"대만"처럼 조사 목록의 글자로 끝나는
    # 2글자 고유명사를 조사 제거 로직이 1글자("인"/"대")로 잘라버리면, 그 한 글자가
    # "인버스"/"인프라" 등 무관한 상품명에도 부분 일치해 검색 정밀도가 무너진다.
    ("인도 펀드", ["인도"], "회귀: 2글자 국가명이 조사처럼 보여도 1글자로 안 잘림"),
    ("대만 ETF", ["대만"], "회귀: '대만'도 '만'이 조사 목록에 있지만 안 잘림"),
]


def run_decompose_checks() -> bool:
    print("=" * 60)
    print(f"§4.1 decompose_product_name_tokens() — {len(_DECOMPOSE_CASES)}개 케이스")
    print("=" * 60)
    results = []
    for name, expected, desc in _DECOMPOSE_CASES:
        got = decompose_product_name_tokens(name)
        ok = got == expected
        print(f"[{'PASS' if ok else 'FAIL'}] {desc}: {name!r} → {got!r} (기대 {expected!r})")
        results.append(ok)
    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# §4.1 — _broaden_name_filters() 순수 함수 테스트
# --------------------------------------------------------------------------
def run_broaden_filters_checks() -> bool:
    print("=" * 60)
    print("§4.1 _broaden_name_filters()")
    print("=" * 60)
    results = []

    name_filter = {
        "column": None, "op": "LIKE", "values": ["국민성장펀드"],
        "source": "user_text", "concept": "ProductName",
        "category": None, "table": None,
    }
    other_filter = {"column": None, "op": "=", "values": ["x"], "concept": "Other"}

    broadened = _broaden_name_filters([other_filter, name_filter])
    ok = broadened is not None
    if ok:
        name, tokens, new_filters = broadened
        ok = ok and name == "국민성장펀드" and tokens == ["국민성장"]
        ok = ok and new_filters[0] is other_filter  # 다른 필터는 그대로
        ok = ok and new_filters[1]["values"] == ["국민성장"]
        ok = ok and new_filters[1]["concept"] == "ProductName"
    print(f"[{'PASS' if ok else 'FAIL'}] ProductName 필터를 찾아 토큰으로 교체")
    results.append(ok)

    # 상품명 필터가 아예 없으면 None
    ok2 = _broaden_name_filters([other_filter]) is None
    print(f"[{'PASS' if ok2 else 'FAIL'}] 상품명 필터 없음 → None")
    results.append(ok2)

    # 분해해도 원래 문구와 동일하면(접미사·공백 없는 이름) 재시도 무의미 → None
    unchanged_filter = dict(name_filter, values=["삼성전자"])
    ok3 = _broaden_name_filters([unchanged_filter]) is None
    print(f"[{'PASS' if ok3 else 'FAIL'}] 분해해도 원문과 동일 → None(재시도 안 함)")
    results.append(ok3)

    # 회귀(교차검증 라운드 2에서 지적) — ProductName 필터가 여러 개고 앞쪽이 분해
    # 불가(삼성전자)여도, 뒤쪽에 분해 가능한 필터(국민성장펀드)가 있으면 그걸 찾아야
    # 한다. 예전엔 앞쪽에서 바로 return None 해서 뒤쪽을 아예 못 봤다.
    broadened2 = _broaden_name_filters([unchanged_filter, name_filter])
    ok4 = broadened2 is not None
    if ok4:
        name2, tokens2, new_filters2 = broadened2
        ok4 = ok4 and name2 == "국민성장펀드" and tokens2 == ["국민성장"]
        ok4 = ok4 and new_filters2[0] is unchanged_filter  # 분해 불가 필터는 그대로
        ok4 = ok4 and new_filters2[1]["values"] == ["국민성장"]
    print(f"[{'PASS' if ok4 else 'FAIL'}] 앞쪽 필터가 분해 불가여도 뒤쪽 분해 가능 필터를 찾음")
    results.append(ok4)

    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# §4.2 — build_filter_clauses()의 ProductName LIKE 분기
# --------------------------------------------------------------------------
def run_build_filter_clauses_checks() -> bool:
    print("=" * 60)
    print("§4.2 build_filter_clauses() — ProductName LIKE 분기")
    print("=" * 60)
    results = []

    # 단일 토큰(평소 경로) — 정식명 OR 약칭, AND 없음
    single = {
        "column": None, "op": "LIKE", "values": ["국민성장펀드"],
        "source": "user_text", "concept": "ProductName",
        "category": None, "table": None,
    }
    clauses = build_filter_clauses([single], "tb_fund")
    expected = ["(itm_nm LIKE '%국민성장펀드%' OR itm_abrv_nm LIKE '%국민성장펀드%')"]
    ok = clauses == expected
    print(f"[{'PASS' if ok else 'FAIL'}] 단일 토큰(§3 정식명 OR 약칭): {clauses!r}")
    results.append(ok)

    # 다중 토큰(§2.1 토큰 분해 재검색) — 각 토큰의 OR절을 AND로 묶는다
    multi = dict(single, values=["국민", "성장"])
    clauses = build_filter_clauses([multi], "tb_fund")
    expected = [
        "(itm_nm LIKE '%국민%' OR itm_abrv_nm LIKE '%국민%') AND "
        "(itm_nm LIKE '%성장%' OR itm_abrv_nm LIKE '%성장%')"
    ]
    ok = clauses == expected
    print(f"[{'PASS' if ok else 'FAIL'}] 다중 토큰(§2.1 AND 매칭): {clauses!r}")
    results.append(ok)

    # ETF 테이블도 정식명/약칭 컬럼이 pd_nm/pd_abrv_nm으로 올바르게 라우팅되는지
    clauses = build_filter_clauses([dict(single, values=["KODEX AI로봇"])], "tb_domestic_etf")
    expected = ["(pd_nm LIKE '%KODEX AI로봇%' OR pd_abrv_nm LIKE '%KODEX AI로봇%')"]
    ok = clauses == expected
    print(f"[{'PASS' if ok else 'FAIL'}] ETF 테이블 정식명/약칭 컬럼 라우팅: {clauses!r}")
    results.append(ok)

    # 회귀(교차검증 라운드 3) — PRODUCT_NAME_COLUMNS는 CONCEPT_COLUMN_MAP과 별도
    # 상수라 두 맵이 어긋날 수 있다. PRODUCT_NAME_COLUMNS에 테이블이 없어도
    # CONCEPT_COLUMN_MAP에 ProductName 컬럼이 있으면 그 컬럼 하나만으로라도(약칭 OR
    # 없이) 검색해야 한다 — 완전히 건너뛰어 조건 없는 전체 조회가 나가면 안 된다.
    import agents.text_to_sql_agent as tsa_mod
    saved = tsa_mod.PRODUCT_NAME_COLUMNS.pop("tb_fund")
    try:
        clauses = build_filter_clauses([single], "tb_fund")
    finally:
        tsa_mod.PRODUCT_NAME_COLUMNS["tb_fund"] = saved
    # name_cols가 (col,) 1-튜플이어도 OR절 조립 경로를 그대로 타므로 괄호는 남는다 —
    # 컬럼이 하나뿐이라 실질적으로는 단일 LIKE와 동일하다.
    ok = clauses == ["(itm_nm LIKE '%국민성장펀드%')"]
    print(f"[{'PASS' if ok else 'FAIL'}] PRODUCT_NAME_COLUMNS 누락 시 "
          f"CONCEPT_COLUMN_MAP 단일 컬럼으로 폴백(완전히 건너뛰지 않음): {clauses!r}")
    results.append(ok)

    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# §4.2/§4.3 — TextToSQLAgent.query() 통합 (가짜 LLM, 가능하면 실제 DB 읽기 전용)
# --------------------------------------------------------------------------
class _FakeSqlModelManager:
    """실제 LLM처럼 '[이미 확정된 조건]을 WHERE절에 그대로 포함'하는 동작만 흉내낸다
    (PROMPT_TEMPLATE의 고정 섹션 구조를 그대로 파싱) — 그 외 추론은 하지 않는다."""

    def __init__(self):
        self.call_count = 0

    def generate_text(self, prompt, *args, **kwargs):
        self.call_count += 1
        table = re.search(r"\[대상 테이블\]\n(.+)", prompt).group(1).strip()
        m = re.search(r"\[이미 확정된 조건.*?\]\n(.*?)\n\n\[질의", prompt, re.DOTALL)
        clauses_block = m.group(1).strip() if m else "(없음)"
        if clauses_block == "(없음)":
            return f"SELECT * FROM {table}"
        where = " AND ".join(line.strip() for line in clauses_block.splitlines() if line.strip())
        return f"SELECT * FROM {table} WHERE {where}"


def run_end_to_end_checks() -> bool:
    print("=" * 60)
    print("§4.2/§4.3 TextToSQLAgent.query() 통합 — 가짜 LLM")
    print("=" * 60)
    results = []

    filters = [{
        "column": None, "op": "LIKE", "values": ["국민성장펀드"],
        "source": "user_text", "concept": "ProductName",
        "category": None, "table": None,
    }]

    if _REAL_DB_PATH is None:
        print("[건너뜀] 실제 DB 파일을 찾지 못해 §4.3(실제 DB 조회)은 생략한다 — "
              "이 제출 레포엔 DB가 아직 없다(네이버클라우드 이전 예정). "
              "SQL 생성 자체(§4.2)는 아래에서 별도로 확인한다.")
        # DB 없이도 확인 가능한 것 — 원본 문구 SQL이 §0 버그 리포트와 일치하는지만 검증.
        mm = _FakeSqlModelManager()
        agent = TextToSQLAgent(mm)
        clauses = build_filter_clauses(filters, "tb_fund")
        ok = clauses == ["(itm_nm LIKE '%국민성장펀드%' OR itm_abrv_nm LIKE '%국민성장펀드%')"]
        print(f"[{'PASS' if ok else 'FAIL'}] 원본 문구 WHERE절 형태 확인: {clauses!r}")
        results.append(ok)
    else:
        print(f"[DB] 실제 DB 사용: {_REAL_DB_PATH}")
        mm = _FakeSqlModelManager()
        agent = TextToSQLAgent(mm)
        result = agent.query(
            original_query="국민성장펀드의 구조와 투자전략 동향 등 찾아서 알려줘",
            table="tb_fund",
            filters=filters,
            table_results={},
        )
        print(f"  1차 시도 이후 최종 결과: sql={result.get('sql')!r}")
        print(f"  row_count={result.get('row_count')}, "
              f"name_match_retry={result.get('name_match_retry')!r}, "
              f"name_not_found={result.get('name_not_found')!r}")

        ok = result.get("row_count", 0) > 0
        print(f"[{'PASS' if ok else 'FAIL'}] §4.3 실제 DB 조회 — 0건이 아님(재검색 후)")
        results.append(ok)

        ok2 = "토큰 분해 재검색" in (result.get("name_match_retry") or "")
        print(f"[{'PASS' if ok2 else 'FAIL'}] 재시도가 실제로 발생했음이 기록됨")
        results.append(ok2)

        # mm.call_count: 1차 시도(1회) + 재시도(1회) = 2회 이상(SQL 추출 실패 재시도가
        # 없었다는 전제하에 정확히 2회). 최소 2회 이상은 되어야 한다(재시도가 실제로
        # LLM을 다시 불렀는지 확인).
        ok3 = mm.call_count >= 2
        print(f"[{'PASS' if ok3 else 'FAIL'}] LLM 재호출 발생(call_count={mm.call_count})")
        results.append(ok3)

    passed, failed = sum(results), len(results) - sum(results)
    print(f"-- {passed} passed, {failed} failed --\n")
    return failed == 0


# --------------------------------------------------------------------------
# 회귀(교차검증 라운드 3) — 재시도 자체가 실패(SQL 생성/실행 실패)하면 "0건"이 아니라
# "실행 실패"다. name_not_found("찾지 못했습니다")를 잘못 붙이면 안 된다.
# --------------------------------------------------------------------------
def run_retry_failure_checks() -> bool:
    print("=" * 60)
    print("회귀: 토큰 분해 재시도 자체가 실패하면 name_not_found를 붙이지 않음")
    print("=" * 60)

    agent = TextToSQLAgent(None)  # _generate_and_execute를 직접 바꿔치기하므로 mm은 안 씀
    calls = []

    def fake_generate_and_execute(original_query, table, clauses, table_results):
        calls.append(clauses)
        if len(calls) == 1:
            # 1차 시도 — 실행은 성공했지만 0건(원본 문구 그대로는 안 걸림)
            return {
                "table": table, "sql": "SELECT * FROM tb_fund WHERE ...",
                "columns": [], "rows": [], "row_count": 0, "attempts": 1,
            }
        # 2차 시도(토큰 분해 재검색) — SQL 생성/실행 자체가 실패
        return {
            "table": table, "sql": None, "columns": [], "rows": [], "row_count": 0,
            "error": "SQL 실행 실패 시뮬레이션",
        }

    agent._generate_and_execute = fake_generate_and_execute
    filters = [{
        "column": None, "op": "LIKE", "values": ["국민성장펀드"],
        "source": "user_text", "concept": "ProductName",
        "category": None, "table": None,
    }]
    result = agent.query("국민성장펀드 알려줘", "tb_fund", filters, {})

    ok = len(calls) == 2  # 재시도가 실제로 일어났는지
    ok = ok and "name_not_found" not in result
    ok = ok and result.get("error") == "SQL 실행 실패 시뮬레이션"
    print(f"[{'PASS' if ok else 'FAIL'}] 재시도 실행 실패 → name_not_found 없음, error 보존: {result!r}")
    print(f"-- {1 if ok else 0} passed, {0 if ok else 1} failed --\n")
    return ok


def main():
    ok1 = run_decompose_checks()
    ok2 = run_broaden_filters_checks()
    ok3 = run_build_filter_clauses_checks()
    ok4 = run_end_to_end_checks()
    ok5 = run_retry_failure_checks()
    sys.exit(0 if (ok1 and ok2 and ok3 and ok4 and ok5) else 1)


if __name__ == "__main__":
    main()
