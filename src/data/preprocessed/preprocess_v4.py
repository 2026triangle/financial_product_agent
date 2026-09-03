import os
import glob
import shutil
import pandas as pd
import numpy as np
from datetime import datetime

def transform_domestic_etf(df):
    """
    PREF01N001 (국내ETF) v4 정제:
    1. 이진 플래그 Y/N 표준화: pd_sale_yn (1->'Y', 0->'N'), pd_tr_yn (1.0->'Y', 0.0->'N', NaN 유지)
    2. 코드 소수점 정제: pd_sect_cd (float -> integer string)
    """
    mod_info = {}
    
    # 1. pd_sale_yn (int64 -> 'Y'/'N')
    orig_sale = df['pd_sale_yn'].copy()
    df['pd_sale_yn'] = df['pd_sale_yn'].map({1: 'Y', 0: 'N'})
    mod_info['pd_sale_yn'] = {
        'type': '이진 플래그 (1/0 -> Y/N)',
        'count': int((orig_sale != df['pd_sale_yn']).sum()),
        'sample': f"1->'Y' ({int((orig_sale==1).sum()):,}건), 0->'N' ({int((orig_sale==0).sum()):,}건)"
    }
    
    # 2. pd_tr_yn (float64 -> 'Y'/'N'/NaN)
    orig_tr = df['pd_tr_yn'].copy()
    df['pd_tr_yn'] = df['pd_tr_yn'].map({1.0: 'Y', 0.0: 'N'})
    mod_info['pd_tr_yn'] = {
        'type': '이진 플래그 (1.0/0.0 -> Y/N)',
        'count': int(orig_tr.notna().sum()),
        'sample': f"1.0->'Y' ({int((orig_tr==1.0).sum()):,}건), 0.0->'N' ({int((orig_tr==0.0).sum()):,}건)"
    }
    
    # 3. pd_sect_cd (float64 -> string)
    orig_sect = df['pd_sect_cd'].copy()
    df['pd_sect_cd'] = df['pd_sect_cd'].apply(lambda v: f"{int(v)}" if pd.notna(v) else np.nan)
    mod_info['pd_sect_cd'] = {
        'type': '코드 소수점 제거 (Float -> Integer String)',
        'count': int(orig_sect.notna().sum()),
        'sample': f"2.0->'2', 3.0->'3', 9.0->'9' ({int(orig_sect.notna().sum()):,}건)"
    }
    
    return df, mod_info

def transform_foreign_etf(df):
    """
    PREF02N001 (해외ETF) v4 정제:
    1. 이진 플래그 Y/N 표준화: pd_sale_yn (1.0->'Y', NaN 유지), pd_tr_yn (0.0->'N', NaN 유지)
    2. 식별자/코드 포맷팅: pd_us_cik (10자리 CIK 패딩), pd_lipper_id (정수 문자열)
    """
    mod_info = {}
    
    # 1. pd_sale_yn (float64 -> 'Y'/NaN)
    orig_sale = df['pd_sale_yn'].copy()
    df['pd_sale_yn'] = df['pd_sale_yn'].map({1.0: 'Y', 0.0: 'N'})
    mod_info['pd_sale_yn'] = {
        'type': '이진 플래그 (1.0 -> Y)',
        'count': int(orig_sale.notna().sum()),
        'sample': f"1.0->'Y' ({int((orig_sale==1.0).sum()):,}건)"
    }
    
    # 2. pd_tr_yn (float64 -> 'N'/NaN)
    orig_tr = df['pd_tr_yn'].copy()
    df['pd_tr_yn'] = df['pd_tr_yn'].map({1.0: 'Y', 0.0: 'N'})
    mod_info['pd_tr_yn'] = {
        'type': '이진 플래그 (0.0 -> N)',
        'count': int(orig_tr.notna().sum()),
        'sample': f"0.0->'N' ({int((orig_tr==0.0).sum()):,}건)"
    }
    
    # 3. pd_us_cik (float64 -> 10자리 SEC CIK 문자열)
    orig_cik = df['pd_us_cik'].copy()
    df['pd_us_cik'] = df['pd_us_cik'].apply(lambda v: f"{int(v):010d}" if pd.notna(v) else np.nan)
    mod_info['pd_us_cik'] = {
        'type': '식별코드 포맷팅 (10자리 CIK 패딩)',
        'count': int(orig_cik.notna().sum()),
        'sample': f"1350487.0 -> '0001350487' ({int(orig_cik.notna().sum()):,}건)"
    }
    
    # 4. pd_lipper_id (float64 -> 정수 문자열)
    orig_lipper = df['pd_lipper_id'].copy()
    df['pd_lipper_id'] = df['pd_lipper_id'].apply(lambda v: f"{int(v)}" if pd.notna(v) else np.nan)
    mod_info['pd_lipper_id'] = {
        'type': '식별코드 소수점 제거 (Float -> Integer String)',
        'count': int(orig_lipper.notna().sum()),
        'sample': f"40113961.0 -> '40113961' ({int(orig_lipper.notna().sum()):,}건)"
    }
    
    return df, mod_info

