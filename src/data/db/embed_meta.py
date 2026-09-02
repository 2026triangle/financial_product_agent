"""
================================================================================
금융상품 메타 벡터 DB 생성 및 임베딩 파이프라인 (embed_meta.py)
--------------------------------------------------------------------------------
기능:
1. db/meta_columns/*.json 파일에서 컬럼 메타데이터(설명, 한글명, 예시값, 예상질문) 로드
2. BAAI/bge-m3 모델을 활용하여 Dense Vector (1024차원) 및 Sparse Lexical Weights 생성
3. meta_schema_table에 원문 및 벡터 데이터 벌크 적재/Upsert
4. 자연어 질의 기반 하이브리드 컬럼 검색 및 라우팅 유틸리티 제공
================================================================================
"""

import os
import sys
import json
import logging
import argparse
from typing import List, Dict, Any, Tuple, Optional
import numpy as np

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def build_embedding_text(col_info: Dict[str, Any], table_name: str) -> str:
    """
    컬럼 메타데이터로부터 BGE-M3 모델 입력용 구조화된 텍스트 생성 (유의어 및 변형 질문 강화)
    """
    col_name = col_info.get('column_name', '')
    kor_name = col_info.get('column_korean_name', '')
    data_type = col_info.get('data_type', '')
    desc = col_info.get('description', '')
    synonyms = col_info.get('synonyms', [])
    samples = col_info.get('sample_values', [])
    queries = col_info.get('sample_queries', [])
    
    synonyms_str = ', '.join(synonyms) if synonyms else ''
    samples_str = ', '.join(map(str, samples[:4])) if samples else '없음'
    queries_str = ' | '.join(queries) if queries else '없음'
    
    lines = [
        f"[테이블] {table_name} | [컬럼명] {col_name} | [한글명] {kor_name} | [타입] {data_type}",
        f"[설명] {desc}"
    ]
    if synonyms_str:
        lines.append(f"[유의어/동의어/변형키워드] {synonyms_str}")
    lines.append(f"[예시값] {samples_str}")
    lines.append(f"[예상질문] {queries_str}")
    
    return '\n'.join(lines)


class BGEM3Embedder:
    """
    BAAI/bge-m3 모델 래퍼 클래스 (Transformers & PyTorch 기반)
    use_safetensors=True를 사용하여 보안 제약 없이 안정적으로 Dense/Sparse 벡터 동시 산출
    """
    def __init__(self, model_name: str = 'BAAI/bge-m3', device: Optional[str] = None):
        self.model_name = model_name
        self.device = device
        self.model = None
        self.tokenizer = None
        self._init_model()

    def _init_model(self):
        import torch
        from transformers import AutoTokenizer, AutoModel

        if self.device is None:
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'

        logger.info(f"BGE-M3 모델 로드 중 ({self.model_name}, device={self.device}, use_safetensors=True)...")
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self.model = AutoModel.from_pretrained(self.model_name, use_safetensors=True)
            self.model.to(self.device)
            self.model.eval()
            logger.info("BGE-M3 모델 및 토크나이저 로드 완료!")
        except Exception as e:
            logger.error(f"BGE-M3 로드 실패: {e}")
            raise e

    def encode(self, texts: List[str], max_length: int = 512) -> Tuple[List[List[float]], List[Dict[str, float]]]:
        """
        텍스트 리스트에 대해 BGE-M3 Dense Vector(1024차원) 및 Sparse Lexical Weights 생성
        """
        import torch
        import torch.nn.functional as F

        if not texts:
            return [], []

        inputs = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="pt"
        ).to(self.device)

        with torch.no_grad():
            outputs = self.model(**inputs, return_dict=True)
            # 1. Dense Vector: CLS 토큰 임베딩 (1024차원) + L2 정규화
            last_hidden = outputs.last_hidden_state # [batch_size, seq_len, 1024]
            cls_embeddings = last_hidden[:, 0]      # [batch_size, 1024]
            dense_vecs = F.normalize(cls_embeddings, p=2, dim=-1).cpu().numpy().tolist()

            # 2. Sparse Lexical Weights (토큰별 ReLu 활성화 기반 가중치 추출)
            # input_ids에 등장한 각 토큰의 최대 활성화 값을 계산하여 어휘 가중치 구성
            token_weights = torch.relu(last_hidden).max(dim=-1)[0] # [batch_size, seq_len]
            input_ids = inputs['input_ids'] # [batch_size, seq_len]

            sparse_list = []
            for b in range(len(texts)):
                sw = {}
                for t_idx, token_id in enumerate(input_ids[b].tolist()):
                    # 특수 토큰(CLS, SEP, PAD) 제외
                    if token_id in [self.tokenizer.cls_token_id, self.tokenizer.sep_token_id, self.tokenizer.pad_token_id]:
                        continue
                    w = float(token_weights[b, t_idx].item())
                    t_str = str(token_id)
                    if w > 0:
                        sw[t_str] = max(sw.get(t_str, 0.0), round(w, 4))
                sparse_list.append(sw)

        return dense_vecs, sparse_list


