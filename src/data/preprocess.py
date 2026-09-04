"""
preprocess.py
=============
4개 금융상품 마스터 데이터셋(국내채권마스터 / 국내ETF마스터 / 해외ETF마스터 / 공모펀드마스터)의
원본(raw) 엑셀 파일을 읽어, 정제된 최종 데이터셋을 생성하는 전처리 파이프라인입니다.

수행 내용:
    1. 공백 제거 및 전량 결측 컬럼 삭제
    2. 날짜 컬럼 포맷 표준화 (YYYY-MM-DD)
    3. 결측치 대체 기호(placeholder) 및 미등록 더미 코드 정제
    4. 이진 플래그(Y/N) 표준화 및 코드 자릿수 포맷 정규화
    5. 데이터셋별 잔여 이슈 정제 (신용등급 다중값 분해, 죽은 컬럼 제거,
       기관명 표기 통일, 이상 코드값 정제 등)

실행:
    python3 preprocess.py
    (raw/ 와 같은 레벨의 preprocessed/ 폴더에 결과 xlsx + 전처리 리포트 생성)
"""
import os
import glob
import shutil
import pandas as pd
import numpy as np
from datetime import datetime

# =====================================================================================
# 공통 상수
# =====================================================================================

TEXT_MISSING_PLACEHOLDERS = {
    '-', '--', '---', '.', '..', 'N/A', 'NA', 'None', 'null', 'nan', 'NULL', '?', 'none', ''
}

DATE_COLUMNS_MAP = {
    "PRBD01N001": ["isu_dt", "mat_dt", "pd_std_info_update", "crd_grd_dt"],
    "PREF01N001": ["cu_upt_dt", "du_upt_dt", "pd_lste_dt", "pd_lstg_dt", "wu_upt_dt"],
    "PREF02N001": ["cu_upt_dt", "du_clpr_base_dt", "du_nav_base_dt", "du_upt_dt", "pd_lstg_dt", "wu_upt_dt"],
    "PRFD01N001": [],
}


def get_date_columns(fname):
    """파일명 전체가 아니라 접두어(PRBD01N001 등)로 매칭 — raw 파일명이 바뀌어도 안 깨지게."""
    prefix = fname.upper().split("_")[0]
    return DATE_COLUMNS_MAP.get(prefix, [])


# =====================================================================================
# 1. 공백 제거 & 전량 결측 컬럼 삭제
# =====================================================================================

def strip_whitespace_and_drop_empty(df):
    stripped_info = {}
    for col in df.columns:
        if df[col].dtype == 'object':
            old_series = df[col].copy()

            def clean_val(v):
                if pd.isna(v):
                    return np.nan
                s = str(v).strip()
                return s if s != "" else np.nan

            new_series = old_series.apply(clean_val)
            modified_count = 0
            for old_v, new_v in zip(old_series, new_series):
                if pd.notna(old_v) and (pd.isna(new_v) or str(old_v) != str(new_v)):
                    modified_count += 1
            df[col] = new_series
            if modified_count > 0:
                stripped_info[col] = modified_count

    dropped_cols = [c for c in df.columns if df[c].isna().all()]
    df = df.drop(columns=dropped_cols)
    return df, dropped_cols, stripped_info


# =====================================================================================
# 2. 날짜 컬럼 표준화 (YYYY-MM-DD)
# =====================================================================================

def parse_date_to_iso(val):
    if pd.isna(val):
        return np.nan
    if isinstance(val, str):
        val_s = val.strip()
        if not val_s or val_s.lower() in ["nan", "none", "null", "nat"]:
            return np.nan
        if val_s in ["0", "0.0"]:
            return np.nan
        if "-" in val_s:
            parts = val_s.split(" ")[0].split("-")
            if len(parts) == 3:
                return f"{parts[0].zfill(4)}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
        digits = "".join(c for c in val_s if c.isdigit())
        if len(digits) == 8:
            return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
        return np.nan
    try:
        val_f = float(val)
        if val_f == 0.0 or np.isnan(val_f):
            return np.nan
        val_s = str(int(round(val_f)))
        if len(val_s) == 8:
            return f"{val_s[0:4]}-{val_s[4:6]}-{val_s[6:8]}"
    except (ValueError, TypeError, OverflowError):
        pass
    return np.nan