def transform_public_fund(df):
    """
    PRFD01N001 (공모펀드) v4 정제:
    1. 이진 플래그 Y/N 표준화: frc_bpr_itm_yn (1.0->'Y', 0.0->'N'), hdge_fd_yn (0.0->'N'), ofsfd_yn (0.0->'N'), thco_sale_yn 이상치 제거
    2. 위험등급명 띄어쓰기 오탈자 정규화: '높은위험'->'높은 위험', '보통위험'->'보통 위험'
    3. 기관/유형 코드 자릿수 패딩 및 소수점 제거: or_co_xtn_itt_cd (8자리), trusc_xtn_itt_cd (8자리), fd_estb_ctry_cd (3자리), pfiv_sale_cntl_tcd (2자리), fd_set_pcd, zrin_fd_ivst_risk_gcd
    """
    mod_info = {}
    
    # 1. frc_bpr_itm_yn (0.0/1.0 -> 'N'/'Y'/NaN)
    orig_frc = df['frc_bpr_itm_yn'].copy()
    df['frc_bpr_itm_yn'] = df['frc_bpr_itm_yn'].map({1.0: 'Y', 0.0: 'N'})
    mod_info['frc_bpr_itm_yn'] = {
        'type': '이진 플래그 (1.0/0.0 -> Y/N)',
        'count': int(orig_frc.notna().sum()),
        'sample': f"0.0->'N' ({int((orig_frc==0.0).sum()):,}건), 1.0->'Y' ({int((orig_frc==1.0).sum()):,}건)"
    }
    
    # 2. hdge_fd_yn (0.0 -> 'N'/NaN)
    orig_hdge = df['hdge_fd_yn'].copy()
    df['hdge_fd_yn'] = df['hdge_fd_yn'].map({1.0: 'Y', 0.0: 'N'})
    mod_info['hdge_fd_yn'] = {
        'type': '이진 플래그 (0.0 -> N)',
        'count': int(orig_hdge.notna().sum()),
        'sample': f"0.0->'N' ({int((orig_hdge==0.0).sum()):,}건)"
    }
    
    # 3. ofsfd_yn (0.0 -> 'N'/NaN)
    orig_ofs = df['ofsfd_yn'].copy()
    df['ofsfd_yn'] = df['ofsfd_yn'].map({1.0: 'Y', 0.0: 'N'})
    mod_info['ofsfd_yn'] = {
        'type': '이진 플래그 (0.0 -> N)',
        'count': int(orig_ofs.notna().sum()),
        'sample': f"0.0->'N' ({int((orig_ofs==0.0).sum()):,}건)"
    }
    
    # 4. thco_sale_yn 이상치 제거
    orig_thco = df['thco_sale_yn'].copy()
    df['thco_sale_yn'] = df['thco_sale_yn'].apply(lambda v: 'Y' if v == 'Y' else np.nan)
    mod_info['thco_sale_yn'] = {
        'type': '플래그 이상치 정제',
        'count': int((orig_thco.notna() & df['thco_sale_yn'].isna()).sum()),
        'sample': f"'KRZ50226929C' -> NaN (1건)"
    }
    
    # 5. zrin_fd_ivst_risk_grd_nm (띄어쓰기 정규화)
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
        'sample': f"'높은위험'->'높은 위험' (163건), '보통위험'->'보통 위험' (75건), '06'->NaN (1건)"
    }
    
    # 6. zrin_fd_ivst_risk_gcd (위험등급코드 정수 문자열)
    orig_rgcd = df['zrin_fd_ivst_risk_gcd'].copy()
    def clean_risk_gcd(v):
        if pd.isna(v) or v == 20054.0:
            return np.nan
        return f"{int(v)}"
    df['zrin_fd_ivst_risk_gcd'] = df['zrin_fd_ivst_risk_gcd'].apply(clean_risk_gcd)
    mod_info['zrin_fd_ivst_risk_gcd'] = {
        'type': '위험등급코드 소수점 제거 (Float -> Integer String)',
        'count': int(orig_rgcd.notna().sum()),
        'sample': f"1.0~6.0 -> '1'~'6' ({int((orig_rgcd <= 6).sum()):,}건), 20054.0->NaN (1건)"
    }
    
    # 7. or_co_xtn_itt_cd (8자리 패딩)
    orig_or = df['or_co_xtn_itt_cd'].copy()
    df['or_co_xtn_itt_cd'] = df['or_co_xtn_itt_cd'].apply(lambda v: f"{int(v):08d}" if pd.notna(v) else np.nan)
    mod_info['or_co_xtn_itt_cd'] = {
        'type': '기관코드 8자리 패딩 (Float -> 8-digit String)',
        'count': int(orig_or.notna().sum()),
        'sample': f"40010.0 -> '00040010' ({int(orig_or.notna().sum()):,}건)"
    }
    
    # 8. trusc_xtn_itt_cd (8자리 패딩)
    orig_trusc = df['trusc_xtn_itt_cd'].copy()
    df['trusc_xtn_itt_cd'] = df['trusc_xtn_itt_cd'].apply(lambda v: f"{int(v):08d}" if pd.notna(v) else np.nan)
    mod_info['trusc_xtn_itt_cd'] = {
        'type': '기관코드 8자리 패딩 (Float -> 8-digit String)',
        'count': int(orig_trusc.notna().sum()),
        'sample': f"20054.0 -> '00020054' ({int(orig_trusc.notna().sum()):,}건)"
    }
    
    # 9. fd_estb_ctry_cd (3자리 패딩)
    orig_ctry = df['fd_estb_ctry_cd'].copy()
    df['fd_estb_ctry_cd'] = df['fd_estb_ctry_cd'].apply(lambda v: f"{int(v):03d}" if pd.notna(v) else np.nan)
    mod_info['fd_estb_ctry_cd'] = {
        'type': '국가코드 3자리 패딩 (Float -> 3-digit String)',
        'count': int(orig_ctry.notna().sum()),
        'sample': f"0.0 -> '000' (92,838건), 410.0 -> '410' (2,715건)"
    }
    
    # 10. fd_set_pcd (정수 문자열)
    orig_pcd = df['fd_set_pcd'].copy()
    df['fd_set_pcd'] = df['fd_set_pcd'].apply(lambda v: f"{int(v)}" if pd.notna(v) else np.nan)
    mod_info['fd_set_pcd'] = {
        'type': '설정유형코드 소수점 제거 (Float -> Integer String)',
        'count': int(orig_pcd.notna().sum()),
        'sample': f"10.0 -> '10', 20.0 -> '20', 0.0 -> '0' ({int(orig_pcd.notna().sum()):,}건)"
    }
    
    # 11. pfiv_sale_cntl_tcd (2자리 패딩)
    orig_tcd = df['pfiv_sale_cntl_tcd'].copy()
    df['pfiv_sale_cntl_tcd'] = df['pfiv_sale_cntl_tcd'].apply(lambda v: f"{int(v):02d}" if pd.notna(v) else np.nan)
    mod_info['pfiv_sale_cntl_tcd'] = {
        'type': '판매제어코드 2자리 패딩 (Float -> 2-digit String)',
        'count': int(orig_tcd.notna().sum()),
        'sample': f"0.0 -> '00', 1.0 -> '01', 2.0 -> '02' ({int(orig_tcd.notna().sum()):,}건)"
    }
    
    return df, mod_info

