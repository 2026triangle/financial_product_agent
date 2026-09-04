"""
extract_bond_category.py — tb_domestic_bond 채권분류 3계층(DAG) 추출
사용: python3 extract_bond_category.py <db경로> [출력json경로]
출력 기본값: vocab/bond_category_hierarchy.json (이 스크립트와 같은 디렉터리 아래)

db경로에 기본값이 없다 — 원본 SQLite(data/db/finance_master.db)는 이 제출 레포로
옮기지 않았고 데이터는 네이버클라우드 DB로 이전된다(Task 6 범위 밖). db경로 인자 없이
실행하면 사용법만 출력하고 종료한다.

산출물 vocab/bond_category_hierarchy.json은 이미 레포에 커밋되어 있으므로,
심사·재현 목적으로는 이 스크립트를 다시 돌릴 필요가 없다 — DB 접근 없이도 결과를 확인할 수 있다.

DB에는 읽기 전용 SELECT DISTINCT/GROUP BY만 1회 실행한다(배치 재적재·폴링 없음).
계층은 트리가 아니라 DAG다 — 부모가 여럿인 노드가 있고(다부모), 레벨 간 동명이인도 있다
(ot_instructions/ot_task4.md §1 참고). 이 스크립트는 그 사실을 있는 그대로 추출만 하고
임의로 "정리"하지 않는다.
"""
import json
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_PATH = SCRIPT_DIR / "vocab" / "bond_category_hierarchy.json"


def _print_usage_and_exit() -> None:
    print(__doc__.strip())
    print(
        f"\nDB 경로가 필요합니다. 예: python3 {Path(__file__).name} "
        f"/path/to/finance_master.db"
    )
    sys.exit(1)

TABLE = "tb_domestic_bond"
LEVELS = [
    ("major", "std_pd_mcls_nm", None),      # 최상위 — 부모 없음
    ("mid", "std_pd_scls_nm", "major"),     # 부모 레벨: 대분류
    ("minor", "bd_knd", "mid"),             # 부모 레벨: 중분류
]


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def extract(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    # 레벨별 값-카운트 (해당 레벨 컬럼 기준 전체 행 수)
    counts_by_level = {}
    for level, column, _ in LEVELS:
        cur.execute(
            f"SELECT {column}, COUNT(*) FROM {TABLE} "
            f"WHERE {column} IS NOT NULL AND TRIM({column}) <> '' "
            f"GROUP BY {column}"
        )
        counts_by_level[level] = {row[0]: row[1] for row in cur.fetchall()}

    # 3컬럼 조합(대분류,중분류,소분류) — 부모/자식 관계는 여기서만 유도한다.
    cur.execute(f"SELECT DISTINCT std_pd_mcls_nm, std_pd_scls_nm, bd_knd FROM {TABLE}")
    combos = cur.fetchall()
    conn.close()

    # level_value -> set(parent_value)  (NULL 부모는 제외, 노드 자체는 유지)
    parents_by_level = {"major": {}, "mid": {}, "minor": {}}
    col_index = {"major": 0, "mid": 1, "minor": 2}

    for level, _column, parent_level in LEVELS:
        idx = col_index[level]
        parent_idx = col_index[parent_level] if parent_level else None
        for combo in combos:
            value = combo[idx]
            if _is_blank(value):
                continue
            parents_by_level[level].setdefault(value, set())
            if parent_idx is not None:
                parent_value = combo[parent_idx]
                if not _is_blank(parent_value):
                    parents_by_level[level][value].add(parent_value)

    nodes = []
    for level, _column, _parent_level in LEVELS:
        level_values = sorted(
            parents_by_level[level].keys(),
            key=lambda v: (-counts_by_level[level].get(v, 0), v),
        )
        for value in level_values:
            nodes.append({
                "level": level,
                "value": value,
                "parents": sorted(parents_by_level[level][value]),
                "count": counts_by_level[level].get(value, 0),
            })

    return {
        "source_table": TABLE,
        "levels": {"major": "std_pd_mcls_nm", "mid": "std_pd_scls_nm", "minor": "bd_knd"},
        "nodes": nodes,
    }


def report(result: dict) -> None:
    nodes = result["nodes"]
    by_level = {"major": 0, "mid": 0, "minor": 0}
    multi_parent = {"major": 0, "mid": 0, "minor": 0}
    for n in nodes:
        by_level[n["level"]] += 1
        if len(n["parents"]) > 1:
            multi_parent[n["level"]] += 1

    # 레벨 간 동명이인 탐지
    value_to_levels = {}
    for n in nodes:
        value_to_levels.setdefault(n["value"], set()).add(n["level"])
    cross_level_dupes = {v: sorted(ls) for v, ls in value_to_levels.items() if len(ls) > 1}

    print(f"노드 총 {len(nodes)}개  (major={by_level['major']}, mid={by_level['mid']}, minor={by_level['minor']})")
    print(f"다부모 노드: mid={multi_parent['mid']}건, minor={multi_parent['minor']}건")
    print(f"레벨 간 동명이인 {len(cross_level_dupes)}건: {cross_level_dupes}")


def main():
    if len(sys.argv) <= 1:
        _print_usage_and_exit()
    db_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_OUT_PATH)

    result = extract(db_path)
    report(result)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"-> saved {out_path}")


