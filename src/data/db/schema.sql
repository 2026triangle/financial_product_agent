-- =============================================================================
-- 금융상품 통합 마스터 데이터베이스 스키마 (Financial Product Master Schema)
-- Description: AI 에이전트 질의응답(RAG/Text-to-SQL) 지원을 위한 4대 금융상품 마스터 스키마
-- Target DBMS: PostgreSQL / MySQL / SQLite 공용 호환 ANSI SQL
-- Tables:
--   1. tb_domestic_bond     : 국내채권 마스터 (PK: pd_no)
--   2. tb_domestic_etf      : 국내ETF 마스터  (PK: pd_itm_no)
--   3. tb_foreign_etf       : 해외ETF 마스터  (PK: pd_itm_no)
--   4. tb_fund              : 공모펀드 본 마스터 (PK: itm_no)
--   5. tb_fund_attribute    : 공모펀드 우대/테마 속성코드 매핑 (PK: itm_no, prfd_attr_cd)
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. 국내채권 마스터 (tb_domestic_bond)
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS tb_domestic_bond;

CREATE TABLE tb_domestic_bond (
    pd_no                   VARCHAR(20)     NOT NULL,   -- 채권단축코드 (Primary Key, 예: KR6000011D25)
    pd_exg_mkt              VARCHAR(20),                -- 거래시장구분 (상장 / 장외)
    pd_nm                   VARCHAR(255),               -- 채권정식명칭 (예: 국고채권01125-3906(24-4))
    pd_abrv_nm              VARCHAR(100),               -- 상품약어명 (HTS/MTS 표기명)
    pd_eng_nm               VARCHAR(255),               -- 채권영문명
    pd_abrv_eng_nm          VARCHAR(100),               -- 채권약어영문명
    pd_ctry_cd              VARCHAR(20),                -- 발행국가코드 (KR, XS 등)
    pd_pbcm                 VARCHAR(100),               -- 발행기관명 (예: 대한민국정부, 한국전력공사)
    std_pd_mcls_nm          VARCHAR(50),                -- 표준상품중분류명 (채권)
    std_pd_scls_nm          VARCHAR(50),                -- 표준상품소분류명 (국채, 특수채, 회사채 등)
    bd_knd                  VARCHAR(100),               -- 채권종류 (51종 상세분류)
    curr_cd                 VARCHAR(20),                -- 액면통화코드 (KRW, USD, EUR 등)
    isu_bal_amt             NUMERIC(25, 4),             -- 발행잔액 (원)
    isu_dt                  DATE,                       -- 발행일자 (YYYY-MM-DD)
    mat_dt                  DATE,                       -- 만기일자 (YYYY-MM-DD, 영구채는 9999-12-31)
    srfc_irt                NUMERIC(10, 4),             -- 표면금리/쿠폰율 (%)
    pd_evco_crd_grd         VARCHAR(50),                -- 평가사신용등급 (3사 평가)
    pd_risk_gcd             INTEGER,                    -- 상품위험등급코드 (1~6)
    pd_std_info_update      DATE,                       -- 기준정보갱신일자
    buy_yield               NUMERIC(10, 4),             -- 당사매수수익률 (%)
    corp_pretax_yield       NUMERIC(10, 4),             -- 법인세전수익률 (%)
    corp_after_tax_yield    NUMERIC(10, 4),             -- 법인세후수익률 (%)
    after_tax_yield         NUMERIC(10, 4),             -- 개인세후수익률 (%)
    pref_tax_yield          NUMERIC(10, 4),             -- 세금우대수익률 (%)
    avg_annual_tax_yield    NUMERIC(10, 4),             -- 연평균세후수익률 (%)
    depo_equiv_yield_154    NUMERIC(10, 4),             -- 은행예금환산수익률 (%)
    buyable_quantity        NUMERIC(25, 4),             -- 매수가능수량 (주/좌)
    remaining_days          INTEGER,                    -- 만기잔여일수 (일)
    dur                     NUMERIC(10, 4),             -- 수정듀레이션 (년)
    cov                     NUMERIC(10, 4),             -- 볼록성 (Convexity)
    ndy_dur                 NUMERIC(10, 4),             -- 익일듀레이션 (년)
    ndy_cov                 NUMERIC(10, 4),             -- 익일볼록성
    eval_price              NUMERIC(15, 4),             -- 당일평가단가 (원)
    applied_yield           NUMERIC(10, 4),             -- 당일적용수익률 (%)
    dirty                   NUMERIC(15, 4),             -- 당일더티프라이스 (원)
    ndy_eval_price          NUMERIC(15, 4),             -- 익일평가단가 (원)
    ndy_applied_yield       NUMERIC(10, 4),             -- 익일적용수익률 (%)
    ndy_dirty               NUMERIC(15, 4),             -- 익일더티프라이스 (원)
    crd_grd                 VARCHAR(50),                -- 대표신용등급 (AAA, AA+, A0 등)
    crd_grd_dt              DATE,                       -- 신용등급평가일자
    CONSTRAINT pk_tb_domestic_bond PRIMARY KEY (pd_no)
);

