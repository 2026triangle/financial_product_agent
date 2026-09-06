'''
answer_agent.py
v1.1
⑥ 답변합성 에이전트 - HyperCLOVA X(CLOVA Studio) 전용.
데이터(상품명/코드 등)는 LLM이 재생성하지 않고 원본 그대로 삽입,
LLM은 안내 문구/도입-마무리 문장만 생성
(CLOVA 세이프티 필터의 숫자 마스킹 회피 + 할루시네이션 방지 목적).
'''

import time
import random
import re

from agents.agent import Agent
from agents.column_utils import guess_identity_columns


SYSTEM_PROMPT = """당신은 금융 상품 정보를 안내하는 답변 합성 에이전트입니다.
아래 규칙을 반드시 지키세요.

1. 아래 [조회 결과 요약]을 참고해서, 사용자 질문에 대한 짧은 도입 문장 하나만
    작성하세요. 절대로 번호 매기기, 목록, 표(마크다운 테이블, "|" 문자 사용)를
    만들지 마세요. 실제 상품 목록과 조회 조건은 당신의 답변 뒤에 별도로 자동
    첨부되므로, 당신은 개별 상품이나 구체적인 조회 조건(테이블명, 필터값 등)을
    언급하지 마세요. 특히 "상품A", "회사A"처럼 예시 형태로라도 가상의 상품/
    회사 이름을 지어내는 것은 절대 금지입니다.
2. [조회 결과 요약]이 비어있거나 오류인 경우, "확인할 수 없습니다"라는 취지로
   답하고 필요하다면 사용자에게 추가 정보를 되물어보세요.
3. 수익률, 가격 변동, 향후 전망 등 투자 예측이나 추천으로 해석될 수 있는
   발언은 절대 하지 마세요.
4. 상품명, 종목명, 회사명, 티커 등 고유명사에 대해 [조회 결과 요약]에 없는
   영문 정식명, 부연 설명, 배경 정보를 절대로 추가하지 마세요. 사용자가 언급한
   이름은 사용자가 쓴 표현 그대로만 반복하세요.
5. 데이터 기준 시점, 발표일, "현재 기준" 등 [조회 결과 요약]에 명시되지 않은
   날짜·시점 정보를 임의로 언급하지 마세요.
6. 사용자가 질문에서 언급한 특정 조건(상품명, 종목명, 테마, 기업관계 등)이
   실제로 검색 결과에서 확인되었다거나 매칭되었다고 단정하지 마세요. 그런
   조건의 충족 여부를 판단할 수 없다면 "~을 조회했습니다" 정도로만 중립적으로
   서술하고, 그 조건이 만족되었다고 주장하지 마세요.
7. 도입 문장은 1~2문장 이내로 간결하게 작성하세요.
8. 아래 [미검증 조건]이 주어진 경우, 그 조건을 만족하는 상품이라고 단정하지
   마세요. 대신 검증 가능한 나머지 조건을 기준으로 조회했다는 취지로
   서술하세요(예: "데이터상 확인 가능한 [조건] 기준 목록입니다").
"""

_MARKDOWN_TABLE_LINE_RE = re.compile(r'^\s*\|.*\|\s*$')


def _strip_markdown_tables(text: str) -> str:
    """AnswerAgent가 규칙(개별 상품 언급 금지)을 어기고 표를 만들어 가짜 상품을
    지어내는 경우에 대비한 코드 레벨 안전장치. 프롬프트 지침만으로는 막히지 않는
    사례가 실측 확인됨(하-1: '채권A'/'회사A' 지어냄) — 마크다운 표 형태의 줄은
    도입 문장에서 통째로 제거한다. 실제 데이터는 _build_data_block이 별도로
    붙이므로 표를 지워도 정보 손실이 없다."""
    lines = text.split("\n")
    cleaned = [line for line in lines if not _MARKDOWN_TABLE_LINE_RE.match(line)]
    return "\n".join(cleaned).strip()

class AnswerAgent(Agent):
    def __init__(self, mm):
        super().__init__(mm)

    def _build_summary(self, curated_results: dict) -> str:
        """LLM에게 넘길 요약 정보 (개수/테이블/조건만, 실제 값은 안 넘김)"""
        if not curated_results:
            return "(조회된 데이터 없음)"
        lines = []
        for table_name, r in curated_results.items():
            if r.get("error"):
                lines.append(f"- {table_name}: 조회 오류 - {r['error']}")
            else:
                lines.append(
                    f"- {table_name}: 총 {r.get('row_count', 0)}건 중 "
                    f"{r.get('curated_row_count', len(r.get('rows', [])))}건 표시 예정"
                )
        return "\n".join(lines)

    def _build_data_block(self, curated_results: dict) -> str:
        """실제 데이터는 LLM을 거치지 않고 원본 그대로 텍스트로 렌더링.
        0은 대회 측 공지에 따라 의도된 값이므로 그대로 노출하되,
        None/빈 문자열처럼 값이 없음을 뜻하는 경우만 생략한다.
        상품코드/상품명 컬럼은 guess_identity_columns로 동적 추정해
        "[코드] 이름 (나머지 컬럼: 값, ...)" 형태로 포맷팅한다
        (테이블마다 컬럼명이 달라 하드코딩하지 않는다)."""
        blocks = []
        for table_name, r in curated_results.items():
            if r.get("error") or not r.get("rows"):
                continue
            cols = r.get("columns", [])
            id_cols = guess_identity_columns(cols)
            code_col = id_cols[0] if len(id_cols) >= 1 else None
            name_col = id_cols[1] if len(id_cols) >= 2 else None
            lines = [f"[{table_name}]"]
            for row in r["rows"]:
                preview = {
                    k: v for k, v in zip(cols, row)
                    if v is not None and v != ""
                }
                code_val = preview.pop(code_col, None) if code_col else None
                name_val = preview.pop(name_col, None) if name_col else None
                head = f"[{code_val}] " if code_val is not None else ""
                head += str(name_val) if name_val is not None else ""
                rest = ", ".join(f"{k}: {v}" for k, v in preview.items())
                if head and rest:
                    lines.append(f"  · {head} ({rest})")
                elif head:
                    lines.append(f"  · {head}")
                else:
                    lines.append(f"  · {preview}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def query(self, original_query: str, curated_results: dict, skipped: list = None) -> dict:
        query_id = str(time.time() + random.random())

        summary = self._build_summary(curated_results)
        caveat_note = ""
        if skipped:
            caveat_note = (
                "\n\n[미검증 조건]\n" + ", ".join(skipped) +
                "\n위 조건은 보유한 데이터로 검증하지 못했습니다."
            )
        prompt = (
            f"[사용자 질문]\n{original_query}\n\n"
            f"[조회 결과 요약]\n{summary}"
            f"{caveat_note}\n\n"
            "위 내용을 바탕으로 도입 문장과 마무리 문장만 작성하세요. "
            "실제 상품 데이터는 언급하지 마세요."
        )

        try:
            wrapper_text = self.mm.generate_text(prompt, system_prompt=SYSTEM_PROMPT)
        except Exception as e:
            wrapper_text = f"확인할 수 없습니다. 답변 생성 중 오류가 발생했습니다: {e}"

        wrapper_text = _strip_markdown_tables(wrapper_text) 
        data_block = self._build_data_block(curated_results)
        answer_text = f"{wrapper_text}\n\n{data_block}" if data_block else wrapper_text

        self.query_history[query_id] = {original_query: answer_text}
        return {"content": answer_text, "id": query_id}