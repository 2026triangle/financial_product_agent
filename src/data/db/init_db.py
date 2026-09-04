"""
================================================================================
금융상품 마스터 데이터베이스 초기화 및 적재 스크립트 (init_db.py)
================================================================================
설명:
  1. 런타임 접속 정보(CLI 인자 / 환경변수 / 대화형 프롬프트)를 통해 DB에 연결합니다.
  2. schema.sql을 실행하여 4대 금융상품 마스터 테이블(5개 테이블)을 생성합니다.
  3. 지정된 전처리 엑셀 파일 디렉터리(--data-dir)에서 4종 데이터를 읽어 DB에 적재합니다.
  4. 공모펀드 데이터는 2단계 구조(본 마스터 tb_fund 11,138건 + 속성 매핑 tb_fund_attribute 95,618건)로
     자동 분리하여 적재합니다.
  5. 적재 완료 후 무결성 및 레코드 수를 자동으로 검증합니다.

재활용 가이드:
  - 향후 v5, v6 전처리 데이터 적재 시:
    python init_db.py --data-dir perprocessed_datas/v5 --drop-existing
================================================================================
"""

import os
import sys
import glob
import time
import argparse
import logging
from typing import Dict, List, Tuple, Any, Optional
import numpy as np
import pandas as pd

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("init_db")


