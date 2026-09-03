"""
================================================================================
금융상품 메타 벡터 DB 대화형 검색 및 라우팅 테스트 도구 (test_meta_search.py)
--------------------------------------------------------------------------------
기능:
1. 사용자가 직접 입력한 자연어 질의에 대해 BGE-M3 Dense + Sparse 하이브리드 컬럼 검색 수행
2. 슬래시 명령어('/')를 통해 런타임에서 Top-K, Alpha(Dense/Sparse 비중), 테이블 필터 등 동적 변경
3. SQLite 및 PostgreSQL 백엔드 모두 지원
================================================================================
"""

import os
import sys
import json
import sqlite3
import argparse
from typing import Optional, Dict, Any

# Windows 콘솔 UTF-8 출력 보장
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from db.embed_meta import BGEM3Embedder, search_similar_columns


HELP_TEXT = """
================================================================================
🛠️ [슬래시 명령어 가이드] (런타임 메타 설정 변경)
================================================================================
  /k <N>      또는 /topk <N>       : 검색 결과 반환 개수 변경 (예: /k 3, /k 10)
  /a <0.0~1.0> 또는 /alpha <값>    : Dense 가중치 비중 변경 (예: /a 0.8, /a 1.0:순수Dense, /a 0.0:순수Sparse)
  /t <테이블> 또는 /table <테이블> : 특정 테이블로만 검색 한정 (예: /t tb_fund, /t all:전체)
  /config                          : 현재 런타임 설정 상태 확인
  /sample                          : 대표 예제 질의 4종 일괄 테스트 실행
  /help       또는 /?              : 본 도움말 출력
  /cls        또는 /clear          : 콘솔 화면 지우기
  /q          또는 /exit, /quit    : 프로그램 종료
================================================================================
"""

SAMPLE_QUERIES = [
    "미국 배당주에 투자하는 총보수 수수료 낮은 해외 ETF",
    "만기가 1년 미만으로 남고 신용등급이 우량한 국내 채권 매수수익률",
    "설정액이 1000억 이상이고 최근 1년 수익률이 좋은 주식형 공모펀드",
    "퇴직연금 IRP 계좌에서 투자 가능한 안전자산 국내 ETF"
]


def print_results(query: str, results: list, top_k: int, alpha: float, table_filter: Optional[str]):
    print("\n" + "=" * 80)
    filter_str = f" [필터: {table_filter}]" if table_filter and table_filter.lower() != 'all' else ""
    print(f"🎯 검색 질의: '{query}'{filter_str}")
    print(f"⚙️ 설정: Top-{top_k} | Alpha(Dense): {alpha:.2f} | Sparse: {(1-alpha):.2f}")
    print("=" * 80)

    if not results:
        print("  ⚠️ 일치하는 컬럼 메타데이터가 없습니다.")
        return

    for idx, r in enumerate(results, 1):
        tbl = r['table_name']
        col = r['column_name']
        kor = r['column_korean_name'] or '한글명 없음'
        dtype = r['data_type'] or '타입 미정'
        h_score = r['hybrid_score']
        d_score = r['dense_score']
        s_score = r['sparse_score']
        desc = r['description']
        synonyms = r.get('synonyms', [])
        samples = r.get('sample_values', [])
        queries = r.get('sample_queries', [])

        print(f"\n  [{idx}] {tbl}.{col} ({kor}) | 타입: {dtype}")
        print(f"      • 종합 유사도: {h_score:.4f}  (Dense: {d_score:.4f} × {alpha:.2f} + Sparse: {s_score:.4f} × {(1-alpha):.2f})")
        print(f"      • 상세 설명: {desc}")
        if synonyms:
            syn_str = ', '.join(synonyms[:6])
            print(f"      • 유의어/키워드: [{syn_str}]")
        if samples:
            smpl_str = ', '.join(map(str, samples[:4]))
            print(f"      • 예시 값: [{smpl_str}]")
        if queries:
            qs_str = ' | '.join(queries[:2])
            print(f"      • 예상 질문: \"{qs_str}\"")
        print("  " + "-" * 76)