def standardize_dates(df, fname):
    date_transforms = {}
    for col in get_date_columns(fname):
        if col in df.columns:
            orig_series = df[col].copy()
            conv_series = orig_series.apply(parse_date_to_iso)
            orig_non_null = orig_series.dropna()
            zeros_count = sum(
                1 for x in orig_non_null
                if (isinstance(x, (int, float)) and x == 0) or str(x).strip() in ['0', '0.0']
            )
            converted_count = conv_series.notna().sum()
            date_transforms[col] = {
                'type': '날짜 표준화 (YYYY-MM-DD)',
                'count': int(len(orig_non_null)),
                'sample': f"유효 {len(orig_non_null):,}건 -> ISO {int(converted_count):,}건 (더미 0값 정제 {zeros_count:,}건)"
            }
            df[col] = conv_series
    return df, date_transforms


# =====================================================================================
# 3. 결측 플레이스홀더 / 더미 코드 -> NaN 통일
# =====================================================================================

def clean_column_missing_values(series, col_name, filename):
    orig_series = series.copy()
    if series.dtype == 'object' or series.dtype == 'str':
        def clean_val(val):
            if pd.isna(val):
                return np.nan
            v_str = str(val).strip()

            if v_str in TEXT_MISSING_PLACEHOLDERS:
                return np.nan
            if col_name == 'curr_cd' and v_str == '000':
                return np.nan
            if col_name == 'itm_eng_nm' and v_str == '0':
                return np.nan
            if col_name == 'fss_itm_no' and v_str == '000000000000':
                return np.nan
            if col_name == 'kofia_fd_ccd' and v_str == '00000000000000000000':
                return np.nan
            if col_name == 'rptt_ksd_itm_no' and v_str in ['000000000000', '00000']:
                return np.nan
            if col_name == 'mtco_itm_no' and v_str in ['0000000', '0000', '00']:
                return np.nan
            if col_name == 'exchdg_yn' and v_str == '00080008':
                return np.nan
            if col_name == 'itm_no' and v_str == '"':
                return np.nan
            if col_name == 'itm_abrv_nm' and v_str == '해당없음' and 'PRFD01N001' in filename:
                return np.nan
            if col_name == 'prfd_attr_cd' and v_str == '해외' and 'PRFD01N001' in filename:
                return np.nan
            return v_str

        cleaned_series = series.apply(clean_val)
        diff_mask = orig_series.notna() & cleaned_series.isna()
        cleaned_count = int(diff_mask.sum())
        samples = orig_series[diff_mask].head(3).tolist() if cleaned_count > 0 else []
        return cleaned_series, cleaned_count, samples
    return orig_series, 0, []


# =====================================================================================
# 4. 데이터셋별 개별 정제
# =====================================================================================

def transform_bond(df):
    """
    국내채권마스터 (PRBD01N001) 정제:
    1. PD_EVCO_CRD_GRD: 여러 신용평가사 등급이 콤마로 이어붙어 저장됨
       -> 대표등급(PD_EVCO_CRD_GRD_MAIN, 첫 번째 값)과 평가사 수(PD_EVCO_CRD_GRD_CNT)를
          새 컬럼으로 분리, 원본 컬럼은 공백만 정리하여 유지
    2. AVG_ANNUAL_TAX_YIELD: 값이 존재하는 행이 전부 0.0으로 고정된 죽은 컬럼 -> 삭제
    """
    mod_info = {}

    if 'pd_evco_crd_grd' in df.columns:
        orig = df['pd_evco_crd_grd'].copy()

        def split_grades(v):
            if pd.isna(v):
                return np.nan, np.nan, np.nan
            parts = [p.strip() for p in str(v).split(',') if p.strip()]
            if not parts:
                return np.nan, np.nan, np.nan
            return ', '.join(parts), parts[0], len(parts)

        parsed = orig.apply(split_grades)
        df['pd_evco_crd_grd'] = parsed.apply(lambda t: t[0])
        df['pd_evco_crd_grd_main'] = parsed.apply(lambda t: t[1])
        df['pd_evco_crd_grd_cnt'] = parsed.apply(lambda t: t[2])
        multi_cnt = int((df['pd_evco_crd_grd_cnt'] > 1).sum())
        mod_info['pd_evco_crd_grd'] = {
            'type': '콤마 다중값 분해 (대표등급/평가사수 컬럼 신설)',
            'count': multi_cnt,
            'sample': "'AAA, AAA, AAA' -> MAIN='AAA', CNT=3"
        }

    if 'avg_annual_tax_yield' in df.columns:
        nn = int(df['avg_annual_tax_yield'].notna().sum())
        df = df.drop(columns=['avg_annual_tax_yield'])
        mod_info['avg_annual_tax_yield'] = {
            'type': '죽은 컬럼 삭제 (분산=0)',
            'count': nn,
            'sample': "존재값 전부 0.0 고정 -> 컬럼 삭제"
        }

    return df, mod_info