def load_all_meta_json(meta_dir: str) -> List[Dict[str, Any]]:
    """
    db/meta_columns/ 디렉터리 내 모든 JSON 파일 로드
    """
    records = []
    if not os.path.exists(meta_dir):
        logger.error(f"메타데이터 디렉터리를 찾을 수 없습니다: {meta_dir}")
        return records

    for filename in sorted(os.listdir(meta_dir)):
        if not filename.endswith('.json'):
            continue
        table_name = os.path.splitext(filename)[0]
        filepath = os.path.join(meta_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                col_list = json.load(f)
                for item in col_list:
                    item['table_name'] = table_name
                    records.append(item)
        except Exception as e:
            logger.error(f"JSON 파일 읽기 실패 ({filename}): {e}")

    logger.info(f"총 {len(records)}개의 컬럼 메타데이터를 로드했습니다.")
    return records


def insert_meta_to_db(
    conn,
    meta_dir: str = 'db/meta_columns',
    model_name: str = 'BAAI/bge-m3',
    batch_size: int = 32
) -> int:
    """
    컬럼 메타데이터를 로드하여 BGE-M3 임베딩을 수행하고 meta_schema_table에 적재
    """
    records = load_all_meta_json(meta_dir)
    if not records:
        logger.warning("적재할 메타데이터가 없습니다.")
        return 0

    embedder = BGEM3Embedder(model_name=model_name)
    cursor = conn.cursor()

    # 테이블 DDL 실행 (존재하지 않을 경우)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS meta_schema_table (
            table_name              VARCHAR(50)     NOT NULL,
            column_name             VARCHAR(50)     NOT NULL,
            column_korean_name      VARCHAR(100),
            data_type               VARCHAR(50),
            description             TEXT            NOT NULL,
            synonyms                TEXT,
            sample_values           TEXT,
            sample_queries          TEXT,
            embedding_text          TEXT,
            dense_vector            TEXT,
            sparse_vector           TEXT,
            created_at              TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT pk_meta_schema_table PRIMARY KEY (table_name, column_name)
        );
    """)
    conn.commit()

    is_sqlite = (
        type(conn).__name__ == 'Connection' and 'sqlite' in type(conn).__module__.lower()
    ) or (
        hasattr(conn, 'isolation_level') and not hasattr(conn, 'info')
    )

    logger.info(f"BGE-M3 임베딩 생성 및 DB 적재 시작 (총 {len(records)}건, 배치 크기: {batch_size}, DB: {'SQLite' if is_sqlite else 'PostgreSQL'})...")

    inserted_count = 0
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        texts = [build_embedding_text(r, r['table_name']) for r in batch]

        dense_vecs, sparse_vecs = embedder.encode(texts)

        for record, emb_text, d_vec, s_vec in zip(batch, texts, dense_vecs, sparse_vecs):
            tbl_nm = record['table_name']
            col_nm = record.get('column_name', '')
            kor_nm = record.get('column_korean_name', '')
            dtype = record.get('data_type', '')
            desc = record.get('description', '')
            synonyms_json = json.dumps(record.get('synonyms', []), ensure_ascii=False)
            sample_vals = json.dumps(record.get('sample_values', []), ensure_ascii=False)
            sample_qs = json.dumps(record.get('sample_queries', []), ensure_ascii=False)
            dense_str = json.dumps(d_vec)
            sparse_str = json.dumps(s_vec, ensure_ascii=False)

            if is_sqlite:
                sql_sqlite = """
                    INSERT OR REPLACE INTO meta_schema_table (
                        table_name, column_name, column_korean_name, data_type,
                        description, synonyms, sample_values, sample_queries, embedding_text,
                        dense_vector, sparse_vector
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """
                cursor.execute(sql_sqlite, (
                    tbl_nm, col_nm, kor_nm, dtype, desc,
                    synonyms_json, sample_vals, sample_qs, emb_text, dense_str, sparse_str
                ))
            else:
                # PostgreSQL 문법
                sql_pg = """
                    INSERT INTO meta_schema_table (
                        table_name, column_name, column_korean_name, data_type,
                        description, synonyms, sample_values, sample_queries, embedding_text,
                        dense_vector, sparse_vector
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (table_name, column_name) DO UPDATE SET
                        column_korean_name = EXCLUDED.column_korean_name,
                        data_type = EXCLUDED.data_type,
                        description = EXCLUDED.description,
                        synonyms = EXCLUDED.synonyms,
                        sample_values = EXCLUDED.sample_values,
                        sample_queries = EXCLUDED.sample_queries,
                        embedding_text = EXCLUDED.embedding_text,
                        dense_vector = EXCLUDED.dense_vector,
                        sparse_vector = EXCLUDED.sparse_vector;
                """
                cursor.execute(sql_pg, (
                    tbl_nm, col_nm, kor_nm, dtype, desc,
                    synonyms_json, sample_vals, sample_qs, emb_text, dense_str, sparse_str
                ))

            inserted_count += 1

        conn.commit()
        logger.info(f"  적재 진행률: {inserted_count}/{len(records)} 건 완료")

    logger.info(f"meta_schema_table 메타 벡터 적재 완료! (총 {inserted_count}건)")
    return inserted_count


def search_similar_columns(
    query: str,
    conn,
    top_k: int = 5,
    alpha: float = 0.7,
    model_name: str = 'BAAI/bge-m3',
    embedder: Optional[BGEM3Embedder] = None,
    table_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    자연어 질의에 대해 Dense + Sparse 하이브리드 유사도 검색을 수행하여 연관 컬럼 반환
    alpha: Dense 비중 (0.0: 순수 Sparse, 1.0: 순수 Dense)
    table_filter: 특정 테이블명으로만 검색을 제한할 때 사용 (예: 'tb_fund')
    """
    if embedder is None:
        embedder = BGEM3Embedder(model_name=model_name)
    query_dense, query_sparse = embedder.encode([query])
    q_dense = np.array(query_dense[0])
    q_sparse = query_sparse[0]

    # Query sparse L2 norm
    q_sparse_norm = np.sqrt(sum(v**2 for v in q_sparse.values())) if q_sparse else 0.0

    cursor = conn.cursor()
    if table_filter and table_filter.lower() != 'all':
        cursor.execute("""
            SELECT table_name, column_name, column_korean_name, data_type, description,
                   synonyms, sample_values, sample_queries, dense_vector, sparse_vector
            FROM meta_schema_table
            WHERE table_name = ?;
        """, (table_filter,))
    else:
        cursor.execute("""
            SELECT table_name, column_name, column_korean_name, data_type, description,
                   synonyms, sample_values, sample_queries, dense_vector, sparse_vector
            FROM meta_schema_table;
        """)
    rows = cursor.fetchall()

    results = []
    for r in rows:
        tbl_nm, col_nm, kor_nm, dtype, desc, syn_str, smpl_vals, smpl_qs, d_str, s_str = r
        d_vec = np.array(json.loads(d_str)) if d_str else np.zeros(1024)
        s_dict = json.loads(s_str) if s_str else {}

        # 1. Dense Cosine Similarity (0.0 ~ 1.0)
        norm_q = np.linalg.norm(q_dense)
        norm_d = np.linalg.norm(d_vec)
        dense_score = float(np.dot(q_dense, d_vec) / (norm_q * norm_d)) if norm_q > 0 and norm_d > 0 else 0.0

        # 2. Sparse Cosine Similarity (0.0 ~ 1.0)
        sparse_score = 0.0
        if q_sparse and s_dict and q_sparse_norm > 0:
            d_sparse_norm = np.sqrt(sum(v**2 for v in s_dict.values()))
            if d_sparse_norm > 0:
                common_tokens = set(q_sparse.keys()) & set(s_dict.keys())
                dot_prod = sum(float(q_sparse[t]) * float(s_dict[t]) for t in common_tokens)
                sparse_score = float(dot_prod / (q_sparse_norm * d_sparse_norm))

        # 3. Hybrid Score 결합 (0.0 ~ 1.0)
        hybrid_score = alpha * dense_score + (1 - alpha) * sparse_score

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

        results.append({
            'table_name': tbl_nm,
            'column_name': col_nm,
            'column_korean_name': kor_nm,
            'data_type': dtype,
            'description': desc,
            'synonyms': synonyms_list,
            'sample_values': samples_list,
            'sample_queries': queries_list,
            'dense_score': round(dense_score, 4),
            'sparse_score': round(sparse_score, 4),
            'hybrid_score': round(hybrid_score, 4)
        })

    # 하이브리드 점수 기준 내림차순 정렬
    results.sort(key=lambda x: x['hybrid_score'], reverse=True)
    return results[:top_k]


def main():
    parser = argparse.ArgumentParser(description="금융상품 메타 벡터 DB 생성 및 임베딩 파이프라인")
    parser.add_argument('--meta-dir', type=str, default='db/meta_columns', help="컬럼 JSON 디렉터리 경로")
    parser.add_argument('--db-type', type=str, default='sqlite', choices=['sqlite', 'postgres', 'mysql'], help="DB 종류")
    parser.add_argument('--db-path', type=str, default='db/finance_master.db', help="SQLite DB 파일 경로")
    parser.add_argument('--model-name', type=str, default='BAAI/bge-m3', help="HuggingFace 임베딩 모델명")
    parser.add_argument('--test-query', type=str, default=None, help="자연어 질의 테스트 검색")
    parser.add_argument('--top-k', type=int, default=5, help="검색 결과 상위 K개")

    args = parser.parse_args()

    import sqlite3
    if args.db_type == 'sqlite':
        conn = sqlite3.connect(args.db_path)
    else:
        logger.error("현재 CLI 직접 실행은 sqlite를 기본 지원합니다. PostgreSQL은 init_db.py를 통해 실행하세요.")
        return

    if args.test_query:
        logger.info(f"자연어 질의 테스트 검색: '{args.test_query}' (Top {args.top_k})")
        top_results = search_similar_columns(args.test_query, conn, top_k=args.top_k, model_name=args.model_name)
        print("\n" + "="*80)
        print(f"자연어 질의: '{args.test_query}'")
        print("="*80)
        for idx, item in enumerate(top_results, 1):
            print(f"[{idx}] {item['table_name']}.{item['column_name']} ({item['column_korean_name']})")
            print(f"    - 종합 유사도: {item['hybrid_score']} (Dense: {item['dense_score']}, Sparse: {item['sparse_score']})")
            print(f"    - 타입: {item['data_type']}")
            print(f"    - 설명: {item['description']}")
            print("-" * 80)
    else:
        insert_meta_to_db(conn, meta_dir=args.meta_dir, model_name=args.model_name)

    conn.close()


if __name__ == '__main__':
    main()