CREATE INDEX idx_bond_nm ON tb_domestic_bond(pd_nm);
CREATE INDEX idx_bond_abrv_nm ON tb_domestic_bond(pd_abrv_nm);
CREATE INDEX idx_bond_pbcm ON tb_domestic_bond(pd_pbcm);
CREATE INDEX idx_bond_scls ON tb_domestic_bond(std_pd_scls_nm);
CREATE INDEX idx_bond_mat_dt ON tb_domestic_bond(mat_dt);
CREATE INDEX idx_bond_crd_grd ON tb_domestic_bond(crd_grd);


-- -----------------------------------------------------------------------------
-- 2. 국내ETF 마스터 (tb_domestic_etf)
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS tb_domestic_etf;

CREATE TABLE tb_domestic_etf (
    pd_itm_no               VARCHAR(20)     NOT NULL,   -- 표준종목코드 (Primary Key, ISIN, 예: KR7069500007)
    pd_itm_no_ma            VARCHAR(20),                -- 단축종목코드 (Ticker, 예: A069500)
    pd_nm                   VARCHAR(255),               -- ETF 공식 상품명
    pd_abrv_nm              VARCHAR(100),               -- 종목약어명 (예: KODEX 200, TIGER 미국S&P500)
    pd_eng_nm               VARCHAR(255),               -- 상품영문명
    cu_fund_mgmt_co         VARCHAR(100),               -- 운용사명 (예: 미래에셋자산운용, 삼성자산운용)
    cu_base_index           VARCHAR(255),               -- 기초지수명 (예: KOSPI 200, S&P 500)
    cu_charge_rt            NUMERIC(10, 4),             -- 총보수율 (TER, %)
    cu_charge_etc_rt        NUMERIC(10, 4),             -- 기타비용율 (%)
    cu_strtegy              VARCHAR(255),               -- 운용전략설명
    cu_lev_fector           NUMERIC(10, 4),             -- 레버리지배수 (1, 2, -1, -2 등)
    cu_upt_dt               DATE,                       -- 운용정보 갱신일자
    pd_lstg_dt              DATE,                       -- 최초 상장일자
    pd_lste_dt              DATE,                       -- 상장폐지일자
    pd_sale_yn              VARCHAR(5),                 -- 판매/거래가능여부 (Y/N)
    pd_tr_yn                VARCHAR(5),                 -- 거래정지여부 (Y/N)
    pd_pen_tr_yn            VARCHAR(5),                 -- 연금계좌거래가능여부 (Y/N)
    pd_pen_risk_nm          VARCHAR(50),                -- 연금거래위험구분 (안전자산 / 위험자산 / N)
    pd_risk_cd              VARCHAR(20),                -- 위험등급코드
    pd_risk_nm              VARCHAR(50),                -- 위험등급명 (매우높은위험 등)
    pd_spac_yn              VARCHAR(5),                 -- 스팩여부 (Y/N)
    pd_sect_cd              VARCHAR(20),                -- 섹터코드
    pd_curr_cd              VARCHAR(20),                -- 통화코드 (KRW 등)
    pd_curr_nm              VARCHAR(50),                -- 통화명
    pd_exg_mkt_cd           VARCHAR(20),                -- 거래소시장코드
    pd_exg_mkt_nm           VARCHAR(50),                -- 거래소시장명
    pd_grp_no               VARCHAR(50),                -- 그룹번호
    pd_mkt_id               VARCHAR(50),                -- 시장ID
    pd_mkt_nm               VARCHAR(100),               -- 시장명
    wu_core_yn              VARCHAR(5),                 -- 핵심추천ETF여부 (Y/N)
    wu_inv_ast_type         VARCHAR(50),                -- 투자자산군 (국내주식, 해외주식, 채권, 원자재 등)
    wu_inv_rgn              VARCHAR(50),                -- 투자지역 (국내, 미국, 글로벌, 중국 등)
    wu_upt_dt               DATE,                       -- 추천정보 갱신일자
    pd_lst_price            NUMERIC(15, 4),             -- 상장원금/상장가 (원)
    pd_lst_stk_cnt          NUMERIC(25, 4),             -- 상장주식수 (주)
    pd_stk_cnt              NUMERIC(25, 4),             -- 총발행주식수 (주)
    pd_circ_stk_cnt         NUMERIC(25, 4),             -- 유통주식수 (주)
    pd_circ_net_tamt        NUMERIC(25, 4),             -- 유통시가총액 (원)
    pd_net_tamt             NUMERIC(25, 4),             -- 시가총액 (원)
    pd_nav_pshr             NUMERIC(15, 4),             -- 주당 순자산가치 (NAV, 원)
    pd_net_ast_pshr         NUMERIC(15, 4),             -- 주당 순자산 (원)
    pd_net_rt_ast_pshr      NUMERIC(15, 4),             -- 주당 실질순자산 (원)
    pd_net_prft_pshr        NUMERIC(15, 4),             -- 주당 당기순이익 (원)
    pd_divd_amt_pshr        NUMERIC(15, 4),             -- 주당 분배금 (원)
    pd_dvid_yield           NUMERIC(10, 4),             -- 분배율/배당수익률 (%)
    du_clpr                 NUMERIC(15, 4),             -- 종가 (원)
    du_bpr                  NUMERIC(15, 4),             -- 기준가 (원)
    du_hpr                  NUMERIC(15, 4),             -- 고가 (원)
    du_lpr                  NUMERIC(15, 4),             -- 저가 (원)
    du_diff_rt              NUMERIC(10, 4),             -- 괴리율 (%)
    du_chas_errt            NUMERIC(10, 4),             -- 추적오차율 (%)
    du_last_nav             NUMERIC(15, 4),             -- 최종 NAV (원)
    du_nav_yday             NUMERIC(15, 4),             -- 전일 NAV (원)
    du_last_aum             NUMERIC(25, 4),             -- 최종 AUM/순자산총액 (원)
    du_nav_rnf_amt          NUMERIC(25, 4),             -- NAV 증감액 (원)
    du_er_1d                NUMERIC(10, 4),             -- 1일 수익률 (%)
    du_er_1m                NUMERIC(10, 4),             -- 1개월 수익률 (%)
    du_er_3m                NUMERIC(10, 4),             -- 3개월 수익률 (%)
    du_er_6m                NUMERIC(10, 4),             -- 6개월 수익률 (%)
    du_er_1y                NUMERIC(10, 4),             -- 1년 수익률 (%)
    du_er_ytd               NUMERIC(10, 4),             -- 연초대비 수익률 (YTD, %)
    du_vol_1d               NUMERIC(25, 4),             -- 1일 거래량 (주)
    du_val_1d               NUMERIC(25, 4),             -- 1일 거래대금 (원)
    du_vol_avg_5d           NUMERIC(25, 4),             -- 5일 평균 거래량 (주)
    du_val_5d               NUMERIC(25, 4),             -- 5일 평균 거래대금 (원)
    du_vol_avg_1m           NUMERIC(25, 4),             -- 1개월 평균 거래량 (주)
    du_val_1m               NUMERIC(25, 4),             -- 1개월 평균 거래대금 (원)
    du_upt_dt               DATE,                       -- 시세정보 갱신일자
    CONSTRAINT pk_tb_domestic_etf PRIMARY KEY (pd_itm_no)
);