def transform_domestic_etf(df):
    """국내ETF마스터 (PREF01N001) 정제"""
    mod_info = {}

    if 'pd_sale_yn' in df.columns:
        orig_sale = df['pd_sale_yn'].copy()
        df['pd_sale_yn'] = df['pd_sale_yn'].map({1: 'Y', 0: 'N'})
        mod_info['pd_sale_yn'] = {
            'type': '이진 플래그 (1/0 -> Y/N)',
            'count': int((orig_sale != df['pd_sale_yn']).sum()),
            'sample': f"1->'Y' ({int((orig_sale==1).sum()):,}건), 0->'N' ({int((orig_sale==0).sum()):,}건)"
        }

    if 'pd_tr_yn' in df.columns:
        orig_tr = df['pd_tr_yn'].copy()
        df['pd_tr_yn'] = df['pd_tr_yn'].map({1.0: 'Y', 0.0: 'N'})
        mod_info['pd_tr_yn'] = {
            'type': '이진 플래그 (1.0/0.0 -> Y/N)',
            'count': int(orig_tr.notna().sum()),
            'sample': f"1.0->'Y' ({int((orig_tr==1.0).sum()):,}건), 0.0->'N' ({int((orig_tr==0.0).sum()):,}건)"
        }

    if 'pd_sect_cd' in df.columns:
        orig_sect = df['pd_sect_cd'].copy()
        df['pd_sect_cd'] = df['pd_sect_cd'].apply(lambda v: f"{int(v)}" if pd.notna(v) else np.nan)
        mod_info['pd_sect_cd'] = {
            'type': '코드 소수점 제거 (Float -> Integer String)',
            'count': int(orig_sect.notna().sum()),
            'sample': f"2.0->'2', 3.0->'3', 9.0->'9' ({int(orig_sect.notna().sum()):,}건)"
        }

    if 'cu_charge_etc_rt' in df.columns:
        nn = int(df['cu_charge_etc_rt'].notna().sum())
        nonzero = int((df['cu_charge_etc_rt'].fillna(0) != 0).sum())
        if nonzero == 0:
            df = df.drop(columns=['cu_charge_etc_rt'])
            mod_info['cu_charge_etc_rt'] = {
                'type': '죽은 컬럼 삭제 (전부 0.0 고정)',
                'count': nn,
                'sample': "존재값 전부 0.0 -> 컬럼 삭제"
            }

    if 'cu_fund_mgmt_co' in df.columns:
        orig_mgmt = df['cu_fund_mgmt_co'].copy()

        SUFFIX_MAP = {
            '미래에셋자산운용': '미래에셋', '미래에셋증권(주)': '미래에셋', '미래에셋': '미래에셋',
            '삼성자산운용': '삼성', '삼성증권(주)': '삼성', '삼성': '삼성',
            'KB자산운용': 'KB', 'KB증권(주)': 'KB', 'KB': 'KB',
            '한국투자신탁운용': '한국투자', '한국투자증권(주)': '한국투자', '한국투자': '한국투자',
            '키움투자자산운용': '키움', '키움증권(주)': '키움', '키움': '키움',
            '한화자산운용': '한화', '한화투자증권(주)': '한화', '한화': '한화',
            '신한자산운용': '신한', '신한투자증권 주식회사': '신한', '신한': '신한',
        }

        def normalize_mgmt(v):
            if pd.isna(v):
                return np.nan
            s = str(v).strip()
            if s in SUFFIX_MAP:
                return SUFFIX_MAP[s]
            for suf in ['투자자산운용', '자산운용', '투자증권 주식회사', '투자증권(주)',
                        '증권(주)', '투자증권', '증권', '주식회사', '(주)']:
                if s.endswith(suf):
                    s = s[: -len(suf)].strip()
                    break
            return s if s else np.nan

        df['cu_fund_mgmt_co'] = orig_mgmt.apply(normalize_mgmt)
        changed = int((orig_mgmt.astype(str) != df['cu_fund_mgmt_co'].astype(str)).sum())
        mod_info['cu_fund_mgmt_co'] = {
            'type': '운용사명 표기 정규화 (법인 접미사 통일)',
            'count': changed,
            'sample': "'키움증권(주)'/'키움' -> '키움' 로 통일"
        }

    if 'pd_pen_risk_nm' in df.columns:
        orig_pen = df['pd_pen_risk_nm'].copy()
        df['pd_pen_risk_nm'] = df['pd_pen_risk_nm'].apply(
            lambda v: np.nan if pd.notna(v) and str(v).strip() == 'N' else v
        )
        cnt = int(((orig_pen.astype(str) == 'N') & orig_pen.notna()).sum())
        if cnt > 0:
            mod_info['pd_pen_risk_nm'] = {
                'type': '이상 코드값 정제',
                'count': cnt,
                'sample': "'N' -> NaN (위험자산/안전자산 외 값)"
            }

    if 'cu_strtegy' in df.columns:
        orig_strat = df['cu_strtegy'].copy()
        VALID_STRATEGY = {'실물복제', '합성복제', '액티브', '패시브', '커버드콜', '레버리지', '인버스'}
        df['cu_strtegy'] = df['cu_strtegy'].apply(
            lambda v: v if pd.isna(v) or str(v).strip() in VALID_STRATEGY else np.nan
        )
        cnt = int((orig_strat.notna() & df['cu_strtegy'].isna()).sum())
        if cnt > 0:
            mod_info['cu_strtegy'] = {
                'type': '이상 코드값 정제',
                'count': cnt,
                'sample': "'C' -> NaN (정의된 운용전략 값 외 이상값)"
            }

    return df, mod_info


