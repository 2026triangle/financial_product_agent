"""
================================================================================
MCT 메타데이터 검색 도구 연동 모듈 (tools/meta_search_tool.py)
--------------------------------------------------------------------------------
기능:
1. ../data/endpoint/meta_column_table.py 의 MCT(Meta Column Table) 검색기 연동
2. BGE-M3 하이브리드(Dense+Sparse) 벡터 검색 기반 최적 테이블/컬럼 탐색
3. ToolManager 등록용 Tool 래퍼 및 OpenAI 호환 도구 스키마 제공
================================================================================
"""

import os
import sys
import logging
from typing import Dict, Any, Optional, Union

# 상위 경로 모듈 로드 보장
from config import config, TRIANGLE_ROOT, DATA_DIR

try:
    from endpoint.meta_column_table import MCT
except ImportError:
    # 경로 보정 후 재시도
    endpoint_dir = os.path.join(str(DATA_DIR), "endpoint")
    if endpoint_dir not in sys.path:
        sys.path.insert(0, endpoint_dir)
    from meta_column_table import MCT

from core.tool_manager import Tool, ToolManager, get_tool_manager

logger = logging.getLogger("agent.tools.meta_search")

# 글로벌 단일 MCT 인스턴스 (Lazy Loading)
_mct_instance: Optional[MCT] = None


def get_mct_instance(
    db_path: Optional[str] = None,
    model_name: Optional[str] = None,
    preload: bool = True
) -> MCT:
    """
    MCT(Meta Column Table) 단일 인스턴스를 가져오거나 초기화합니다.
    
    :param db_path: SQLite DB 경로 (생략 시 config.db.db_path)
    :param model_name: BGE-M3 모델명 (생략 시 config.db.bge_m3_model)
    :param preload: 인메모리 캐시 사전 적재 여부
    """
    global _mct_instance
    if _mct_instance is None:
        target_db = db_path or config.db.db_path
        target_model = model_name or config.db.bge_m3_model
        logger.info(f"🚀 MCT 인스턴스 초기화 시작 [DB: {target_db}, Model: {target_model}]")
        _mct_instance = MCT(
            db_path=target_db,
            model_name=target_model,
            default_top_k=config.db.meta_top_k,
            default_alpha=config.db.meta_alpha,
            preload=preload
        )
    return _mct_instance


def search_finance_metadata(
    query: str,
    table_name: str = "all",
    top_k: int = 5,
    alpha: float = 0.7,
    **kwargs
) -> Dict[str, Any]:
    """
    자연어 질의를 분석하여 금융상품(국내채권, 국내ETF, 해외ETF, 공모펀드) 데이터베이스의
    가장 연관된 테이블 및 컬럼 메타데이터(한글명, 데이터타입, 설명, 유의어, 샘플값, 예상질문)를
    BGE-M3 Dense+Sparse 하이브리드 벡터 검색으로 탐색합니다.
    
    :param query: 사용자의 자연어 검색 질의 (예: '만기 1년 미만 신용도 우량한 채권 매수금리', '수수료 저렴한 미국 ETF', '1년 수익률 좋은 펀드')
    :param table_name: 검색 대상을 특정 테이블로 한정 (tb_domestic_bond, tb_domestic_etf, tb_foreign_etf, tb_fund, tb_fund_attribute, all)
    :param top_k: 반환받을 연관 컬럼의 최대 개수 (기본값: 5, 범위: 1~20)
    :param alpha: Dense 의미 유사도 vs Sparse 키워드 가중치 비중 (0.0: 순수 키워드, 1.0: 순수 의미, 기본값: 0.7)
    :return: 검색 결과 딕셔너리 (상태, 매칭된 컬럼 리스트 등)
    """
    mct = get_mct_instance()
    tbl_filter = None if table_name in ("all", "none", "", None) else table_name
    
    return mct(
        query=query,
        table_name=tbl_filter,
        top_k=top_k,
        alpha=alpha
    )


def register_meta_search_tool(
    tool_manager: Optional[ToolManager] = None
) -> Tool:
    """
    MCT 메타데이터 검색기를 ToolManager에 도구로 등록합니다.
    OpenAI 표준 Tool Calling 스키마가 완벽하게 주입됩니다.
    
    :param tool_manager: 등록할 ToolManager (생략 시 전역 기본 ToolManager)
    :return: 등록된 Tool 객체
    """
    tm = tool_manager or get_tool_manager()
    
    # MCT 클래스에 정의된 OpenAI 네이티브 Tool 스키마 활용
    openai_schema = MCT.get_openai_tool_schema()
    
    meta_tool = Tool(
        name="search_finance_metadata",
        func=search_finance_metadata,
        description=openai_schema["function"]["description"],
        schema=openai_schema
    )
    
    tm.register(meta_tool)
    logger.info("✅ 'search_finance_metadata' 도구가 ToolManager에 성공적으로 등록되었습니다.")
    return meta_tool