CREATE INDEX idx_etf_kr_ticker ON tb_domestic_etf(pd_itm_no_ma);
CREATE INDEX idx_etf_kr_nm ON tb_domestic_etf(pd_nm);
CREATE INDEX idx_etf_kr_abrv_nm ON tb_domestic_etf(pd_abrv_nm);
CREATE INDEX idx_etf_kr_mgmt_co ON tb_domestic_etf(cu_fund_mgmt_co);
CREATE INDEX idx_etf_kr_base_idx ON tb_domestic_etf(cu_base_index);
CREATE INDEX idx_etf_kr_ast_type ON tb_domestic_etf(wu_inv_ast_type);
CREATE INDEX idx_etf_kr_rgn ON tb_domestic_etf(wu_inv_rgn);


-- -----------------------------------------------------------------------------
-- 3. 해외ETF 마스터 (tb_foreign_etf)
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS tb_foreign_etf;

CREATE TABLE tb_foreign_etf (
    pd_itm_no               VARCHAR(20)     NOT NULL,   -- 글로벌 티커 (Primary Key, Ticker, 예: SPY, QQQ)
    pd_isin_cd              VARCHAR(20),                -- 국제표준종목코드 (ISIN, 예: US78462F1030)
    pd_itm_no_ma            VARCHAR(20),                -- 단축종목코드
    pd_nm                   VARCHAR(255),               -- 공식 상품명 (예: SPDR S&P 500 ETF Trust)
    pd_abrv_nm              VARCHAR(100),               -- 약어 상품명
    pd_us_cik               VARCHAR(20),                -- 미국 SEC CIK 번호 (10자리 표준 식별자)
    pd_lipper_id            VARCHAR(30),                -- Refinitiv Lipper 식별자
    pd_curr_cd              VARCHAR(20),                -- 상장통화 (USD, EUR, HKD 등)
    pd_trd_ccy              VARCHAR(20),                -- 거래통화
    pd_exg_mkt_cd           VARCHAR(20),                -- 거래소시장코드
    pd_grp_no               VARCHAR(50),                -- 그룹번호
    pd_mkt_id               VARCHAR(50),                -- 시장ID
    cu_fund_mgmt_co         VARCHAR(100),               -- 글로벌 운용사 (BlackRock, Vanguard, State Street 등)
    cu_base_index           VARCHAR(255),               -- 기초 벤치마크 지수 (S&P 500, Nasdaq 100 등)
    cu_charge_rt            NUMERIC(10, 4),             -- 총보수율(Expense Ratio, %)
    cu_etn_yn               VARCHAR(5),                 -- ETN 여부 (Y/N)
    cu_inverse_short_yn     VARCHAR(5),                 -- 인버스/숏 전략 여부 (Y/N)
    cu_index_tracking_yn    VARCHAR(5),                 -- 패시브 지수추종 여부 (Y/N)
    cu_index_repl_mthd      VARCHAR(50),                -- 지수복제방식 (Full, Optimized, Swap 등)
    cu_strtegy              TEXT,                       -- 운용전략설명 (Dense Vector 임베딩 및 자연어 질의 대상)
    cu_upt_dt               DATE,                       -- 운용정보 갱신일자
    pd_lstg_dt              DATE,                       -- 해외 상장일자
    pd_sale_yn              VARCHAR(5),                 -- 거래가능여부 (Y/N)
    pd_tr_yn                VARCHAR(5),                 -- 거래정지여부 (Y/N)
    wu_core_yn              VARCHAR(5),                 -- 핵심추천종목여부 (Y/N)
    wu_inv_ast_type         VARCHAR(50),                -- 투자자산군 (Equity, Bond, Alternatives, Commodity)
    wu_inv_rgn              VARCHAR(50),                -- 투자지역 (United States, Global, China 등)
    wu_upt_dt               DATE,                       -- 분류정보 갱신일자
    pd_lst_price            NUMERIC(15, 4),             -- 상장가격
    pd_lst_stk_cnt          NUMERIC(25, 4),             -- 상장주식수
    ru_mkt_price            NUMERIC(15, 4),             -- 실시간 시장가격
    ru_mkt_volume           NUMERIC(25, 4),             -- 실시간 시장거래량
    du_clpr                 NUMERIC(15, 4),             -- 종가
    du_bpr                  NUMERIC(15, 4),             -- 기준가
    du_hpr                  NUMERIC(15, 4),             -- 고가
    du_lpr                  NUMERIC(15, 4),             -- 저가
    du_opr                  NUMERIC(15, 4),             -- 시가
    du_diff_rt              NUMERIC(10, 4),             -- 괴리율 (%)
    du_er_1d                NUMERIC(10, 4),             -- 1일 수익률 (%)
    du_er_1y                NUMERIC(10, 4),             -- 1년 수익률 (%)
    du_last_nav             NUMERIC(15, 4),             -- 최종 NAV
    du_last_aum             NUMERIC(25, 4),             -- 최종 AUM / 순자산규모
    du_val_1d               NUMERIC(25, 4),             -- 1일 거래대금
    du_vol_1d               NUMERIC(25, 4),             -- 1일 거래량
    du_clpr_base_dt         DATE,                       -- 종가 산출 기준일자
    du_clpr_src             VARCHAR(50),                -- 종가 소스
    du_nav_base_dt          DATE,                       -- NAV 산출 기준일자
    du_base_dt_match_yn     VARCHAR(5),                 -- 기준일자 일치여부 (Y/N)
    du_upt_dt               DATE,                       -- 성과정보 갱신일자
    CONSTRAINT pk_tb_foreign_etf PRIMARY KEY (pd_itm_no)
);