# ------------------------------------------------------------------------------
# 1. DB 연결 추상화 클래스 (SQLite / PostgreSQL / MySQL)
# ------------------------------------------------------------------------------
class DBConnector:
    """다양한 DB 백엔드(SQLite, PostgreSQL, MySQL)를 통일된 인터페이스로 다루는 연결 관리자"""

    def __init__(self, db_type: str = "postgresql", **kwargs):
        self.db_type = db_type.lower()
        self.kwargs = kwargs
        self.conn = None
        self.engine = None
        self._connect()

    def _connect(self):
        if self.db_type in ("sqlite", "sqlite3"):
            import sqlite3
            db_path = self.kwargs.get("db_path", "db/finance_master.db")
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            logger.info(f"[SQLite] 로컬 데이터베이스 파일 연결: {db_path}")
            self.conn = sqlite3.connect(db_path)
            self.conn.execute("PRAGMA foreign_keys = ON;")
        elif self.db_type in ("postgresql", "postgres", "mysql", "mariadb"):
            import urllib.parse
            db_url = self.kwargs.get("db_url")
            database = self.kwargs.get("database", "finance_master")
            host = self.kwargs.get("host", "localhost")
            port = self.kwargs.get("port")
            if not port:
                port = 5432 if "postgres" in self.db_type else 3306
            user = self.kwargs.get("user", "postgres" if "postgres" in self.db_type else "root")
            password = self.kwargs.get("password", "")

            if not db_url:
                # 비밀번호 특수문자 URL 인코딩 처리
                encoded_password = urllib.parse.quote_plus(str(password)) if password else ""
                encoded_user = urllib.parse.quote_plus(str(user))
                driver = "postgresql+psycopg2" if "postgres" in self.db_type else "mysql+pymysql"
                db_url = f"{driver}://{encoded_user}:{encoded_password}@{host}:{port}/{database}"

            try:
                from sqlalchemy import create_engine, text
                from sqlalchemy.exc import OperationalError

                logger.info(f"[{self.db_type.upper()}] SQLAlchemy 엔진 연결 중... (host: {host}:{port}, db: {database}, user: {user})")
                
                # PostgreSQL의 경우 대상 데이터베이스가 없을 때 자동 생성 시도
                if "postgres" in self.db_type and not self.kwargs.get("db_url"):
                    try:
                        self.engine = create_engine(db_url, pool_pre_ping=True)
                        self.conn = self.engine.raw_connection()
                    except OperationalError as oe:
                        err_msg = str(oe).lower()
                        if "does not exist" in err_msg or "database" in err_msg:
                            logger.info(f"[{self.db_type.upper()}] 데이터베이스 '{database}'가 존재하지 않아 생성을 시도합니다...")
                            default_db_url = f"postgresql+psycopg2://{encoded_user}:{encoded_password}@{host}:{port}/postgres"
                            admin_engine = create_engine(default_db_url, isolation_level="AUTOCOMMIT")
                            with admin_engine.connect() as admin_conn:
                                admin_conn.execute(text(f'CREATE DATABASE "{database}"'))
                            logger.info(f"✅ 데이터베이스 '{database}' 생성 완료.")
                            self.engine = create_engine(db_url, pool_pre_ping=True)
                            self.conn = self.engine.raw_connection()
                        else:
                            raise
                else:
                    self.engine = create_engine(db_url, pool_pre_ping=True)
                    self.conn = self.engine.raw_connection()

            except ImportError:
                logger.error(
                    f"\n[오류] {self.db_type} 접속을 위해 SQLAlchemy 및 전용 드라이버가 필요합니다.\n"
                    f"설치 명령: pip install sqlalchemy "
                    f"{'psycopg2-binary' if 'postgres' in self.db_type else 'pymysql'}\n"
                )
                sys.exit(1)
            except Exception as e:
                logger.error(f"\n❌ [{self.db_type.upper()} 연결 실패] 상세 오류 원인:\n{e}\n")
                logger.error(
                    "확인 사항:\n"
                    "  1. PostgreSQL / MySQL 서버가 실행 중인지 확인 (호스트/포트)\n"
                    "  2. DB 사용자 계정(User) 및 비밀번호(Password) 일치 여부 확인\n"
                    "  3. 데이터베이스(Database) 존재 여부 및 접근 권한 확인\n"
                )
                raise
        else:
            raise ValueError(f"지원하지 않는 DB 타입입니다: {self.db_type} (sqlite, postgresql, mysql 지원)")

    def execute_script(self, sql_script: str):
        """DDL 스크립트 실행"""
        if self.db_type in ("sqlite", "sqlite3"):
            cursor = self.conn.cursor()
            cursor.executescript(sql_script)
            self.conn.commit()
        else:
            # PostgreSQL / MySQL
            statements = [stmt.strip() for stmt in sql_script.split(";") if stmt.strip()]
            cursor = self.conn.cursor()
            for stmt in statements:
                try:
                    cursor.execute(stmt)
                except Exception as e:
                    logger.warning(f"SQL 실행 중 경고/에러: {e} (SQL: {stmt[:60]}...)")
            self.conn.commit()

    def execute_query(self, query: str, params: Optional[Tuple] = None) -> List[Tuple]:
        cursor = self.conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        return cursor.fetchall()

    def bulk_insert(self, table_name: str, columns: List[str], data_rows: List[Tuple], batch_size: int = 1000):
        """고속 배치 INSERT"""
        if not data_rows:
            return

        cursor = self.conn.cursor()
        col_names = ", ".join(columns)
        
        if self.db_type in ("sqlite", "sqlite3"):
            placeholders = ", ".join(["?"] * len(columns))
            sql = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"
        else:
            # PostgreSQL / MySQL
            placeholders = ", ".join(["%s"] * len(columns))
            sql = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"

        total = len(data_rows)
        for i in range(0, total, batch_size):
            batch = data_rows[i:i + batch_size]
            cursor.executemany(sql, batch)
        self.conn.commit()

    def sync_columns(self, table_name: str, columns: List[str], data_rows: List[Tuple]):
        """
        적재하려는 데이터의 컬럼과 테이블의 실제 컬럼을 비교해서, 테이블에 없는 컬럼은
        자동으로 ALTER TABLE ADD COLUMN으로 추가한다. raw 데이터 구조가 바뀌어도(컬럼 추가)
        schema.sql을 매번 손으로 안 고쳐도 되게 하기 위함. schema.sql은 여전히 "정식 문서"로
        유지하고, 자동 추가된 컬럼은 여유 될 때 정식 타입/코멘트로 반영 권장.
        """
        if self.db_type not in ("sqlite", "sqlite3"):
            logger.warning(f"[{table_name}] sync_columns는 현재 SQLite만 지원합니다. 수동 확인 필요.")
            return
        if not data_rows:
            return

        cursor = self.conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing_cols = {row[1].lower() for row in cursor.fetchall()}

        missing = [c for c in columns if c.lower() not in existing_cols]
        if not missing:
            return

        col_index = {c: i for i, c in enumerate(columns)}
        for col in missing:
            sample = None
            idx = col_index[col]
            for row in data_rows:
                v = row[idx]
                if v is not None:
                    sample = v
                    break
            sql_type = self._guess_sql_type(sample)
            logger.warning(
                f"⚠️  [{table_name}] 스키마에 없던 컬럼을 자동 추가: {col} ({sql_type}) "
                f"— 나중에 db/schema.sql에도 정식 타입/설명 반영 권장"
            )
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col} {sql_type}")
        self.conn.commit()

    @staticmethod
    def _guess_sql_type(sample: Any) -> str:
        """실제 데이터 샘플 값으로 대략적인 SQL 타입을 추정 (참고용, 정밀 타입은 schema.sql에서 직접 확정)"""
        if isinstance(sample, bool):
            return "VARCHAR(10)"
        if isinstance(sample, int):
            return "INTEGER"
        if isinstance(sample, float):
            return "NUMERIC(18, 4)"
        return "VARCHAR(255)"

    def close(self):
        if self.conn:
            self.conn.close()


