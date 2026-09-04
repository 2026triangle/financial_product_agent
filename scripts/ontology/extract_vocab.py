"""
extract_vocab.py — v4 전처리 데이터에서 통제 어휘(Controlled Vocabulary) 후보 추출
사용: python3 ontology/extract_vocab.py <xlsx경로> [출력json경로]
카디널리티 기반 자동 발굴: 고유값 <= MAX_CARD 인 범주형 컬럼을 어휘 후보로 판정
"""
import sys, os, json
import pandas as pd

MAX_CARD = 50
NUM_CARD = 15  # 연속형 수치 배제 기준

def scan(path):
    df = pd.read_excel(path)
    name = os.path.basename(path)
    res = {"file": name, "rows": len(df), "cols": len(df.columns), "vocab": {}}
    for col in df.columns:
        s = df[col].dropna()
        if s.empty:
            continue
        nuniq = s.nunique()
        if nuniq > MAX_CARD or nuniq <= 1:
            continue
        if pd.api.types.is_numeric_dtype(s) and nuniq > NUM_CARD:
            continue
        vc = s.value_counts()
        res["vocab"][str(col)] = {
            "n_unique": int(nuniq),
            "missing_rate": round(1 - len(s) / len(df), 4),
            "values": {str(k): int(v) for k, v in vc.items()},
        }
    return res

def report(res):
    print(f"\n{'='*72}\n{res['file']}  ({res['rows']:,}행 x {res['cols']}열)  어휘후보 {len(res['vocab'])}개")
    for col, info in res["vocab"].items():
        print(f"\n  [{col}]  고유 {info['n_unique']}  결측 {info['missing_rate']:.1%}")
        for v, c in list(info["values"].items())[:MAX_CARD]:
            print(f"      {v[:44]:<44} {c:>8,}")

if __name__ == "__main__":
    src = sys.argv[1]
    res = scan(src)
    report(res)
    if len(sys.argv) > 2:
        json.dump(res, open(sys.argv[2], "w"), ensure_ascii=False, indent=2)
        print(f"\n-> saved {sys.argv[2]}")