# =============================================================================
# common.ttl에 붙여넣을 인스턴스 블록 생성 — Revised Romanization 기반 결정론적 URI 부여.
# 손으로 61개 URI를 짓지 않도록 돕는 저작 보조용이다(common.ttl 자체는 손으로 관리).
# `python3 extract_bond_category.py --ttl` 로 블록을 stdout에 출력한다.
# =============================================================================
_INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_MEDIALS = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
# 받침(종성)은 뒤에 자음이 오거나 어말일 때의 대표 발음으로 로마자화한다(공식 RR 규칙).
# 예: 국+고 -> 'guk'+'go' (o가 아니라 받침 그대로) -> "Gukgochae" (ot_v1.md 예시와 일치)
_FINALS = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "p", "l", "l", "p", "l",
           "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]

LEVEL_PREFIX = {"major": "BondCatM", "mid": "BondCatS", "minor": "BondCatK"}
LEVEL_COLUMN = {"major": "std_pd_mcls_nm", "mid": "std_pd_scls_nm", "minor": "bd_knd"}

# 자명한 동의어만 최소로 (data/AGENTS.md: 억지 변형 지양). ot_v1.md §3 예시 그대로.
_OBVIOUS_SYNONYMS = {
    ("minor", "국고채권"): ["국채"],
}


def _romanize(word: str) -> str:
    out = []
    for ch in word:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            final = code % 28
            code //= 28
            medial = code % 21
            code //= 21
            initial = code
            out.append(_INITIALS[initial] + _MEDIALS[medial] + _FINALS[final])
        elif ch.isalnum():
            out.append(ch.lower())
        # 공백/괄호/마침표 등 나머지는 버린다 (turtle 로컬명 안전성)
    s = "".join(out)
    return s[:1].upper() + s[1:] if s else s


def _ttl_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_uri_index(nodes):
    """(level, value) -> 'BondCatX_Romanized' 로컬명. 레벨 내 충돌 시 숫자 접미사로 방지."""
    index = {}
    used_by_level = {"major": set(), "mid": set(), "minor": set()}
    for n in nodes:
        base = f"{LEVEL_PREFIX[n['level']]}_{_romanize(n['value'])}"
        candidate = base
        i = 2
        while candidate in used_by_level[n["level"]]:
            candidate = f"{base}{i}"
            i += 1
        used_by_level[n["level"]].add(candidate)
        index[(n["level"], n["value"])] = candidate
    return index


def generate_ttl_block(result: dict) -> str:
    nodes = result["nodes"]
    uri = build_uri_index(nodes)
    parent_level = {"major": None, "mid": "major", "minor": "mid"}

    lines = [
        "# =============================================================================",
        "# 5-E. 통제 어휘 — 채권분류 (tb_domestic_bond 3계층, 트리 아님 — DAG)",
        "#      대분류(std_pd_mcls_nm 6) > 중분류(std_pd_scls_nm 16) > 소분류(bd_knd 39) = 61개",
        "#      다부모 노드 있음(중분류 3건, 소분류 10건) — 원천 데이터의 사실이므로 트리로 '고치지' 않는다.",
        "#      레벨 간 동명이인 2건(특수은행채: 중분류/소분류, 개인투자용국채: 대분류/소분류) 존재 →",
        "#      URI에 레벨 접두어(BondCatM/BondCatS/BondCatK) 필수.",
        "#      추출: extract_bond_category.py --ttl (원본 JSON: vocab/bond_category_hierarchy.json)",
        "# =============================================================================",
    ]

    for n in nodes:
        local = uri[(n["level"], n["value"])]
        plevel = parent_level[n["level"]]
        parent_locals = [uri[(plevel, p)] for p in n["parents"]] if plevel else []

        parts = [f"fp:{local} a fp:BondCategory ;"]
        parts.append(f'    rdfs:label "{_ttl_escape(n["value"])}"@ko ;')
        parts.append(f'    fp:categoryLevel "{n["level"]}" ;')
        parts.append(f'    fp:sourceColumn "{LEVEL_COLUMN[n["level"]]}" ;')

        syns = _OBVIOUS_SYNONYMS.get((n["level"], n["value"]))
        has_parents = bool(parent_locals)
        has_syns = bool(syns)

        vc_end = ";" if (has_parents or has_syns) else "."
        parts.append(f'    fp:valueCode "tb_domestic_bond:{_ttl_escape(n["value"])}" {vc_end}')

        if has_parents:
            pc_end = ";" if has_syns else "."
            pc_values = ", ".join(f"fp:{p}" for p in parent_locals)
            parts.append(f"    fp:parentCategory {pc_values} {pc_end}")

        if has_syns:
            syn_str = ", ".join(f'"{_ttl_escape(s)}"' for s in syns)
            parts.append(f"    fp:hasSynonym {syn_str} .")

        lines.append("\n".join(parts))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    if "--ttl" in sys.argv:
        db_path = None
        for a in sys.argv[1:]:
            if a != "--ttl":
                db_path = a
                break
        if db_path is None:
            _print_usage_and_exit()
        _result = extract(str(db_path))
        print(generate_ttl_block(_result), end="")
    else:
        main()
