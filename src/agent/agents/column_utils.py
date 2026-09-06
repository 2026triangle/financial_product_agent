"""컬럼명 패턴으로 상품코드/상품명 컬럼을 추정하는 공용 유틸.
main.py의 _guess_identity_columns와 동일한 휴리스틱을 answer_agent.py에서도
쓰기 위해 분리했다 (순환 import 방지, 테이블마다 컬럼명 하드코딩 불필요)."""


def guess_identity_columns(columns):
    id_cols = []
    for c in columns:
        cl = c.lower()
        if cl.endswith("no") or cl == "itm_no":
            id_cols.append(c)
            break
    for c in columns:
        cl = c.lower()
        if cl.endswith("nm") and c not in id_cols:
            id_cols.append(c)
            break
    return id_cols