def transform_foreign_etf(df):
    """해외ETF마스터 (PREF02N001) 정제"""
    mod_info = {}

    if 'pd_sale_yn' in df.columns:
        orig_sale = df['pd_sale_yn'].copy()
        df['pd_sale_yn'] = df['pd_sale_yn'].map({1.0: 'Y', 0.0: 'N'})
        mod_info['pd_sale_yn'] = {
            'type': '이진 플래그 (1.0 -> Y)',
            'count': int(orig_sale.notna().sum()),
            'sample': f"1.0->'Y' ({int((orig_sale==1.0).sum()):,}건)"
        }

    if 'pd_tr_yn' in df.columns:
        orig_tr = df['pd_tr_yn'].copy()
        df['pd_tr_yn'] = df['pd_tr_yn'].map({1.0: 'Y', 0.0: 'N'})
        mod_info['pd_tr_yn'] = {
            'type': '이진 플래그 (0.0 -> N)',
            'count': int(orig_tr.notna().sum()),
            'sample': f"0.0->'N' ({int((orig_tr==0.0).sum()):,}건)"
        }

    if 'pd_us_cik' in df.columns:
        orig_cik = df['pd_us_cik'].copy()
        df['pd_us_cik'] = df['pd_us_cik'].apply(lambda v: f"{int(v):010d}" if pd.notna(v) else np.nan)
        mod_info['pd_us_cik'] = {
            'type': '식별코드 포맷팅 (10자리 CIK 패딩)',
            'count': int(orig_cik.notna().sum()),
            'sample': f"1350487.0 -> '0001350487' ({int(orig_cik.notna().sum()):,}건)"
        }

    if 'pd_lipper_id' in df.columns:
        orig_lipper = df['pd_lipper_id'].copy()
        df['pd_lipper_id'] = df['pd_lipper_id'].apply(lambda v: f"{int(v)}" if pd.notna(v) else np.nan)
        mod_info['pd_lipper_id'] = {
            'type': '식별코드 소수점 제거 (Float -> Integer String)',
            'count': int(orig_lipper.notna().sum()),
            'sample': f"40113961.0 -> '40113961' ({int(orig_lipper.notna().sum()):,}건)"
        }

    if 'cu_base_index' in df.columns:
        orig_idx = df['cu_base_index'].copy()
        PLACEHOLDER_INDEX = {
            'Index is not provided by Management Company',
            'Index is not available on Lipper Database',
        }
        df['cu_base_index'] = df['cu_base_index'].apply(
            lambda v: np.nan if pd.notna(v) and str(v).strip() in PLACEHOLDER_INDEX else v
        )
        cnt = int((orig_idx.notna() & df['cu_base_index'].isna()).sum())
        mod_info['cu_base_index'] = {
            'type': '플레이스홀더 문자열 -> NaN',
            'count': cnt,
            'sample': "'Index is not provided by Management Company', "
                      "'Index is not available on Lipper Database' -> NaN"
        }

    if 'du_er_1d' in df.columns:
        nn = int(df['du_er_1d'].notna().sum())
        nonzero = int((df['du_er_1d'].fillna(0) != 0).sum())
        if nonzero == 0:
            df = df.drop(columns=['du_er_1d'])
            mod_info['du_er_1d'] = {
                'type': '죽은 컬럼 삭제 (전부 0.0 고정)',
                'count': nn,
                'sample': "존재값 전부 0.0 -> 컬럼 삭제"
            }

    return df, mod_info