# ------------------------------------------------------------------------------
# 2. 데이터 파일 로더 및 정제 전처리기
# ------------------------------------------------------------------------------
class DataLoader:
    """엑셀 파일 검색 및 DB 테이블별 데이터프레임 변환기"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        if not os.path.isdir(data_dir):
            raise FileNotFoundError(f"지정된 데이터 디렉터리를 찾을 수 없습니다: {data_dir}")

    def find_file(self, pattern: str) -> str:
        files = glob.glob(os.path.join(self.data_dir, pattern))
        if not files:
            raise FileNotFoundError(f"패턴과 일치하는 엑셀 파일을 찾을 수 없습니다: {os.path.join(self.data_dir, pattern)}")
        return files[0]

    @staticmethod
    def clean_value(val: Any) -> Any:
        """NaN, NaT, inf 및 비정상 값을 SQL NULL(None)으로 변환"""
        if pd.isna(val):
            return None
        if isinstance(val, (np.floating, float)):
            if np.isnan(val) or np.isinf(val):
                return None
            return float(val)
        if isinstance(val, (np.integer, int)):
            return int(val)
        if isinstance(val, (pd.Timestamp, np.datetime64)):
            return val.strftime("%Y-%m-%d")
        if isinstance(val, str):
            val = val.strip()
            return val if val else None
        return val

    def load_bond_data(self) -> Tuple[List[str], List[Tuple]]:
        """1. 국내채권 마스터 데이터 로드"""
        fp = self.find_file("prbd01n001_data.xlsx")
        logger.info(f"국내채권 마스터 로드 중: {os.path.basename(fp)}")
        df = pd.read_excel(fp)

        # 컬럼명을 소문자로 통일하여 매핑
        df.columns = [c.lower() for c in df.columns]

        # 완전히 빈 행 제거
        df = df.dropna(subset=['pd_no'])

        # --- pd_no 중복 처리 ---
        # 새 raw 데이터는 채권 1개(pd_no)당 시장구분(pd_exg_mkt: 장내/장외)별로
        # 별도의 시세 레코드가 붙어 있음. 채권 정체성 컬럼은 행마다 동일하고
        # 시세 관련 컬럼만 시장별로 다르게 채워져 있음.
        # -> 장내(거래소) 행을 대표로 채택, 없으면 info_seq가 가장 작은 장외 행을 채택.
        if 'pd_exg_mkt' in df.columns:
            # 정렬 우선순위: 장내(0) > 장외(1), 그다음 info_seq 오름차순
            market_rank = df['pd_exg_mkt'].map(lambda v: 0 if v == '장내' else 1)
            sort_cols = pd.DataFrame({
                '_market_rank': market_rank,
                '_info_seq': pd.to_numeric(df.get('info_seq'), errors='coerce').fillna(9999) if 'info_seq' in df.columns else 0,
            }, index=df.index)
            df = df.assign(**{c: sort_cols[c] for c in sort_cols.columns})
            df = df.sort_values(['pd_no', '_market_rank', '_info_seq'])
            before = len(df)
            df = df.drop_duplicates(subset=['pd_no'], keep='first')
            after = len(df)
            logger.info(f"pd_no 중복 제거: {before}행 -> {after}행 (시장구분 우선순위 적용)")
            df = df.drop(columns=['_market_rank', '_info_seq'])
        else:
            before = len(df)
            df = df.drop_duplicates(subset=['pd_no'], keep='first')
            after = len(df)
            if before != after:
                logger.warning(f"pd_exg_mkt 컬럼 없음 — 단순 first-keep으로 pd_no 중복 제거: {before}행 -> {after}행")

        # 데이터프레임 클린징
        cols = list(df.columns)
        rows = [tuple(self.clean_value(v) for v in row) for row in df.values]
        return cols, rows

    def load_domestic_etf_data(self) -> Tuple[List[str], List[Tuple]]:
        """2. 국내ETF 마스터 데이터 로드"""
        fp = self.find_file("pref01n001_data.xlsx")
        logger.info(f"국내ETF 마스터 로드 중: {os.path.basename(fp)}")
        df = pd.read_excel(fp)
        df.columns = [c.lower() for c in df.columns]
        df = df.dropna(subset=['pd_itm_no'])
        
        cols = list(df.columns)
        rows = [tuple(self.clean_value(v) for v in row) for row in df.values]
        return cols, rows

    def load_foreign_etf_data(self) -> Tuple[List[str], List[Tuple]]:
        """3. 해외ETF 마스터 데이터 로드"""
        fp = self.find_file("pref02n001_data.xlsx")
        logger.info(f"해외ETF 마스터 로드 중: {os.path.basename(fp)}")
        df = pd.read_excel(fp)
        df.columns = [c.lower() for c in df.columns]
        df = df.dropna(subset=['pd_itm_no'])
        
        cols = list(df.columns)
        rows = [tuple(self.clean_value(v) for v in row) for row in df.values]
        return cols, rows
        
    def load_fund_data(self) -> Tuple[Tuple[List[str], List[Tuple]], Tuple[List[str], List[Tuple]]]:
        """
        4. 공모펀드 마스터 2단계 1:N 분리 로드:
        - tb_fund (본 테이블, itm_no PK)
        - tb_fund_attribute (속성 테이블, itm_no + prfd_attr_cd PK)

        ※ 26.08.24 raw 교체: prfd_attr_cd(행당 1개, long format) -> prfd_attr_cds(쉼표구분 목록,
        wide format)로 원본 구조가 바뀜. tb_fund_attribute 스키마/PK는 그대로 유지하기 위해
        여기서 쉼표 목록을 예전처럼 (itm_no, prfd_attr_cd) 행들로 풀어준다(explode).
        """
        fp = self.find_file("prfd01n001_data.xlsx")
        logger.info(f"공모펀드 마스터 로드 및 2단계(1:N) 분리 중: {os.path.basename(fp)}")
        df = pd.read_excel(fp)
        df.columns = [c.lower() for c in df.columns]

        df = df.dropna(subset=['itm_no'])

        # prfd_attr_cnt는 파생값이라 굳이 저장 안 해도 되고, prfd_attr_search_text는
        # MCT 검색에 쓸모 있으니 본 테이블에 남긴다.
        fund_main_cols = [c for c in df.columns if c not in ('prfd_attr_cds', 'prfd_attr_cnt')]
        df_fund_main = df.drop_duplicates(subset=['itm_no'])[fund_main_cols]
        fund_main_rows = [tuple(self.clean_value(v) for v in row) for row in df_fund_main.values]

        attr_pairs = []
        for itm_no, cds_raw in df[['itm_no', 'prfd_attr_cds']].dropna().itertuples(index=False):
            for cd in str(cds_raw).split(','):
                cd = cd.strip()
                if cd:
                    attr_pairs.append((itm_no, cd))

        attr_cols = ['itm_no', 'prfd_attr_cd']
        df_attr = pd.DataFrame(attr_pairs, columns=attr_cols).drop_duplicates()
        attr_rows = [tuple(self.clean_value(v) for v in row) for row in df_attr.values]

        return (fund_main_cols, fund_main_rows), (attr_cols, attr_rows)

# ------------------------------------------------------------------------------
# 3. 데이터베이스 초기화 및 적재 오케스트레이터
# ------------------------------------------------------------------------------
def init_database(
    data_dir: str = "perprocessed_datas/v4",
    meta_dir: str = "db/meta_columns",
    schema_path: str = "db/schema.sql",
    db_type: str = "sqlite",
    drop_existing: bool = True,
    batch_size: int = 1000,
    with_meta: bool = True,
    **db_kwargs
):
    """DB 연결 -> DDL 스키마 생성 -> 엑셀 파싱 -> 데이터 적재 -> 무결성 검증"""
    start_time = time.time()
    logger.info("=" * 70)
    logger.info("🚀 금융상품 마스터 데이터베이스 적재 파이프라인 시작")
    logger.info(f"   • 데이터 소스 디렉터리 : {data_dir}")
    logger.info(f"   • 스키마 DDL 파일      : {schema_path}")
    logger.info(f"   • 대상 DBMS            : {db_type.upper()}")
    logger.info("=" * 70)

    # 1. DB 연결
    connector = DBConnector(db_type=db_type, **db_kwargs)

    # 2. schema.sql 실행
    if os.path.isfile(schema_path):
        logger.info(f"스키마 DDL 파일 적용 중 ({schema_path})...")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        connector.execute_script(schema_sql)
        logger.info("✅ 테이블 및 인덱스 생성 완료.")
    else:
        logger.warning(f"스키마 파일({schema_path})이 존재하지 않아 테이블 생성을 건너뜁니다.")

    # 3. 데이터 로드 및 적재
    loader = DataLoader(data_dir)

    # (1) 국내채권 마스터
    bond_cols, bond_rows = loader.load_bond_data()
    logger.info(f"📥 tb_domestic_bond 적재 중... ({len(bond_rows):,}건)")
    connector.sync_columns("tb_domestic_bond", bond_cols, bond_rows)
    connector.bulk_insert("tb_domestic_bond", bond_cols, bond_rows, batch_size=batch_size)

    # (2) 국내ETF 마스터
    etf_kr_cols, etf_kr_rows = loader.load_domestic_etf_data()
    logger.info(f"📥 tb_domestic_etf 적재 중... ({len(etf_kr_rows):,}건)")
    connector.sync_columns("tb_domestic_etf", etf_kr_cols, etf_kr_rows)
    connector.bulk_insert("tb_domestic_etf", etf_kr_cols, etf_kr_rows, batch_size=batch_size)

    # (3) 해외ETF 마스터
    etf_ovrs_cols, etf_ovrs_rows = loader.load_foreign_etf_data()
    logger.info(f"📥 tb_foreign_etf 적재 중... ({len(etf_ovrs_rows):,}건)")
    connector.sync_columns("tb_foreign_etf", etf_ovrs_cols, etf_ovrs_rows)
    connector.bulk_insert("tb_foreign_etf", etf_ovrs_cols, etf_ovrs_rows, batch_size=batch_size)

    # (4) 공모펀드 마스터 (본 테이블 + 속성 매핑 테이블)
    (fund_cols, fund_rows), (attr_cols, attr_rows) = loader.load_fund_data()
    logger.info(f"📥 tb_fund (본 테이블) 적재 중... ({len(fund_rows):,}건)")
    connector.sync_columns("tb_fund", fund_cols, fund_rows)
    connector.bulk_insert("tb_fund", fund_cols, fund_rows, batch_size=batch_size)

    logger.info(f"📥 tb_fund_attribute (속성 매핑 테이블) 적재 중... ({len(attr_rows):,}건)")
    connector.sync_columns("tb_fund_attribute", attr_cols, attr_rows)
    connector.bulk_insert("tb_fund_attribute", attr_cols, attr_rows, batch_size=batch_size)

    # 6. 메타 벡터 데이터베이스(meta_schema_table) 적재 (BGE-M3 임베딩)
    if with_meta:
        logger.info("\n" + "=" * 70)
        logger.info("🧠 [메타 벡터 DB] BGE-M3 기반 컬럼 메타데이터 임베딩 및 적재 시작...")
        logger.info("=" * 70)
        try:
            try:
                from db.embed_meta import insert_meta_to_db
            except ImportError:
                from embed_meta import insert_meta_to_db
            raw_conn = connector.conn
            meta_count = insert_meta_to_db(raw_conn, meta_dir=meta_dir)
            logger.info(f"✅ meta_schema_table {meta_count}개 컬럼 임베딩 적재 완료.")
        except Exception as me:
            logger.error(f"⚠️ 메타 벡터 DB 적재 중 오류 발생 (본 데이터 적재는 완료됨): {me}")

    # 7. 적재 결과 검증 및 리포트
    logger.info("\n" + "-" * 70)
    logger.info("📊 데이터베이스 적재 결과 및 정합성 검증 리포트")
    logger.info("-" * 70)

    tables = [
        ("tb_domestic_bond", "pd_no"),
        ("tb_domestic_etf", "pd_itm_no"),
        ("tb_foreign_etf", "pd_itm_no"),
        ("tb_fund", "itm_no"),
        ("tb_fund_attribute", "itm_no, prfd_attr_cd")
    ]
    if with_meta:
        tables.append(("meta_schema_table", "table_name, column_name"))

    for tbl, pk in tables:
        count_res = connector.execute_query(f"SELECT COUNT(*) FROM {tbl}")
        count = count_res[0][0] if count_res else 0
        logger.info(f"  • {tbl.ljust(22)} : {str(count).rjust(8)}건 적재 완료 (PK: {pk})")

    # 펀드 1:N 외래키 정합성 검증
    fk_check = connector.execute_query("""
        SELECT COUNT(*) 
        FROM tb_fund_attribute a 
        LEFT JOIN tb_fund f ON a.itm_no = f.itm_no 
        WHERE f.itm_no IS NULL
    """)
    orphan_count = fk_check[0][0] if fk_check else 0
    if orphan_count == 0:
        logger.info("  • [외래키 정합성 검증] tb_fund_attribute 고아 레코드(Orphan) 0건 (100% 일치) ✅")
    else:
        logger.error(f"  • [외래키 오류] tb_fund에 매칭되지 않는 속성 레코드 {orphan_count}건 발견! ❌")

    elapsed = time.time() - start_time
    logger.info("=" * 70)
    logger.info(f"🎉 모든 데이터 적재 및 검증이 성공적으로 완료되었습니다! (소요 시간: {elapsed:.2f}초)")
    logger.info("=" * 70)

    connector.close()


# ------------------------------------------------------------------------------
# 4. CLI 인터페이스 및 접속 정보 수신 파서
# ------------------------------------------------------------------------------
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="금융상품 마스터 데이터 DB 적재 프로그램 (AI Agent RAG/SQL 지원)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  1. 기본 로컬 SQLite 적재 (기본값, 메타 벡터 포함):
     python db/init_db.py

  2. SQLite 특정 경로 및 v5 전처리 데이터 적재:
     python db/init_db.py --data-dir perprocessed_datas/v5 --db-path db/finance_master_v5.db

  3. PostgreSQL 원격 DB 적재:
     python db/init_db.py --db-type postgresql --host localhost --port 5432 --user postgres --password secret --database finance_db

  4. 대화형 프롬프트 모드로 접속 정보 입력:
     python db/init_db.py --interactive
        """
    )
    parser.add_argument("--data-dir", type=str, default="perprocessed_datas/v4", help="전처리 엑셀 파일 디렉터리 경로 (기본값: perprocessed_datas/v4)")
    parser.add_argument("--meta-dir", type=str, default="db/meta_columns", help="컬럼 메타데이터 JSON 디렉터리 (기본값: db/meta_columns)")
    parser.add_argument("--schema-path", type=str, default="db/schema.sql", help="스키마 DDL 파일 경로 (기본값: db/schema.sql)")
    parser.add_argument("--db-type", type=str, default="sqlite", choices=["sqlite", "postgresql", "mysql", "mariadb"], help="DBMS 종류 (기본값: sqlite)")
    parser.add_argument("--db-path", type=str, default="db/finance_master.db", help="SQLite 데이터베이스 파일 경로 (기본값: db/finance_master.db)")
    parser.add_argument("--db-url", type=str, default=None, help="SQLAlchemy 데이터베이스 연결 URL (지정 시 다른 호스트 인자 무시)")
    parser.add_argument("--host", type=str, default=os.getenv("DB_HOST", "localhost"), help="DB 호스트 주소")
    parser.add_argument("--port", type=int, default=None, help="DB 포트 번호")
    parser.add_argument("--user", type=str, default=os.getenv("DB_USER", "postgres"), help="DB 사용자 계정")
    parser.add_argument("--password", type=str, default=os.getenv("DB_PASSWORD", ""), help="DB 비밀번호")
    parser.add_argument("--database", type=str, default=os.getenv("DB_NAME", "finance_master"), help="데이터베이스 이름")
    parser.add_argument("--batch-size", type=int, default=2000, help="Bulk INSERT 배치 크기 (기본값: 2000)")
    parser.add_argument("--drop-existing", action="store_true", default=True, help="기존 테이블 DROP 후 재생성")
    parser.add_argument("--no-meta", dest="with_meta", action="store_false", help="메타 벡터 DB 적재 건너뛰기")
    parser.set_defaults(with_meta=True)
    parser.add_argument("--interactive", action="store_true", help="런타임 대화형 프롬프트로 접속 정보 입력")
    return parser.parse_args()


