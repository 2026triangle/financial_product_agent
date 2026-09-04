"""
================================================================================
build_ttl_from_meta.py — db/meta_columns/*.json → 도메인 TTL 생성기 (Task 1)
--------------------------------------------------------------------------------
scripts/ontology/meta_columns/*.json 의 203개 컬럼 메타데이터를 owl:DatatypeProperty로
기계적으로 변환해 bond_kr.ttl / etf_kr.ttl / etf_gl.ttl / fund_pub.ttl 을 덮어쓴다.

변환 규칙 (ONTOLOGY_SPEC.md §4)
  column_name        → fp:속성명            (snake_case → camelCase)
  column_korean_name → rdfs:label ...@ko
  data_type          → rdfs:range           (xsd:string/decimal/integer/date)
  "<table>.<column>" → fp:sourceColumn
  synonyms            → fp:hasSynonym (A그룹만, 복수 값)
  description         → rdfs:comment ...@ko

같은 컬럼명이 여러 테이블에 동시에 존재하면(예: cu_charge_rt는 국내/해외ETF 공통)
가장 먼저 등장하는 도메인 파일에서 한 번만 정의하고, domain은 두 테이블의
공통 상위 클래스(fp:ETF, fp:Product)로 넓힌다. 나머지 파일은 그 정의를 그대로
재사용하며 중복 선언하지 않는다 — 이게 "완료 조건: 중복 정의된 속성 없음"이다.

실행: 레포 루트에서 python3 scripts/ontology/build_ttl_from_meta.py
     (입력: scripts/ontology/meta_columns/, 출력: 레포 루트의 ontology/)
검증: python3 -c "import rdflib; g=rdflib.Graph(); [g.parse(f) for f in ...]"
================================================================================
"""
import json
import re
from pathlib import Path
from collections import OrderedDict

SCRIPT_DIR = Path(__file__).resolve().parent          # repo/scripts/ontology
META_COLUMNS_DIR = SCRIPT_DIR / "meta_columns"
OUTPUT_DIR = SCRIPT_DIR.parents[1] / "ontology"       # repo/ontology (대회 제출 규격 트리)

# -----------------------------------------------------------------------------
# 테이블 ↔ 도메인 파일 ↔ 클래스 매핑 (실측: ONTOLOGY_SPEC.md §2.1, common.ttl §1)
# -----------------------------------------------------------------------------
TABLE_ORDER = [
    "tb_domestic_bond",
    "tb_domestic_etf",
    "tb_foreign_etf",
    "tb_fund",
    "tb_fund_attribute",
]

TABLE_TO_FILE = {
    "tb_domestic_bond": "bond_kr.ttl",
    "tb_domestic_etf": "etf_kr.ttl",
    "tb_foreign_etf": "etf_gl.ttl",
    "tb_fund": "fund_pub.ttl",
    "tb_fund_attribute": "fund_pub.ttl",
}

TABLE_TO_CLASS = {
    "tb_domestic_bond": "Bond",
    "tb_domestic_etf": "DomesticETF",
    "tb_foreign_etf": "ForeignETF",
    "tb_fund": "Fund",
    "tb_fund_attribute": "AttributeTag",
}

FILE_TITLES = {
    "bond_kr.ttl": "국내채권 온톨로지",
    "etf_kr.ttl": "국내ETF 온톨로지",
    "etf_gl.ttl": "해외ETF 온톨로지",
    "fund_pub.ttl": "공모펀드 온톨로지",
}

# 국내/해외 ETF가 같은 컬럼을 공유하면 공통 상위클래스 fp:ETF를 domain으로 쓴다.
ETF_TABLES = {"tb_domestic_etf", "tb_foreign_etf"}
# Bond/ETF(국내+해외)/Fund 는 전부 fp:Product 하위클래스라 셋 이상 걸치면 fp:Product.
PRODUCT_TABLES = {"tb_domestic_bond", "tb_domestic_etf", "tb_foreign_etf", "tb_fund"}

# -----------------------------------------------------------------------------
# synonym 차등 — data/AGENTS.md §4 (핵심 비즈니스 컬럼 A그룹 / 식별자 B그룹)
# 명시된 예시는 그대로 쓰고, 나머지 컬럼은 접미사·키워드 휴리스틱으로 분류한다.
# 애매하면 B그룹(synonym 생략)으로 떨어뜨린다 — "억지 변형 지양" 원칙(AGENTS.md).
# -----------------------------------------------------------------------------
A_GROUP_EXPLICIT = {
    # 수익률 / 금리
    "buy_yield", "after_tax_yield", "fd_yr1_ern_r", "du_er_1y", "pd_dvid_yield",
    # 신용도 / 안전도 / 위험도
    "crd_grd", "pd_risk_gcd", "pd_pen_risk_nm", "zrin_fd_ivst_risk_grd_nm",
    # 수수료 / 비용
    "cu_charge_rt", "cu_charge_etc_rt",
    # 자산 규모 / 인기
    "fd_nast_suma", "pd_net_tamt", "du_last_aum", "isu_bal_amt",
    # 연금 / 절세
    "pd_pen_tr_yn", "pref_tax_yield", "exchdg_yn",
    # 전략 / 테마
    "cu_strtegy", "cu_base_index", "wu_inv_ast_type", "wu_inv_rgn",
    "or_attr_desc", "prfd_attr_cd",
}

