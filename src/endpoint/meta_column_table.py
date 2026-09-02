"""
================================================================================
MCT (Meta Column Table) - 금융상품 메타 벡터 검색 및 Tool 엔드포인트
--------------------------------------------------------------------------------
기능:
1. SQLite DB (meta_schema_table)에 영속화된 203개 컬럼 메타데이터를 인메모리(RAM)에
   사전 로드(Preload/Warm-up)하여 디스크 I/O 없이 1ms 미만의 초고속 하이브리드 검색 제공
2. BGE-M3 (Dense 1024-dim + Sparse Lexical Weights) 기반 시맨틱+키워드 하이브리드 매칭
3. AI 에이전트 Tool Calling 인터페이스 (Python Callable, dict/json 입출력)
4. 런타임 기본값 (default_top_k, default_alpha, default_table) 동적 변경 기능
5. 표준 MCP (Model Context Protocol) 도구 스키마 및 실행 핸들러 제공
================================================================================
"""

import os
import sys
import json
import sqlite3
import logging
from typing import List, Dict, Any, Optional, Union
import numpy as np

# 프로젝트 루트 경로 자동 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from db.embed_meta import BGEM3Embedder

logger = logging.getLogger("endpoint.mct")


class MCT:
    """
    Meta Column Table (MCT) 검색기 객체
    
    자연어 질의를 입력받아 금융 마스터 테이블의 최적 컬럼 및 스키마 정보를 반환하는
    AI 에이전트 전용 하이브리드 벡터 검색 도구입니다.
    """

    def __init__(
        self,
        db_path: str = "db/finance_master.db",
        model_name: str = "BAAI/bge-m3",
        default_top_k: int = 5,
        default_alpha: float = 0.7,
        default_table: Optional[str] = None,
        embedder: Optional[BGEM3Embedder] = None,
        preload: bool = True
    ):
        """
        MCT 인스턴스 초기화
        
        :param db_path: SQLite DB 파일 경로 (기본: db/finance_master.db)
        :param model_name: HuggingFace BGE-M3 임베딩 모델명
        :param default_top_k: 기본 반환 결과 개수 (기본: 5)
        :param default_alpha: Dense 유사도 가중치 비중 (0.0: 순수 Sparse ~ 1.0: 순수 Dense, 기본: 0.7)
        :param default_table: 기본 대상 테이블 필터 (기본: None / 전체)
        :param embedder: 사전에 로드된 BGEM3Embedder 객체 (선택)
        :param preload: 초기화 시 DB 데이터를 메모리에 사전 로드할지 여부 (기본: True)
        """
        # 절대 경로 처리
        if not os.path.isabs(db_path):
            self.db_path = os.path.join(PROJECT_ROOT, db_path)
        else:
            self.db_path = db_path

        self.model_name = model_name
        self.default_top_k = default_top_k
        self.default_alpha = default_alpha
        self.default_table = default_table

        # BGE-M3 임베더 초기화 (주입되지 않았을 경우 생성)
        if embedder is not None:
            self.embedder = embedder
        else:
            self.embedder = BGEM3Embedder(model_name=self.model_name)

        # 인메모리 메타데이터 캐시
        self._memory_cache: List[Dict[str, Any]] = []
        if preload:
            self.reload_cache()

    def reload_cache(self) -> int:
        """
        SQLite DB의 meta_schema_table 데이터를 읽어 인메모리 캐시로 적재합니다.
        DB가 업데이트되었을 때 호출하여 메모리 데이터를 동기화할 수 있습니다.
        """
        if not os.path.exists(self.db_path):
            logger.warning(f"DB 파일이 존재하지 않습니다: {self.db_path}")
            self._memory_cache = []
            return 0

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT table_name, column_name, column_korean_name, data_type,
                       description, synonyms, sample_values, sample_queries,
                       dense_vector, sparse_vector
                FROM meta_schema_table;
            """)
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            logger.error(f"meta_schema_table 조회 실패: {e}")
            conn.close()
            return 0

        cache = []
        for r in rows:
            tbl_nm, col_nm, kor_nm, dtype, desc, syn_str, smpl_vals, smpl_qs, d_str, s_str = r
            
            d_vec = np.array(json.loads(d_str), dtype=np.float32) if d_str else np.zeros(1024, dtype=np.float32)
            s_dict = json.loads(s_str) if s_str else {}
            
            # Sparse L2 norm 사전 계산 (검색 가속화)
            s_norm = float(np.sqrt(sum(v**2 for v in s_dict.values()))) if s_dict else 0.0

            try:
                synonyms_list = json.loads(syn_str) if syn_str else []
            except Exception:
                synonyms_list = []

            try:
                samples_list = json.loads(smpl_vals) if smpl_vals else []
            except Exception:
                samples_list = []

            try:
                queries_list = json.loads(smpl_qs) if smpl_qs else []
            except Exception:
                queries_list = []

            cache.append({
                'table_name': tbl_nm,
                'column_name': col_nm,
                'column_korean_name': kor_nm or '',
                'data_type': dtype or '',
                'description': desc or '',
                'synonyms': synonyms_list,
                'sample_values': samples_list,
                'sample_queries': queries_list,
                'dense_vec': d_vec,
                'sparse_dict': s_dict,
                'sparse_norm': s_norm
            })

        conn.close()
        self._memory_cache = cache
        logger.info(f"✅ MCT 인메모리 캐시 적재 완료 (총 {len(cache)}개 컬럼)")
        return len(cache)

    # --------------------------------------------------------------------------
    # 설정 관리 메서드 (기본값 변경)
    # --------------------------------------------------------------------------
    def set_defaults(
        self,
        top_k: Optional[int] = None,
        alpha: Optional[float] = None,
        table_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        런타임에 MCT 검색 기본값을 변경합니다.
        """
        if top_k is not None:
            if top_k <= 0:
                raise ValueError("top_k는 1 이상의 정수여야 합니다.")
            self.default_top_k = top_k

        if alpha is not None:
            if not (0.0 <= alpha <= 1.0):
                raise ValueError("alpha는 0.0과 1.0 사이의 실수여야 합니다.")
            self.default_alpha = alpha

        if table_name is not None:
            self.default_table = None if table_name.lower() in ("all", "none", "") else table_name.lower()

        return self.get_config()

    def get_config(self) -> Dict[str, Any]:
        """현재 런타임 설정 상태를 반환합니다."""
        return {
            "db_path": self.db_path,
            "model_name": self.model_name,
            "default_top_k": self.default_top_k,
            "default_alpha": self.default_alpha,
            "default_table": self.default_table or "all",
            "cached_columns_count": len(self._memory_cache)
        }

    # --------------------------------------------------------------------------
    # 핵심 검색 메서드 (인메모리 초고속 하이브리드 연산)
    # --------------------------------------------------------------------------
    def search(
        self,
        query: str,
        table_name: Optional[str] = None,
        top_k: Optional[int] = None,
        alpha: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        자연어 질의에 대해 BGE-M3 Dense + Sparse 하이브리드 유사도 검색을 수행합니다.
        
        :param query: 사용자 자연어 질의 (예: "만기 1년 미만 안전한 채권 금리")
        :param table_name: 특정 테이블명 한정 필터 (예: 'tb_domestic_bond', None/all 시 전체 검색)
        :param top_k: 반환할 상위 결과 개수 (기본값: default_top_k)
        :param alpha: Dense 비중 0.0~1.0 (기본값: default_alpha)
        :return: 정제된 컬럼 메타데이터 및 유사도 점수 리스트
        """
        if not query or not query.strip():
            return []

        # 기본값 적용
        k = top_k if top_k is not None else self.default_top_k
        a = alpha if alpha is not None else self.default_alpha
        tbl_filter = table_name if table_name is not None else self.default_table
        if tbl_filter and tbl_filter.lower() in ("all", "none", ""):
            tbl_filter = None

        if not self._memory_cache:
            self.reload_cache()
            if not self._memory_cache:
                logger.warning("검색 대상 메타데이터 캐시가 비어 있습니다.")
                return []

        # 1. 질의 텍스트 BGE-M3 인코딩 (Dense + Sparse)
        q_dense_list, q_sparse_list = self.embedder.encode([query])
        q_dense = np.array(q_dense_list[0], dtype=np.float32)
        q_sparse = q_sparse_list[0]
        q_norm_dense = np.linalg.norm(q_dense)
        q_sparse_norm = float(np.sqrt(sum(v**2 for v in q_sparse.values()))) if q_sparse else 0.0

        results = []
        for item in self._memory_cache:
            # 테이블 필터 검사
            if tbl_filter and item['table_name'].lower() != tbl_filter.lower():
                continue

            # (1) Dense Cosine Similarity (0.0 ~ 1.0)
            d_vec = item['dense_vec']
            d_norm = np.linalg.norm(d_vec)
            if q_norm_dense > 0 and d_norm > 0:
                dense_score = float(np.dot(q_dense, d_vec) / (q_norm_dense * d_norm))
            else:
                dense_score = 0.0

            # (2) Sparse Cosine Similarity (0.0 ~ 1.0)
            s_dict = item['sparse_dict']
            s_norm = item['sparse_norm']
            sparse_score = 0.0
            if q_sparse and s_dict and q_sparse_norm > 0 and s_norm > 0:
                common_tokens = set(q_sparse.keys()) & set(s_dict.keys())
                if common_tokens:
                    dot_prod = sum(float(q_sparse[t]) * float(s_dict[t]) for t in common_tokens)
                    sparse_score = float(dot_prod / (q_sparse_norm * s_norm))

            # (3) Hybrid Score 결합
            hybrid_score = a * dense_score + (1.0 - a) * sparse_score

            results.append({
                "table_name": item['table_name'],
                "column_name": item['column_name'],
                "column_korean_name": item['column_korean_name'],
                "data_type": item['data_type'],
                "description": item['description'],
                "synonyms": item['synonyms'],
                "sample_values": item['sample_values'],
                "sample_queries": item['sample_queries'],
                "dense_score": round(dense_score, 4),
                "sparse_score": round(sparse_score, 4),
                "hybrid_score": round(hybrid_score, 4)
            })

        # 하이브리드 점수 내림차순 정렬 후 Top-K 추출
        results.sort(key=lambda x: x['hybrid_score'], reverse=True)
        return results[:k]

    # --------------------------------------------------------------------------
    # AI 에이전트 Tool Calling 인터페이스 (Callable & JSON Input/Output)
    # --------------------------------------------------------------------------
    def __call__(self, **kwargs) -> Dict[str, Any]:
        """
        AI 에이전트의 Tool Calling 호출을 처리하는 매직 메서드
        
        지원하는 입력 형태:
        mct(query="수익률 좋은 펀드", table_name="tb_fund", top_k=3, alpha=0.8)
        mct(params={"query": "..."})
        """
        params = kwargs.get("params", kwargs)
        
        query = params.get("query", "")
        table_name = params.get("table_name", None)
        top_k = params.get("top_k", None)
        alpha = params.get("alpha", None)

        if top_k is not None:
            try:
                top_k = int(top_k)
            except Exception:
                top_k = self.default_top_k

        if alpha is not None:
            try:
                alpha = float(alpha)
            except Exception:
                alpha = self.default_alpha

        try:
            results = self.search(query=query, table_name=table_name, top_k=top_k, alpha=alpha)
            return {
                "status": "success",
                "query": query,
                "table_filter": table_name or "all",
                "top_k": top_k or self.default_top_k,
                "alpha": alpha or self.default_alpha,
                "total_matched": len(results),
                "columns": results
            }
        except Exception as e:
            logger.error(f"MCT Tool Call 실패: {e}")
            return {
                "status": "error",
                "message": str(e),
                "query": query,
                "columns": []
            }

    def execute_tool(self, tool_input: Union[str, Dict[str, Any]]) -> str:
        """
        에이전트가 전달한 JSON 문자열 또는 dict 입력을 받아 정제된 JSON 응답 문자열로 반환
        """
        if isinstance(tool_input, str):
            try:
                parsed_input = json.loads(tool_input)
            except Exception:
                parsed_input = {"query": tool_input}
        else:
            parsed_input = tool_input

        output_dict = self.__call__(**parsed_input)
        return json.dumps(output_dict, ensure_ascii=False, indent=2)

    # --------------------------------------------------------------------------
    # API 네이티브 도구 명세 (Tool Schemas for OpenAI / Anthropic / Gemini / MCP)
    # --------------------------------------------------------------------------
    @classmethod
    def get_openai_tool_schema(cls) -> Dict[str, Any]:
        """OpenAI 및 LangChain / LiteLLM 네이티브 Function Calling 스키마 반환"""
        return {
            "type": "function",
            "function": {
                "name": "search_finance_metadata",
                "description": (
                    "자연어 질의(Natural Language Query)를 분석하여 금융상품(국내채권, 국내ETF, 해외ETF, 공모펀드) 데이터베이스의 "
                    "가장 연관된 테이블 및 컬럼 메타데이터(한글명, 데이터타입, 설명, 유의어, 샘플값, 예상질문)를 "
                    "BGE-M3 Dense+Sparse 하이브리드 벡터 검색으로 탐색합니다. "
                    "Text-to-SQL 쿼리를 작성하거나 사용자 질의에 맞는 금융 지표를 파악할 때 반드시 호출하세요."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "사용자의 자연어 검색 질의 (예: '만기 1년 미만 신용도 우량한 채권 매수금리', '수수료 저렴한 미국 ETF', '1년 수익률 좋은 펀드')"
                        },
                        "table_name": {
                            "type": "string",
                            "description": "검색 대상을 특정 테이블로 한정할 때 지정 (생략 시 전체 테이블 대상 검색)",
                            "enum": ["tb_domestic_bond", "tb_domestic_etf", "tb_foreign_etf", "tb_fund", "tb_fund_attribute", "all"],
                            "default": "all"
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "반환받을 연관 컬럼의 최대 개수 (기본값: 5, 범위: 1~20)",
                            "default": 5
                        },
                        "alpha": {
                            "type": "number",
                            "description": "Dense 의미 유사도 vs Sparse 키워드 가중치 비중 (0.0: 순수 키워드, 1.0: 순수 의미, 기본값: 0.7)",
                            "default": 0.7
                        }
                    },
                    "required": ["query"],
                    "additionalProperties": False
                }
            }
        }

    @classmethod
    def get_anthropic_tool_schema(cls) -> Dict[str, Any]:
        """Anthropic Claude Tools API 네이티브 스키마 반환"""
        return {
            "name": "search_finance_metadata",
            "description": (
                "자연어 질의(Natural Language Query)를 분석하여 금융상품(국내채권, 국내ETF, 해외ETF, 공모펀드) 데이터베이스의 "
                "가장 연관된 테이블 및 컬럼 메타데이터(한글명, 데이터타입, 설명, 유의어, 샘플값, 예상질문)를 "
                "BGE-M3 Dense+Sparse 하이브리드 벡터 검색으로 탐색합니다."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "사용자의 자연어 검색 질의"
                    },
                    "table_name": {
                        "type": "string",
                        "description": "검색 대상을 특정 테이블로 한정 (선택값: tb_domestic_bond, tb_domestic_etf, tb_foreign_etf, tb_fund, tb_fund_attribute, all)",
                        "enum": ["tb_domestic_bond", "tb_domestic_etf", "tb_foreign_etf", "tb_fund", "tb_fund_attribute", "all"],
                        "default": "all"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "반환받을 연관 컬럼 최대 개수 (기본값: 5)",
                        "default": 5
                    },
                    "alpha": {
                        "type": "number",
                        "description": "Dense 가중치 비중 (0.0~1.0, 기본값: 0.7)",
                        "default": 0.7
                    }
                },
                "required": ["query"]
            }
        }

    @classmethod
    def get_gemini_tool_schema(cls) -> Dict[str, Any]:
        """Google Gemini Function Declaration 네이티브 스키마 반환"""
        return {
            "name": "search_finance_metadata",
            "description": (
                "자연어 질의(Natural Language Query)를 분석하여 금융상품(국내채권, 국내ETF, 해외ETF, 공모펀드) 데이터베이스의 "
                "가장 연관된 테이블 및 컬럼 메타데이터(한글명, 데이터타입, 설명, 유의어, 샘플값, 예상질문)를 탐색합니다."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "query": {"type": "STRING", "description": "사용자의 자연어 검색 질의"},
                    "table_name": {
                        "type": "STRING",
                        "description": "검색 대상 테이블명",
                        "enum": ["tb_domestic_bond", "tb_domestic_etf", "tb_foreign_etf", "tb_fund", "tb_fund_attribute", "all"]
                    },
                    "top_k": {"type": "INTEGER", "description": "반환받을 최대 컬럼 수 (기본값: 5)"},
                    "alpha": {"type": "NUMBER", "description": "Dense 가중치 (기본값: 0.7)"}
                },
                "required": ["query"]
            }
        }

    @classmethod
    def get_mcp_tool_definition(cls) -> Dict[str, Any]:
        """Claude / Cursor / Antigravity 등 LLM 에이전트 연동용 표준 MCP 도구 스키마를 반환합니다."""
        return {
            "name": "search_finance_metadata",
            "description": (
                "자연어 질의(Natural Language Query)를 분석하여 금융상품 데이터베이스의 "
                "가장 연관된 테이블 및 컬럼 메타데이터(한글명, 데이터타입, 설명, 유의어, 샘플값, 예상질문)를 "
                "BGE-M3 Dense+Sparse 하이브리드 벡터 검색으로 탐색합니다. "
                "Text-to-SQL 쿼리를 작성하거나 사용자 질의에 맞는 금융 지표를 파악할 때 반드시 호출하세요."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "사용자의 자연어 검색 질의 (예: '만기 1년 미만 신용도 우량한 채권 매수금리', '수수료 저렴한 미국 ETF', '1년 수익률 좋은 펀드')"
                    },
                    "table_name": {
                        "type": "string",
                        "description": "검색 대상을 특정 테이블로 한정할 때 지정 (선택값: 'tb_domestic_bond', 'tb_domestic_etf', 'tb_foreign_etf', 'tb_fund', 'tb_fund_attribute', 'all'). 생략 시 전체 테이블 대상 검색.",
                        "enum": ["tb_domestic_bond", "tb_domestic_etf", "tb_foreign_etf", "tb_fund", "tb_fund_attribute", "all"]
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "반환받을 연관 컬럼의 최대 개수 (기본값: 5, 범위: 1~20)",
                        "default": 5
                    },
                    "alpha": {
                        "type": "number",
                        "description": "Dense 의미 유사도 vs Sparse 키워드 가중치 비중 (0.0: 순수 키워드, 1.0: 순수 의미, 기본값: 0.7)",
                        "default": 0.7
                    }
                },
                "required": ["query"]
            }
        }
