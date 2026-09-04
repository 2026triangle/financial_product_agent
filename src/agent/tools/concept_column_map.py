# tools/concept_column_map.py
CONCEPT_COLUMN_MAP = {
    "tb_domestic_bond": {
        "CreditRating": "crd_grd",
        "RiskGrade": "pd_risk_gcd",
    },
    "tb_domestic_etf": {
        "RiskGrade": "pd_risk_cd",
        "AssetClass": "wu_inv_ast_type",
        "Region": "wu_inv_rgn",
    },
    "tb_foreign_etf": {
        # RiskGrade 없음 (온톨로지 notAvailableIn 명시)
        "AssetClass": "wu_inv_ast_type",
        "Region": "wu_inv_rgn",
    },
    "tb_fund": {
        "RiskGrade": "zrin_fd_ivst_risk_gcd",
        "AssetClass": "or_attr_desc",
        "Region": "fd_ivst_rgn_desc",
    },
}