B_GROUP_EXPLICIT = {
    "pd_no", "pd_itm_no", "pd_isin_cd", "ksd_itm_no", "fss_itm_no",
    "pd_us_cik", "curr_cd", "pd_exg_mkt_cd",
    "itm_no", "std_itm_no", "pd_itm_no_ma",
}

# 식별자/규격/날짜/시스템 플래그류 접미사 → B그룹 기본값
B_GROUP_SUFFIXES = ("_no", "_cd", "_id", "_dt", "_ma", "_cik", "_yn")

# 수익률·위험·비용·규모·테마성 키워드가 들어있으면 A그룹으로 승격
A_GROUP_KEYWORDS = (
    "yield", "ern_r", "er_1", "er_3", "er_6", "er_ytd", "dvid", "charge",
    "risk", "crd_grd", "grd_nm", "strtegy", "aum", "nast", "tamt", "bal_amt",
    "pen", "tax", "hdge", "ast_type", "inv_rgn", "attr_desc", "irt",
)


def classify_group(column_name: str) -> str:
    """컬럼을 synonym A그룹/B그룹으로 분류한다. (data/AGENTS.md §4 준수)"""
    if column_name in A_GROUP_EXPLICIT:
        return "A"
    if column_name in B_GROUP_EXPLICIT:
        return "B"
    if column_name.endswith(B_GROUP_SUFFIXES):
        return "B"
    if any(kw in column_name for kw in A_GROUP_KEYWORDS):
        return "A"
    return "B"


# -----------------------------------------------------------------------------
# snake_case → camelCase / data_type → xsd range
# -----------------------------------------------------------------------------
def snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def infer_range(data_type: str) -> str:
    dt = (data_type or "").upper()
    if dt.startswith(("NUMERIC", "DECIMAL", "FLOAT", "DOUBLE", "REAL")):
        return "xsd:decimal"
    if dt.startswith(("INTEGER", "INT", "BIGINT", "SMALLINT")):
        return "xsd:integer"
    if dt.startswith("TIMESTAMP") or "DATETIME" in dt:
        return "xsd:dateTime"
    if dt.startswith("DATE"):
        return "xsd:date"
    return "xsd:string"