CREATE INDEX idx_etf_ovrs_isin ON tb_foreign_etf(pd_isin_cd);
CREATE INDEX idx_etf_ovrs_nm ON tb_foreign_etf(pd_nm);
CREATE INDEX idx_etf_ovrs_abrv ON tb_foreign_etf(pd_abrv_nm);
CREATE INDEX idx_etf_ovrs_mgmt_co ON tb_foreign_etf(cu_fund_mgmt_co);
CREATE INDEX idx_etf_ovrs_base_idx ON tb_foreign_etf(cu_base_index);
CREATE INDEX idx_etf_ovrs_ast_type ON tb_foreign_etf(wu_inv_ast_type);
CREATE INDEX idx_etf_ovrs_rgn ON tb_foreign_etf(wu_inv_rgn);


-- -----------------------------------------------------------------------------
-- 4. 공모펀드 본 마스터 (tb_fund) - 2단계 구조 본 테이블 (11,138건)
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS tb_fund_attribute;
DROP TABLE IF EXISTS tb_fund;

CREATE TABLE tb_fund (
    itm_no                  VARCHAR(20)     NOT NULL,   -- 펀드표준코드 (Primary Key, ISIN, 예: KR5116450039)
    itm_nm                  VARCHAR(255),               -- 펀드정식명칭
    itm_abrv_nm             VARCHAR(100),               -- 펀드약어명 (HTS/MTS 표기명)
    itm_eng_nm              VARCHAR(255),               -- 펀드영문명
    itm_eabrv_nm            VARCHAR(100),               -- 펀드약어영문명
    std_itm_no              VARCHAR(20),                -- 표준상품코드
    ksd_itm_no              VARCHAR(20),                -- 예탁원코드
    rptt_ksd_itm_no         VARCHAR(20),                -- 대표예탁원코드 (모펀드 식별코드)
    fss_itm_no              VARCHAR(50),                -- 금융감독원 DART 공시코드
    kofia_fd_ccd            VARCHAR(50),                -- 금융투자협회 분류코드
    mtco_itm_no             VARCHAR(50),                -- 운용사 내부 종목코드
    or_co_xtn_itt_cd        VARCHAR(20),                -- 운용사 대외기관코드 (8자리)
    trusc_xtn_itt_cd        VARCHAR(20),                -- 수탁회사 대외기관코드 (8자리)
    bmrk_nm                 VARCHAR(255),               -- 벤치마크 지수명 (예: KOSPI 200)
    bmrk_eng_nm             VARCHAR(255),               -- 벤치마크 영문명
    curr_cd                 VARCHAR(20),                -- 기준통화코드 (KRW, USD 등)
    exchdg_yn               VARCHAR(5),                 -- 환헤지적용여부 (Y/N/해당없음)
    ovrs_fd_desc            VARCHAR(50),                -- 해외펀드구분 (국내, 해외, 국내외혼합)
    fd_ivst_rgn_desc        VARCHAR(50),                -- 투자지역상세 (글로벌, 국내, 미국/북미, 중국 등)
    or_attr_desc            VARCHAR(50),                -- 운용자산유형 (주식형, 채권형, 혼합형, MMF 등)
    fd_estb_ctry_cd         VARCHAR(10),                -- 펀드설립국가코드 (000: 국내, 410: 해외)
    fd_set_pcd              VARCHAR(20),                -- 펀드설정유형코드 (10: 추가형, 20: 단위형)
    prvo_pbff_desc          VARCHAR(50),                -- 공모/사모구분 (공모)
    prvo_fd_desc            VARCHAR(50),                -- 사모펀드구분
    zrin_fd_ivst_risk_gcd   VARCHAR(10),                -- 위험등급코드 (1~6)
    zrin_fd_ivst_risk_grd_nm VARCHAR(50),               -- 위험등급명 (매우 높은 위험, 높은 위험 등)
    sale_yn                 VARCHAR(50),                -- 판매상태 (판매중 / 판매완료)
    thco_sale_yn            VARCHAR(5),                 -- 당사(미래에셋)판매여부 (Y/N)
    frc_bpr_itm_yn          VARCHAR(5),                 -- 외화기준가여부 (Y/N)
    hdge_fd_yn              VARCHAR(5),                 -- 헤지펀드여부 (N)
    ofsfd_yn                VARCHAR(5),                 -- 역외펀드여부 (N)
    pers_corp_desc          VARCHAR(50),                -- 개인/법인구분 (전체, 개인전용, 법인전용)
    pfiv_sale_cntl_tcd      VARCHAR(20),                -- 전문투자자판매제어코드 (00: 일반, 01/02: 전문)
    int_dvd_desc            VARCHAR(50),                -- 이자/배당구분
    fd_nast_suma            NUMERIC(25, 4),             -- 순자산총액/설정액 (원)
    fd_wk1_ern_r            NUMERIC(10, 4),             -- 1주일 수익률 (%)
    fd_mm1_ern_r            NUMERIC(10, 4),             -- 1개월 수익률 (%)
    fd_mm3_ern_r            NUMERIC(10, 4),             -- 3개월 수익률 (%)
    fd_mm6_ern_r            NUMERIC(10, 4),             -- 6개월 수익률 (%)
    fd_mm18_ern_r           NUMERIC(10, 4),             -- 18개월 수익률 (%)
    fd_yr1_ern_r            NUMERIC(10, 4),             -- 1년 수익률 (%)
    fd_yr2_ern_r            NUMERIC(10, 4),             -- 2년 수익률 (%)
    fd_yr3_ern_r            NUMERIC(10, 4),             -- 3년 수익률 (%)
    fd_yr5_ern_r            NUMERIC(10, 4),             -- 5년 수익률 (%)
    CONSTRAINT pk_tb_fund PRIMARY KEY (itm_no)
);