def run_v4_preprocessing():
    v4_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.dirname(os.path.dirname(v4_dir))
    v3_dir = os.path.join(data_dir, "perprocessed_datas", "v3")
    
    os.makedirs(v4_dir, exist_ok=True)
    
    v3_files = sorted(glob.glob(os.path.join(v3_dir, "*.xlsx")))
    print(f"[v4 Data Preprocessing] Found {len(v3_files)} files in v3 folder: '{v3_dir}'\n")
    
    summary_records = []
    
    for file_path in v3_files:
        fname = os.path.basename(file_path)
        out_path = os.path.join(v4_dir, fname)
        print(f"--------------------------------------------------")
        print(f"Processing: {fname}...")
        
        # If schema file, copy as is
        if "schema" in fname:
            shutil.copy2(file_path, out_path)
            df_schema = pd.read_excel(file_path, engine="calamine")
            print(f"  - Schema file preserved: Shape={df_schema.shape}")
            summary_records.append({
                "filename": fname,
                "is_schema": True,
                "orig_shape": df_schema.shape,
                "final_shape": df_schema.shape,
                "mod_info": {}
            })
            continue
            
        df = pd.read_excel(file_path, engine="calamine")
        orig_shape = df.shape
        mod_info = {}
        
        if fname.startswith("PREF01N001"):
            df, mod_info = transform_domestic_etf(df)
        elif fname.startswith("PREF02N001"):
            df, mod_info = transform_foreign_etf(df)
        elif fname.startswith("PRFD01N001"):
            df, mod_info = transform_public_fund(df)
        elif fname.startswith("PRBD01N001"):
            print("  - Domestic bond: No flag/code transformations needed (integrity preserved).")
            
        for col, info in mod_info.items():
            print(f"  - [{info['type']}] {col:25s}: {info['count']:,}건 ({info['sample']})")
            
        final_shape = df.shape
        print(f"  - Saving to: {out_path}...")
        df.to_excel(out_path, index=False)
        print(f"  - Saved successfully. Final Shape: {final_shape}")
        
        summary_records.append({
            "filename": fname,
            "is_schema": False,
            "orig_shape": orig_shape,
            "final_shape": final_shape,
            "mod_info": mod_info
        })
        
    # Generate diff.md report
    diff_path = os.path.join(v4_dir, "diff.md")
    generate_diff_md(diff_path, summary_records)
    print(f"\n==================================================")
    print(f"[v4 Preprocessing Completed Successfully]")
    print(f"Summary saved to diff.md at: {diff_path}")

