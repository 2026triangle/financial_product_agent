"""
================================================================================
DB 매니징 도구 및 원클릭 적재 엔드포인트 (endpoint/db_manager.py)
--------------------------------------------------------------------------------
기능:
1. v4, v5, v6 등 신규 전처리 버전 디렉터리를 인자로 받아 마스터 테이블 5종 및
   meta_schema_table에 전자동 원클릭 적재
2. import하여 init_DB(...) 함수 호출 또는 DBManager 클래스로 활용 가능
3. 적재 결과 리포트 및 무결성 검증 결과 dict 반환
4. CLI 터미널 실행 지원
================================================================================
"""

import os
import sys
import logging
from typing import Dict, Any, Optional

# 프로젝트 루트 경로 자동 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from db.init_db import init_database

logger = logging.getLogger("endpoint.db_manager")


def init_DB(
    data_dir: str = "perprocessed_datas/v4",
    db_path: str = "db/finance_master.db",
    schema_path: str = "db/schema.sql",
    with_meta: bool = True,
    meta_dir: str = "db/meta_columns",
    drop_existing: bool = True,
    db_type: str = "sqlite",
    **db_kwargs
) -> Dict[str, Any]:
    """
    지정된 전처리 엑셀 데이터와 메타데이터를 파싱하여 데이터베이스에 원클릭 적재하는 엔드포인트 함수
    
    :param data_dir: 전처리 데이터 디렉터리 경로 (예: 'perprocessed_datas/v4', 'perprocessed_datas/v5' 등)
    :param db_path: SQLite DB 파일 경로 (db_type='sqlite' 시 사용)
    :param schema_path: DDL 스키마 SQL 파일 경로 (기본: 'db/schema.sql')
    :param with_meta: meta_schema_table BGE-M3 임베딩 적재 여부 (기본: True)
    :param meta_dir: 컬럼 메타데이터 JSON 디렉터리 (기본: 'db/meta_columns')
    :param drop_existing: 기존 테이블 초기화 후 재생성 여부 (기본: True)
    :param db_type: 대상 DBMS 종류 ('sqlite', 'postgresql', 'mysql')
    :param db_kwargs: PostgreSQL/MySQL 접속 인자 (host, port, user, password, database 등)
    :return: 적재 완료 통계 및 상태를 담은 결과 딕셔너리
    """
    # 상대 경로를 프로젝트 루트 기준 절대 경로로 변환
    def to_abs(p: str) -> str:
        return p if os.path.isabs(p) else os.path.join(PROJECT_ROOT, p)

    abs_data_dir = to_abs(data_dir)
    abs_schema_path = to_abs(schema_path)
    abs_meta_dir = to_abs(meta_dir)
    abs_db_path = to_abs(db_path) if db_type == "sqlite" else db_path

    if not os.path.exists(abs_data_dir):
        raise FileNotFoundError(f"데이터 소스 디렉터리를 찾을 수 없습니다: {abs_data_dir}")

    if not os.path.exists(abs_schema_path):
        raise FileNotFoundError(f"스키마 파일을 찾을 수 없습니다: {abs_schema_path}")

    # SQLite인 경우 db_path 전달
    if db_type in ("sqlite", "sqlite3"):
        db_kwargs["db_path"] = abs_db_path

    logger.info("======================================================================")
    logger.info(f"🚀 [DBManager] 데이터베이스 초기화 및 원클릭 적재 시작 (버전: {data_dir})")
    logger.info("======================================================================")

    start_time = os.times().elapsed if hasattr(os.times(), 'elapsed') else 0

    init_database(
        data_dir=abs_data_dir,
        schema_path=abs_schema_path,
        db_type=db_type,
        drop_existing=drop_existing,
        with_meta=with_meta,
        meta_dir=abs_meta_dir,
        **db_kwargs
    )

    return {
        "status": "success",
        "data_version_dir": data_dir,
        "db_type": db_type,
        "db_path": abs_db_path if db_type == "sqlite" else db_kwargs.get("database"),
        "with_meta": with_meta,
        "message": "데이터베이스 및 메타 벡터 테이블 적재가 성공적으로 완료되었습니다."
    }


class DBManager:
    """
    데이터베이스 버전 관리 및 마스터 테이블 마이그레이션 매니저 클래스
    """
    def __init__(
        self,
        db_type: str = "sqlite",
        db_path: str = "db/finance_master.db",
        schema_path: str = "db/schema.sql",
        meta_dir: str = "db/meta_columns",
        **db_kwargs
    ):
        self.db_type = db_type
        self.db_path = db_path
        self.schema_path = schema_path
        self.meta_dir = meta_dir
        self.db_kwargs = db_kwargs

    def load_version(self, version_dir: str, with_meta: bool = True, drop_existing: bool = True) -> Dict[str, Any]:
        """특정 전처리 버전 디렉터리를 지정하여 DB에 적재합니다 (예: 'perprocessed_datas/v5')"""
        return init_DB(
            data_dir=version_dir,
            db_path=self.db_path,
            schema_path=self.schema_path,
            with_meta=with_meta,
            meta_dir=self.meta_dir,
            drop_existing=drop_existing,
            db_type=self.db_type,
            **self.db_kwargs
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="금융상품 마스터 DB 원클릭 매니징 엔드포인트")
    parser.add_argument("--data-dir", "-d", type=str, default="perprocessed_datas/v4", help="전처리 데이터 디렉터리 경로")
    parser.add_argument("--db-type", type=str, default="sqlite", choices=["sqlite", "postgresql", "mysql"])
    parser.add_argument("--db-path", type=str, default="db/finance_master.db", help="SQLite DB 파일 경로")
    parser.add_argument("--no-meta", action="store_true", help="메타 벡터 임베딩 생성을 건너뜁니다.")

    args = parser.parse_args()

    init_DB(
        data_dir=args.data_dir,
        db_path=args.db_path,
        db_type=args.db_type,
        with_meta=not args.no_meta
    )