def transform_public_fund(df):
    """공모펀드마스터 (PRFD01N001) 정제"""
    mod_info = {}

    if 'frc_bpr_itm_yn' in df.columns:
        orig_frc = df['frc_bpr_itm_yn'].copy()
        df['frc_bpr_itm_yn'] = df['frc_bpr_itm_yn'].map({1.0: 'Y', 0.0: 'N'})
        mod_info['frc_bpr_itm_yn'] = {
            'type': '이진 플래그 (1.0/0.0 -> Y/N)',
            'count': int(orig_frc.notna().sum()),
            'sample': f"0.0->'N' ({int((orig_frc==0.0).sum()):,}건), 1.0->'Y' ({int((orig_frc==1.0).sum()):,}건)"
        }

    if 'hdge_fd_yn' in df.columns:
        orig_hdge = df['hdge_fd_yn'].copy()
        df['hdge_fd_yn'] = df['hdge_fd_yn'].map({1.0: 'Y', 0.0: 'N'})
        mod_info['hdge_fd_yn'] = {
            'type': '이진 플래그 (0.0 -> N)',
            'count': int(orig_hdge.notna().sum()),
            'sample': f"0.0->'N' ({int((orig_hdge==0.0).sum()):,}건)"
        }

    if 'ofsfd_yn' in df.columns:
        orig_ofs = df['ofsfd_yn'].copy()
        df['ofsfd_yn'] = df['ofsfd_yn'].map({1.0: 'Y', 0.0: 'N'})
        mod_info['ofsfd_yn'] = {
            'type': '이진 플래그 (0.0 -> N)',
            'count': int(orig_ofs.notna().sum()),
            'sample': f"0.0->'N' ({int((orig_ofs==0.0).sum()):,}건)"
        }

    if 'thco_sale_yn' in df.columns:
        orig_thco = df['thco_sale_yn'].copy()
        df['thco_sale_yn'] = df['thco_sale_yn'].apply(lambda v: 'Y' if v == 'Y' else np.nan)
        mod_info['thco_sale_yn'] = {
            'type': '플래그 이상치 정제',
            'count': int((orig_thco.notna() & df['thco_sale_yn'].isna()).sum()),
            'sample': "'KRZ50226929C' -> NaN"
        }

    if 'zrin_fd_ivst_risk_grd_nm' in df.columns:
        orig_rnm = df['zrin_fd_ivst_risk_grd_nm'].copy()

        def clean_risk_nm(v):
            if pd.isna(v) or str(v).strip() == '06':
                return np.nan
            s = str(v).strip()
            if s == '높은위험':
                return '높은 위험'
            if s == '보통위험':
                return '보통 위험'
            return s

        df['zrin_fd_ivst_risk_grd_nm'] = df['zrin_fd_ivst_risk_grd_nm'].apply(clean_risk_nm)
        typo_cnt = int(((orig_rnm == '높은위험') | (orig_rnm == '보통위험') | (orig_rnm == '06')).sum())
        mod_info['zrin_fd_ivst_risk_grd_nm'] = {
            'type': '범주형 띄어쓰기 정규화',
            'count': typo_cnt,
            'sample': "'높은위험'->'높은 위험', '보통위험'->'보통 위험', '06'->NaN"
        }

    if 'zrin_fd_ivst_risk_gcd' in df.columns:
        orig_rgcd = df['zrin_fd_ivst_risk_gcd'].copy()

        def clean_risk_gcd(v):
            if pd.isna(v) or v == 20054.0:
                return np.nan
            return f"{int(v)}"

        df['zrin_fd_ivst_risk_gcd'] = df['zrin_fd_ivst_risk_gcd'].apply(clean_risk_gcd)
        mod_info['zrin_fd_ivst_risk_gcd'] = {
            'type': '위험등급코드 소수점 제거 (Float -> Integer String)',
            'count': int(orig_rgcd.notna().sum()),
            'sample': "1.0~6.0 -> '1'~'6', 20054.0->NaN"
        }

    if 'or_co_xtn_itt_cd' in df.columns:
        orig_or = df['or_co_xtn_itt_cd'].copy()
        df['or_co_xtn_itt_cd'] = df['or_co_xtn_itt_cd'].apply(lambda v: f"{int(v):08d}" if pd.notna(v) else np.nan)
        mod_info['or_co_xtn_itt_cd'] = {
            'type': '기관코드 8자리 패딩 (Float -> 8-digit String)',
            'count': int(orig_or.notna().sum()),
            'sample': f"40010.0 -> '00040010' ({int(orig_or.notna().sum()):,}건)"
        }

    if 'trusc_xtn_itt_cd' in df.columns:
        orig_trusc = df['trusc_xtn_itt_cd'].copy()
        df['trusc_xtn_itt_cd'] = df['trusc_xtn_itt_cd'].apply(lambda v: f"{int(v):08d}" if pd.notna(v) else np.nan)
        mod_info['trusc_xtn_itt_cd'] = {
            'type': '기관코드 8자리 패딩 (Float -> 8-digit String)',
            'count': int(orig_trusc.notna().sum()),
            'sample': f"20054.0 -> '00020054' ({int(orig_trusc.notna().sum()):,}건)"
        }

    if 'fd_estb_ctry_cd' in df.columns:
        orig_ctry = df['fd_estb_ctry_cd'].copy()
        df['fd_estb_ctry_cd'] = df['fd_estb_ctry_cd'].apply(lambda v: f"{int(v):03d}" if pd.notna(v) else np.nan)
        mod_info['fd_estb_ctry_cd'] = {
            'type': '국가코드 3자리 패딩 (Float -> 3-digit String)',
            'count': int(orig_ctry.notna().sum()),
            'sample': "0.0 -> '000', 410.0 -> '410'"
        }

    if 'fd_set_pcd' in df.columns:
        orig_pcd = df['fd_set_pcd'].copy()
        df['fd_set_pcd'] = df['fd_set_pcd'].apply(lambda v: f"{int(v)}" if pd.notna(v) else np.nan)
        mod_info['fd_set_pcd'] = {
            'type': '설정유형코드 소수점 제거 (Float -> Integer String)',
            'count': int(orig_pcd.notna().sum()),
            'sample': f"10.0 -> '10', 20.0 -> '20', 0.0 -> '0' ({int(orig_pcd.notna().sum()):,}건)"
        }

    if 'pfiv_sale_cntl_tcd' in df.columns:
        orig_tcd = df['pfiv_sale_cntl_tcd'].copy()
        df['pfiv_sale_cntl_tcd'] = df['pfiv_sale_cntl_tcd'].apply(lambda v: f"{int(v):02d}" if pd.notna(v) else np.nan)
        mod_info['pfiv_sale_cntl_tcd'] = {
            'type': '판매제어코드 2자리 패딩 (Float -> 2-digit String)',
            'count': int(orig_tcd.notna().sum()),
            'sample': "0.0 -> '00', 1.0 -> '01', 2.0 -> '02'"
        }

    if 'or_attr_desc' in df.columns:
        orig_attr = df['or_attr_desc'].copy()
        cnt = int((orig_attr.astype(str).str.strip() == '06').sum())
        df['or_attr_desc'] = df['or_attr_desc'].apply(
            lambda v: np.nan if pd.notna(v) and str(v).strip() == '06' else v
        )
        if cnt > 0:
            mod_info['or_attr_desc'] = {
                'type': '미매핑 코드값 정제',
                'count': cnt,
                'sample': f"'06' -> NaN (자산분류 코드표에 없는 미매핑 코드, {cnt:,}건)"
            }

    return df, mod_info