def generate_diff_md(diff_path, summary_records):
    lines = []
    lines.append("# v4 Data Preprocessing Report (diff.md)\n")
    lines.append(f"**전처리 수행 일시**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("## 1. 전처리 개요 (Overview)\n")
    lines.append("`v4` 단계 전처리는 `perprocessed_datas/v3`에 저장된 결측치 정제 데이터를 바탕으로, **(1) 숫자로 저장된 이진 플래그(1/0, 1.0/0.0)를 표준 `'Y'/'N'` 포맷으로 통일(NaN의 고유 의미 보존)**하고, **(2) 위험등급명 등의 범주형 띄어쓰기 오탈자를 정규화**하며, **(3) 실수형(Float64)으로 저장되어 있던 기관/유형 코드의 소수점 제거 및 표준 자릿수 패딩 문자열 변환**을 수행합니다.\n")
    lines.append("### 주요 처리 내용:")
    lines.append("1. **이진 플래그 표준화 (`1`/`0`, `1.0`/`0.0` → `'Y'`/`'N'`)**: 수치형으로 저장되었던 판매여부, 거래정지여부, 외화기준가여부, 헤지펀드여부, 역외펀드여부를 `'Y'/'N'`으로 통일하고, 3-Valued Logic(해당없음/미측정)을 유지하기 위해 `NaN`은 그대로 보존")
    lines.append("2. **범주형 띄어쓰기 오탈자 정규화**: 공모펀드 위험등급명(`zrin_fd_ivst_risk_grd_nm`)의 `'높은위험'` → `'높은 위험'`, `'보통위험'` → `'보통 위험'` 통일 및 이상치 제거")
    lines.append("3. **기관/유형/식별코드 표준 포맷팅**: 대외기관코드(`or_co_xtn_itt_cd`, `trusc_xtn_itt_cd`), 국가코드(`fd_estb_ctry_cd`), SEC CIK(`pd_us_cik`), 섹터코드 등 소수점이 포함된 실수형 코드를 스키마 명세에 맞게 고정자릿수 패딩 문자열로 변환\n")
    
    lines.append("## 2. 데이터셋별 변경 요약 (Dataset Summary Table)\n")
    lines.append("| 파일명 | 데이터 Shape (행, 열) | 변환 컬럼 수 | 주요 변환 항목 | 상태 |")
    lines.append("| :--- | :---: | :---: | :--- | :---: |")
    for r in summary_records:
        if r["is_schema"]:
            lines.append(f"| `{r['filename']}` | ({r['orig_shape'][0]:,}, {r['orig_shape'][1]}) | - | 스키마 명세 보존 | 스키마 보존 |")
        else:
            mods = r["mod_info"]
            lines.append(f"| `{r['filename']}` | ({r['orig_shape'][0]:,}, {r['orig_shape'][1]}) | **{len(mods)}개** | {', '.join(list(mods.keys())[:3]) if mods else '기존 무결성 유지'} | 변환 완료 |")
    lines.append("\n")
    
    lines.append("## 3. 상세 변환 내역 (Detailed Transformation Log)\n")
    for r in summary_records:
        if r["is_schema"]:
            continue
        lines.append(f"### 📄 `{r['filename']}`\n")
        mods = r["mod_info"]
        if not mods:
            lines.append("- **변환 대상 컬럼**: 없음 (기존 데이터셋 무결성 유지)\n")
            lines.append("---\n")
            continue
            
        lines.append(f"- **정제 완료 컬럼 수**: {len(mods)}개 컬럼")
        lines.append("\n| 컬럼명 | 정제 유형 | 적용 건수 | 세부 변환 내역 및 샘플 |")
        lines.append("| :--- | :---: | :---: | :--- |")
        for col, info in mods.items():
            lines.append(f"| `{col}` | {info['type']} | **{info['count']:,}건** | {info['sample']} |")
        lines.append("\n---\n")
        
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    run_v4_preprocessing()