def ttl_escape(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text.strip()


# -----------------------------------------------------------------------------
# 1단계: db/meta_columns/*.json 적재
# -----------------------------------------------------------------------------
def load_meta_columns():
    per_table = {}
    for table in TABLE_ORDER:
        path = META_COLUMNS_DIR / f"{table}.json"
        per_table[table] = json.loads(path.read_text(encoding="utf-8"))
    return per_table


# -----------------------------------------------------------------------------
# 2단계: 컬럼명(camelCase 기준) 단위로 테이블 전역 중복을 감지하고,
#        어느 파일이 "소유"할지 · domain 클래스가 무엇일지 결정한다.
# -----------------------------------------------------------------------------
def build_property_index(per_table):
    # prop_name → [(table, column_entry), ...]  (TABLE_ORDER 순서로 누적)
    occurrences = OrderedDict()
    for table in TABLE_ORDER:
        for entry in per_table[table]:
            prop_name = snake_to_camel(entry["column_name"])
            occurrences.setdefault(prop_name, []).append((table, entry))

    props = []
    for prop_name, occ_list in occurrences.items():
        tables_present = {t for t, _ in occ_list}
        owning_table = occ_list[0][0]          # TABLE_ORDER 순서상 가장 먼저 나온 테이블
        owning_entry = occ_list[0][1]

        if tables_present <= ETF_TABLES and len(tables_present) > 1:
            domain_class = "ETF"
        elif tables_present <= PRODUCT_TABLES:
            domain_class = TABLE_TO_CLASS[owning_table] if len(tables_present) == 1 else "Product"
        else:
            # AttributeTag 등 공통 상위클래스가 없는 조합은 최초 소유 테이블 클래스로 귀속
            domain_class = TABLE_TO_CLASS[owning_table]

        source_columns = [f"{t}.{e['column_name']}" for t, e in occ_list]
        group = classify_group(owning_entry["column_name"])
        synonyms = owning_entry.get("synonyms") or [] if group == "A" else []

        props.append({
            "prop_name": prop_name,
            "owning_table": owning_table,
            "owning_file": TABLE_TO_FILE[owning_table],
            "domain_class": domain_class,
            "range_xsd": infer_range(owning_entry.get("data_type", "")),
            "label_ko": owning_entry.get("column_korean_name") or owning_entry["column_name"],
            "comment_ko": owning_entry.get("description") or "",
            "source_column": "|".join(source_columns),
            "synonyms": synonyms,
            "shared_tables": sorted(tables_present),
        })
    return props


# -----------------------------------------------------------------------------
# 3단계: TTL 렌더링
# -----------------------------------------------------------------------------
def render_property(p) -> str:
    lines = [f"fp:{p['prop_name']} a owl:DatatypeProperty ;"]
    lines.append(f"    rdfs:domain fp:{p['domain_class']} ; rdfs:range {p['range_xsd']} ;")
    lines.append(f'    rdfs:label "{ttl_escape(p["label_ko"])}"@ko ;')

    has_comment = bool(p["comment_ko"])
    has_synonyms = bool(p["synonyms"])

    src_end = ";" if (has_comment or has_synonyms) else "."
    lines.append(f'    fp:sourceColumn "{p["source_column"]}" {src_end}')

    if has_comment:
        cmt_end = ";" if has_synonyms else "."
        lines.append(f'    rdfs:comment "{ttl_escape(p["comment_ko"])}"@ko {cmt_end}')

    if has_synonyms:
        syn_str = ", ".join(f'"{ttl_escape(s)}"' for s in p["synonyms"])
        lines.append(f"    fp:hasSynonym {syn_str} .")

    return "\n".join(lines)


HEADER_TEMPLATE = """# =============================================================================
# {filename} — {title_ko}
# {source_comment}
# 이 파일은 build_ttl_from_meta.py 가 db/meta_columns/*.json 으로부터 자동 생성한다.
# 손으로 직접 편집하지 말 것 — 재생성 시 덮어써진다. 어휘/관계 정의는 common.ttl 참고.
# =============================================================================
@prefix fp:   <http://mafest.ai/product#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

<> a owl:Ontology ;
   owl:imports <./common.ttl> ;
   rdfs:label "{title_ko}"@ko .
"""


def render_file(filename, props_for_file, reused_props):
    tables_in_file = sorted({p["owning_table"] for p in props_for_file}, key=TABLE_ORDER.index)
    source_comment = "소스 테이블: " + ", ".join(tables_in_file) if tables_in_file else "소스 테이블: (없음)"

    out = [HEADER_TEMPLATE.format(
        filename=filename,
        title_ko=FILE_TITLES[filename],
        source_comment=source_comment,
    )]

    if reused_props:
        out.append(
            "# -----------------------------------------------------------------------------\n"
            "# 다른 도메인 파일과 공유되어 여기서는 재선언하지 않는 속성 (중복 정의 방지)\n"
            "# 실제 정의는 아래 목록의 소유 파일을 참고할 것 — 이 그래프에는 owl:imports 체인이\n"
            "# 아니라 rdflib가 5개 파일을 함께 로드할 때만 완전해진다(완료 조건의 결합 그래프 검증 참고).\n"
            "# -----------------------------------------------------------------------------"
        )
        for rp in reused_props:
            out.append(f"#   fp:{rp['prop_name']}  (정의: {rp['owning_file']}, 공유 테이블: {', '.join(rp['shared_tables'])})")
        out.append("")

    for table in tables_in_file:
        table_props = [p for p in props_for_file if p["owning_table"] == table]
        out.append(
            "# -----------------------------------------------------------------------------\n"
            f"# {table} 전용 속성 ({len(table_props)}개)\n"
            "# -----------------------------------------------------------------------------"
        )
        for p in table_props:
            out.append(render_property(p))
            out.append("")

    return "\n".join(out).rstrip() + "\n"


def main():
    per_table = load_meta_columns()
    props = build_property_index(per_table)

    by_file = OrderedDict((f, []) for f in ["bond_kr.ttl", "etf_kr.ttl", "etf_gl.ttl", "fund_pub.ttl"])
    for p in props:
        by_file[p["owning_file"]].append(p)

    # 공유 속성인데 이 파일이 "소유자"가 아닌 다른 파일들에 재사용 안내 코멘트를 남긴다.
    shared_props = [p for p in props if len(p["shared_tables"]) > 1]
    reused_by_file = OrderedDict((f, []) for f in by_file)
    for p in shared_props:
        involved_files = {TABLE_TO_FILE[t] for t in p["shared_tables"]}
        for f in involved_files:
            if f != p["owning_file"]:
                reused_by_file[f].append(p)

    total_source_columns = 0
    for filename, props_for_file in by_file.items():
        content = render_file(filename, props_for_file, reused_by_file[filename])
        (OUTPUT_DIR / filename).write_text(content, encoding="utf-8")
        total_source_columns += sum(len(p["source_column"].split("|")) for p in props_for_file)
        print(f"  {filename}: 속성 {len(props_for_file)}개 (재사용 안내 {len(reused_by_file[filename])}건)")

    print(f"총 컬럼(fp:sourceColumn 개수 합) = {total_source_columns} / 목표 203")
    print(f"고유 속성(중복 제거 후) = {len(props)}개")


if __name__ == "__main__":
    main()