def interactive_loop(conn, embedder: BGEM3Embedder, default_k: int = 5, default_alpha: float = 0.7, default_db: str = "SQLite"):
    top_k = default_k
    alpha = default_alpha
    table_filter = "all"

    print("\n" + "=" * 80)
    print("🚀 금융상품 메타 벡터 DB 대화형 검색 콘솔 (Interactive REPL)")
    print(f"   • 연결 DB : {default_db}")
    print(f"   • 임베딩  : BAAI/bge-m3 (Dense 1024-dim + Sparse Lexical Weights)")
    print("   • 도움말  : 명령어 목록은 '/help' 또는 '/?'를 입력하세요.")
    print("   • 종료    : '/q' 또는 'exit' 입력")
    print("=" * 80)

    while True:
        try:
            prompt_str = f"\n[α={alpha:.2f} | Top-{top_k} | {table_filter}] 질의 입력 > "
            user_input = input(prompt_str).strip()

            if not user_input:
                continue

            # 종료 명령어
            if user_input.lower() in ('/q', '/exit', '/quit', 'exit', 'quit', 'q'):
                print("\n👋 메타 벡터 검색 콘솔을 종료합니다. 수고하셨습니다!")
                break

            # 슬래시 명령어 처리
            if user_input.startswith('/'):
                parts = user_input.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd in ('/help', '/?'):
                    print(HELP_TEXT)
                    continue

                elif cmd in ('/k', '/topk'):
                    if not arg:
                        print(f"  ℹ️ 현재 Top-K: {top_k}. 변경 예시: /k 3, /topk 10")
                    else:
                        try:
                            val = int(arg)
                            if val > 0:
                                top_k = val
                                print(f"  ✅ Top-K가 {top_k}(으)로 변경되었습니다.")
                            else:
                                print("  ❌ Top-K는 1 이상의 정수여야 합니다.")
                        except ValueError:
                            print(f"  ❌ 올바른 숫자를 입력하세요: '{arg}'")
                    continue

                elif cmd in ('/a', '/alpha'):
                    if not arg:
                        print(f"  ℹ️ 현재 Alpha: {alpha:.2f}. 변경 예시: /a 0.8 (0.0: 순수 Sparse ~ 1.0: 순수 Dense)")
                    else:
                        try:
                            val = float(arg)
                            if 0.0 <= val <= 1.0:
                                alpha = val
                                print(f"  ✅ Alpha(Dense 비중)가 {alpha:.2f}(으)로 변경되었습니다. (Sparse 비중: {1-alpha:.2f})")
                            else:
                                print("  ❌ Alpha는 0.0 이상 1.0 이하의 실수여야 합니다.")
                        except ValueError:
                            print(f"  ❌ 올바른 실수를 입력하세요: '{arg}'")
                    continue

                elif cmd in ('/t', '/table'):
                    if not arg:
                        print(f"  ℹ️ 현재 테이블 필터: {table_filter}. (tb_domestic_bond, tb_domestic_etf, tb_foreign_etf, tb_fund, tb_fund_attribute, all)")
                    else:
                        valid_tables = ['tb_domestic_bond', 'tb_domestic_etf', 'tb_foreign_etf', 'tb_fund', 'tb_fund_attribute', 'all']
                        if arg.lower() in valid_tables:
                            table_filter = arg.lower()
                            print(f"  ✅ 검색 대상 테이블이 '{table_filter}'(으)로 설정되었습니다.")
                        else:
                            print(f"  ❌ 유효하지 않은 테이블명입니다. 가능한 목록: {', '.join(valid_tables)}")
                    continue

                elif cmd == '/config':
                    print("\n" + "-" * 50)
                    print("📋 현재 런타임 설정 상태:")
                    print(f"  • 대상 데이터베이스 : {default_db}")
                    print(f"  • 반환 개수 (Top-K)  : {top_k}")
                    print(f"  • Dense 비중 (Alpha) : {alpha:.2f}")
                    print(f"  • Sparse 비중        : {1-alpha:.2f}")
                    print(f"  • 테이블 필터        : {table_filter}")
                    print("-" * 50)
                    continue

                elif cmd == '/sample':
                    print("\n🧪 대표 예제 질의 4종 일괄 테스트를 시작합니다...")
                    for sq in SAMPLE_QUERIES:
                        results = search_similar_columns(
                            sq, conn, top_k=top_k, alpha=alpha,
                            embedder=embedder, table_filter=table_filter
                        )
                        print_results(sq, results, top_k, alpha, table_filter)
                    continue

                elif cmd in ('/cls', '/clear'):
                    os.system('cls' if os.name == 'nt' else 'clear')
                    continue

                else:
                    print(f"  ⚠️ 알 수 없는 명령어입니다: '{cmd}'. 도움말을 보려면 '/help'를 입력하세요.")
                    continue

            # 일반 자연어 질의 검색 실행
            results = search_similar_columns(
                user_input, conn, top_k=top_k, alpha=alpha,
                embedder=embedder, table_filter=table_filter
            )
            print_results(user_input, results, top_k, alpha, table_filter)

        except (KeyboardInterrupt, EOFError):
            print("\n👋 콘솔을 종료합니다.")
            break
        except Exception as e:
            print(f"\n❌ 검색 처리 중 오류가 발생했습니다: {e}")


