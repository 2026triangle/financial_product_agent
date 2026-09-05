# tools/concept_column_map.py
CONCEPT_COLUMN_MAP = {
    "tb_domestic_bond": {
        "CreditRating": "crd_grd",
        "RiskGrade": "pd_risk_gcd",
        # 상품명 텍스트 검색용 정식명 컬럼(qp_task14_productname.md §3.3 실측).
        # 약칭 컬럼은 pd_abrv_nm이지만, LIKE '%값%'가 부분 일치라 정식명만으로도
        # 대부분 커버된다 — §4.3 end-to-end 기대 SQL(itm_nm 기준)과 같은 원칙.
        "ProductName": "pd_nm",
        # 판매가능 판정용 컬럼(qp_task16_parser_fixes.md §3.3, ot_spec.md §2.6) — 국내채권만
        # 판매여부 컬럼이 없어 buyable_quantity로 대신 판정한다(> 0). 비교 연산자가 다르므로
        # 실제 WHERE절 조립은 text_to_sql_agent.py의 SELLABLE 분기가 담당한다.
        "Sellable": "buyable_quantity",
    },
    "tb_domestic_etf": {
        "RiskGrade": "pd_risk_cd",
        "AssetClass": "wu_inv_ast_type",
        "Region": "wu_inv_rgn",
        "ProductName": "pd_nm",
        "Sellable": "pd_sale_yn",
    },
    "tb_foreign_etf": {
        # RiskGrade 없음 (온톨로지 notAvailableIn 명시)
        "AssetClass": "wu_inv_ast_type",
        "Region": "wu_inv_rgn",
        "ProductName": "pd_nm",
        "Sellable": "pd_sale_yn",
    },
    "tb_fund": {
        "RiskGrade": "zrin_fd_ivst_risk_gcd",
        "AssetClass": "or_attr_desc",
        "Region": "fd_ivst_rgn_desc",
        # 펀드만 컬럼명이 다르다(pd_nm이 아니라 itm_nm) — qp_task14_productname.md §3.3.
        "ProductName": "itm_nm",
        # DB 실측(qp_task16_parser_fixes.md §3.4) — 컬럼명은 sale_yn이지만 값은 'Y'가
        # 아니라 '판매중'/'판매완료'다. 실제 값 매핑은 text_to_sql_agent.py에 있다.
        "Sellable": "sale_yn",
    },
}
