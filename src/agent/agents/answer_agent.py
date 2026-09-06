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

from agents.agent import Agent

SYSTEM_PROMPT = """당신은 금융 상품 정보를 안내하는 답변 합성 에이전트입니다.
아래 규칙을 반드시 지키세요.

1. 아래 [조회 결과 요약]을 참고해서, 사용자 질문에 대한 짧은 도입 문장 하나만
   작성하세요. 절대로 번호 매기기, 목록, 항목 나열을 하지 마세요. 실제 상품
   목록과 조회 조건은 당신의 답변 뒤에 별도로 자동 첨부되므로, 당신은 개별
   상품이나 구체적인 조회 조건(테이블명, 필터값 등)을 언급하지 마세요.
2. [조회 결과 요약]이 비어있거나 오류인 경우, "확인할 수 없습니다"라는 취지로
   답하고 필요하다면 사용자에게 추가 정보를 되물어보세요.
3. 수익률, 가격 변동, 향후 전망 등 투자 예측이나 추천으로 해석될 수 있는
   발언은 절대 하지 마세요.
4. 상품명, 종목명, 회사명, 티커 등 고유명사에 대해 [조회 결과 요약]에 없는
   영문 정식명, 부연 설명, 배경 정보를 절대로 추가하지 마세요. 사용자가 언급한
   이름은 사용자가 쓴 표현 그대로만 반복하세요.
5. 데이터 기준 시점, 발표일, "현재 기준" 등 [조회 결과 요약]에 명시되지 않은
   날짜·시점 정보를 임의로 언급하지 마세요.
6. 도입 문장은 1~2문장 이내로 간결하게 작성하세요.
"""

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
        """실제 데이터는 LLM을 거치지 않고 원본 그대로 텍스트로 렌더링"""
        blocks = []
        for table_name, r in curated_results.items():
            if r.get("error") or not r.get("rows"):
                continue
            cols = r.get("columns", [])
            lines = [f"[{table_name}]"]
            for row in r["rows"]:
                preview = dict(zip(cols, row))
                lines.append(f"  · {preview}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def query(self, original_query: str, curated_results: dict) -> dict:
        query_id = str(time.time() + random.random())

        summary = self._build_summary(curated_results)
        prompt = (
            f"[사용자 질문]\n{original_query}\n\n"
            f"[조회 결과 요약]\n{summary}\n\n"
            "위 내용을 바탕으로 도입 문장과 마무리 문장만 작성하세요. "
            "실제 상품 데이터는 언급하지 마세요."
        )

        try:
            wrapper_text = self.mm.generate_text(prompt, system_prompt=SYSTEM_PROMPT)
        except Exception as e:
            wrapper_text = f"확인할 수 없습니다. 답변 생성 중 오류가 발생했습니다: {e}"

        data_block = self._build_data_block(curated_results)
        answer_text = f"{wrapper_text}\n\n{data_block}" if data_block else wrapper_text

        self.query_history[query_id] = {original_query: answer_text}
        return {"content": answer_text, "id": query_id}