# =====================================================================================
# 5. 실행 드라이버
# =====================================================================================

def run_preprocessing():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = script_dir
    raw_dir = os.path.join(data_dir, "raw")
    out_dir = os.path.join(data_dir, "preprocessed")
    os.makedirs(out_dir, exist_ok=True)

    raw_files = sorted(glob.glob(os.path.join(raw_dir, "*.xlsx")))
    print(f"[Preprocessing] Found {len(raw_files)} files in raw folder: '{raw_dir}'\n")

    summary_records = []

    for file_path in raw_files:
        fname = os.path.basename(file_path)
        out_path = os.path.join(out_dir, fname)
        print("--------------------------------------------------")
        print(f"Processing: {fname}...")

        if "schema" in fname:
            shutil.copy2(file_path, out_path)
            df_schema = pd.read_excel(file_path)
            print(f"  - Schema file preserved: Shape={df_schema.shape}")
            summary_records.append({
                "filename": fname, "is_schema": True,
                "orig_shape": df_schema.shape, "final_shape": df_schema.shape,
                "dropped_cols": [], "stripped_info": {}, "date_transforms": {},
                "missing_cleaned": {}, "mod_info": {}
            })
            continue

        df = pd.read_excel(file_path)
        df.columns = [c.lower() for c in df.columns]
        orig_shape = df.shape

        df, dropped_cols, stripped_info = strip_whitespace_and_drop_empty(df)
        df, date_transforms = standardize_dates(df, fname)

        missing_cleaned = {}
        for col in df.columns:
            cleaned_series, cnt, samples = clean_column_missing_values(df[col], col, fname)
            if cnt > 0:
                missing_cleaned[col] = {"cleaned_count": cnt, "samples": samples}
            df[col] = cleaned_series

        mod_info = {}
        if fname.upper().startswith("PRBD01N001"):
            df, mod_info = transform_bond(df)
        elif fname.upper().startswith("PREF01N001"):
            df, mod_info = transform_domestic_etf(df)
        elif fname.upper().startswith("PREF02N001"):
            df, mod_info = transform_foreign_etf(df)
        elif fname.upper().startswith("PRFD01N001"):
            df, mod_info = transform_public_fund(df)

        final_shape = df.shape
        print(f"  - Original Shape: {orig_shape} -> Final Shape: {final_shape}")
        print(f"  - Saving to: {out_path}...")
        df.to_excel(out_path, index=False)
        print("  - Saved successfully.")

        summary_records.append({
            "filename": fname, "is_schema": False,
            "orig_shape": orig_shape, "final_shape": final_shape,
            "dropped_cols": dropped_cols, "stripped_info": stripped_info,
            "date_transforms": date_transforms, "missing_cleaned": missing_cleaned,
            "mod_info": mod_info
        })

    report_path = os.path.join(out_dir, "preprocessing_report.md")
    generate_report(report_path, summary_records)
    print("\n==================================================")
    print("[Preprocessing Completed Successfully]")
    print(f"Report saved to: {report_path}")