CREATE INDEX idx_fund_nm ON tb_fund(itm_nm);
CREATE INDEX idx_fund_abrv_nm ON tb_fund(itm_abrv_nm);
CREATE INDEX idx_fund_rptt_ksd ON tb_fund(rptt_ksd_itm_no);
CREATE INDEX idx_fund_rgn ON tb_fund(fd_ivst_rgn_desc);
CREATE INDEX idx_fund_attr ON tb_fund(or_attr_desc);
CREATE INDEX idx_fund_risk_gcd ON tb_fund(zrin_fd_ivst_risk_gcd);
CREATE INDEX idx_fund_sale_yn ON tb_fund(sale_yn);
CREATE INDEX idx_fund_thco_sale ON tb_fund(thco_sale_yn);


-- -----------------------------------------------------------------------------
-- 5. 공모펀드 속성코드 매핑 테이블 (tb_fund_attribute) - 1:N 관계 매핑 (95,618건)
-- -----------------------------------------------------------------------------
CREATE TABLE tb_fund_attribute (
    itm_no                  VARCHAR(20)     NOT NULL,   -- 펀드표준코드 (FK, tb_fund.itm_no)
    prfd_attr_cd            VARCHAR(20)     NOT NULL,   -- 공모펀드속성코드 (예: F103, N143, V102)
    CONSTRAINT pk_tb_fund_attribute PRIMARY KEY (itm_no, prfd_attr_cd),
    CONSTRAINT fk_fund_attr_itm_no FOREIGN KEY (itm_no) 
        REFERENCES tb_fund (itm_no) ON DELETE CASCADE
);