def interactive_prompt(args):
    """대화형 프롬프트로 사용자 입력 수신"""
    print("\n" + "=" * 60)
    print("📋 DB 접속 정보 대화형 입력 모드")
    print("=" * 60)
    db_type_input = input(f"• DBMS 종류 [sqlite/postgresql/mysql] (현재: {args.db_type}): ").strip()
    if db_type_input:
        args.db_type = db_type_input.lower()

    if args.db_type == "sqlite":
        db_path_input = input(f"• SQLite DB 파일 경로 (현재: {args.db_path}): ").strip()
        if db_path_input:
            args.db_path = db_path_input
    else:
        host_input = input(f"• DB Host (현재: {args.host}): ").strip()
        if host_input:
            args.host = host_input
        port_default = 5432 if "postgres" in args.db_type else 3306
        port_input = input(f"• DB Port (기본: {port_default}): ").strip()
        args.port = int(port_input) if port_input else port_default
        user_input = input(f"• DB User (현재: {args.user}): ").strip()
        if user_input:
            args.user = user_input
        import getpass
        args.password = getpass.getpass("• DB Password: ")
        db_name_input = input(f"• DB Name (현재: {args.database}): ").strip()
        if db_name_input:
            args.database = db_name_input

    data_dir_input = input(f"• 엑셀 데이터 디렉터리 (현재: {args.data_dir}): ").strip()
    if data_dir_input:
        args.data_dir = data_dir_input
    print("=" * 60 + "\n")


def main():
    args = parse_arguments()

    if args.interactive:
        interactive_prompt(args)

    db_kwargs = {
        "db_path": args.db_path,
        "db_url": args.db_url,
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "database": args.database
    }

    try:
        init_database(
            data_dir=args.data_dir,
            schema_path=args.schema_path,
            db_type=args.db_type,
            drop_existing=args.drop_existing,
            batch_size=args.batch_size,
            **db_kwargs
        )
    except Exception as e:
        logger.exception(f"데이터베이스 적재 중 오류가 발생했습니다: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