def generate_report(report_path, summary_records):
    lines = []
    lines.append("# 데이터 전처리 리포트\n")
    lines.append(f"**전처리 수행 일시**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("## 1. 개요\n")
    lines.append(
        "`raw/` 원본 데이터를 대상으로 공백/전량결측 컬럼 정리, 날짜 표준화, "
        "결측 플레이스홀더·더미코드 통일, 이진 플래그·코드 포맷 표준화, "
        "데이터셋별 잔여 이슈 정제까지 한 번의 실행으로 수행합니다.\n"
    )
    lines.append("## 2. 데이터셋별 변경 요약\n")
    lines.append("| 파일명 | 원본 Shape | 최종 Shape | 삭제 컬럼 | 공백정제 컬럼 | 날짜표준화 컬럼 | 결측정제 컬럼 | 개별정제 컬럼 |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for r in summary_records:
        if r["is_schema"]:
            lines.append(f"| `{r['filename']}` | ({r['orig_shape'][0]:,}, {r['orig_shape'][1]}) | ({r['final_shape'][0]:,}, {r['final_shape'][1]}) | - | - | - | - | 스키마 보존 |")
        else:
            lines.append(
                f"| `{r['filename']}` | ({r['orig_shape'][0]:,}, {r['orig_shape'][1]}) | "
                f"({r['final_shape'][0]:,}, {r['final_shape'][1]}) | {len(r['dropped_cols'])} | "
                f"{len(r['stripped_info'])} | {len(r['date_transforms'])} | "
                f"{len(r['missing_cleaned'])} | {len(r['mod_info'])} |"
            )
    lines.append("\n")

    lines.append("## 3. 상세 처리 내역\n")
    for r in summary_records:
        if r["is_schema"]:
            continue
        lines.append(f"### 📄 `{r['filename']}`\n")
        lines.append(f"- 원본: {r['orig_shape'][0]:,} rows × {r['orig_shape'][1]} cols  →  최종: {r['final_shape'][0]:,} rows × {r['final_shape'][1]} cols\n")

        if r['dropped_cols']:
            lines.append(f"**전량 결측 삭제 컬럼 ({len(r['dropped_cols'])}개)**: " + ", ".join(f"`{c}`" for c in r['dropped_cols']) + "\n")

        if r['date_transforms']:
            lines.append("**날짜 표준화**\n")
            lines.append("| 컬럼명 | 내역 |")
            lines.append("| :--- | :--- |")
            for col, info in r['date_transforms'].items():
                lines.append(f"| `{col}` | {info['sample']} |")
            lines.append("")

        if r['missing_cleaned']:
            lines.append(f"**결측 플레이스홀더/더미코드 정제 ({len(r['missing_cleaned'])}개 컬럼)**\n")
            lines.append("| 컬럼명 | 정제 건수 | 샘플 |")
            lines.append("| :--- | :---: | :--- |")
            for col, info in r['missing_cleaned'].items():
                lines.append(f"| `{col}` | {info['cleaned_count']:,} | `{info['samples']}` |")
            lines.append("")

        if r['mod_info']:
            lines.append(f"**개별 정제 ({len(r['mod_info'])}개 컬럼)**\n")
            lines.append("| 컬럼명 | 정제 유형 | 적용 건수 | 세부 내역 |")
            lines.append("| :--- | :---: | :---: | :--- |")
            for col, info in r['mod_info'].items():
                lines.append(f"| `{col}` | {info['type']} | **{info['count']:,}건** | {info['sample']} |")
            lines.append("")

        lines.append("---\n")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    run_preprocessing()