CREATE INDEX idx_fund_attr_cd ON tb_fund_attribute(prfd_attr_cd);
CREATE INDEX idx_fund_attr_itm ON tb_fund_attribute(itm_no);


-- -----------------------------------------------------------------------------
-- 6. 금융상품 메타 벡터 데이터베이스 테이블 (meta_schema_table)
-- Description: 자연어 질의-컬럼 라우팅을 위한 컬럼 설명/예시/예상질문 및 BGE-M3 Dense/Sparse 벡터 저장소
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS meta_schema_table;

CREATE TABLE meta_schema_table (
    table_name              VARCHAR(50)     NOT NULL,   -- 대상 테이블명 (예: tb_domestic_bond, tb_fund 등)
    column_name             VARCHAR(50)     NOT NULL,   -- 대상 컬럼명 (예: pd_nm, buy_yield, fd_yr1_ern_r 등)
    column_korean_name      VARCHAR(100),               -- 컬럼 한글명 (예: 당사매수수익률, 1년수익률 등)
    data_type               VARCHAR(50),                -- 데이터 타입 (예: NUMERIC(10,4), VARCHAR(255) 등)
    description             TEXT            NOT NULL,   -- 컬럼의 상세 비즈니스/금융 의미 및 역할 설명 원문
    synonyms                TEXT,                       -- 동의어/유의어/변형 키워드 리스트 (JSON Array: ["안전한 채권", "AAA", "부도위험"])
    sample_values           TEXT,                       -- 대표 예시 값 리스트 (JSON Array 문자열: ["3.25", "4.10"])
    sample_queries          TEXT,                       -- 해당 컬럼과 연관된 자연어 예상 질문 리스트 (JSON Array)
    embedding_text          TEXT,                       -- BGE-M3 임베딩에 투입된 구조화된 결합 텍스트 원문
    dense_vector            TEXT,                       -- BGE-M3 1024차원 Dense Vector (JSON Array 문자열)
    sparse_vector           TEXT,                       -- BGE-M3 Lexical Token Weights (JSON Object 문자열)
    created_at              TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_meta_schema_table PRIMARY KEY (table_name, column_name)
);

CREATE INDEX idx_meta_tbl ON meta_schema_table(table_name);
CREATE INDEX idx_meta_col ON meta_schema_table(column_name);
CREATE INDEX idx_meta_kor_nm ON meta_schema_table(column_korean_name);