def main():
    parser = argparse.ArgumentParser(description="금융상품 메타 벡터 DB 대화형 검색 도구")
    parser.add_argument('--query', '-q', type=str, default=None, help="1회성 질의 검색 (생략 시 대화형 콘솔 진입)")
    parser.add_argument('--top-k', '-k', type=int, default=5, help="검색 결과 상위 K개 (기본값: 5)")
    parser.add_argument('--alpha', '-a', type=float, default=0.7, help="Dense 가중치 비중 0.0~1.0 (기본값: 0.7)")
    parser.add_argument('--table', '-t', type=str, default='all', help="검색 대상 테이블 필터 (기본값: all)")
    parser.add_argument('--db-type', type=str, default='sqlite', choices=['sqlite', 'postgresql'], help="DB 종류 (기본값: sqlite)")
    parser.add_argument('--db-path', type=str, default='db/finance_master.db', help="SQLite DB 파일 경로")
    parser.add_argument('--host', type=str, default='localhost', help="PostgreSQL 호스트")
    parser.add_argument('--port', type=int, default=5432, help="PostgreSQL 포트")
    parser.add_argument('--user', type=str, default='postgres', help="PostgreSQL 유저")
    parser.add_argument('--password', type=str, default='', help="PostgreSQL 비밀번호")
    parser.add_argument('--database', type=str, default='finance_master', help="PostgreSQL 데이터베이스명")

    args = parser.parse_args()

    # DB 연결
    if args.db_type == 'sqlite':
        if not os.path.exists(args.db_path):
            print(f"❌ SQLite 데이터베이스 파일을 찾을 수 없습니다: {args.db_path}")
            print("   먼저 'python db/init_db.py'를 실행하여 DB를 생성하세요.")
            sys.exit(1)
        conn = sqlite3.connect(args.db_path)
        db_desc = f"SQLite ({args.db_path})"
    else:
        try:
            import psycopg2
            conn = psycopg2.connect(
                host=args.host, port=args.port, user=args.user,
                password=args.password, dbname=args.database
            )
            db_desc = f"PostgreSQL ({args.host}:{args.port}/{args.database})"
        except Exception as e:
            print(f"❌ PostgreSQL 연결 실패: {e}")
            sys.exit(1)

    print("🧠 BGE-M3 임베딩 모델 로드 중 (GPU 가속 확인 중)...")
    embedder = BGEM3Embedder()

    if args.query:
        # 1회성 CLI 검색 모드
        results = search_similar_columns(
            args.query, conn, top_k=args.top_k, alpha=args.alpha,
            embedder=embedder, table_filter=args.table
        )
        print_results(args.query, results, args.top_k, args.alpha, args.table)
    else:
        # 대화형 인터랙티브 REPL 모드
        interactive_loop(conn, embedder, default_k=args.top_k, default_alpha=args.alpha, default_db=db_desc)

    conn.close()


if __name__ == '__main__':
    main()