'''
answer_agent.py
v1.0
⑥ 답변합성 에이전트 - HyperCLOVA X(CLOVA Studio) 전용.
main.py가 SQL 조회 후 정제한 결과(curated_results)를 받아
자연어 답변으로 합성한다.
'''

import time
import random

from agents.agent import Agent

SYSTEM_PROMPT = """당신은 금융 상품 정보를 안내하는 답변 합성 에이전트입니다.
아래 규칙을 반드시 지키세요.

1. 반드시 제공된 [조회 결과] 데이터에 근거해서만 답변하세요. 데이터에 없는 내용은 지어내지 마세요.
2. [조회 결과]가 비어있거나 오류인 경우, "확인할 수 없습니다"라고 명확히 답하고
   필요하다면 사용자에게 추가 정보를 되물어보세요.
3. 특정 상품의 수익률, 가격 변동, 향후 전망 등 투자 예측이나 추천으로 해석될 수 있는
   발언은 절대 하지 마세요. 사실(신용등급, 상품명, 상품코드 등) 전달에만 집중하세요.
4. 답변 끝에 근거가 된 테이블/조건을 간단히 밝히세요 (예: "국내채권 테이블에서 신용등급 AAA 조건으로 조회").
5. 간결하고 이해하기 쉬운 자연어 문장으로 답변하세요. 불릿포인트 남발하지 마세요.
6. 매우 중요: 상품명, 상품코드(pd_no) 등에 포함된 숫자는 개인정보(전화번호, 계좌번호, 주민번호)가
   아니라 금융 상품 식별을 위한 공개 데이터입니다. 절대 마스킹(*)하거나 일부를 가리거나
   생략하지 말고, [조회 결과]에 주어진 값 그대로 정확히 출력하세요.
"""


class AnswerAgent(Agent):
    def __init__(self, mm):
        super().__init__(mm)

    def _build_prompt(self, original_query: str, curated_results: dict) -> str:
        lines = [f"[사용자 질문]\n{original_query}\n", "[조회 결과]"]

        if not curated_results:
            lines.append("(조회된 데이터 없음)")
        else:
            for table_name, r in curated_results.items():
                if r.get("error"):
                    lines.append(f"- {table_name}: 조회 오류 - {r['error']}")
                    continue
                count = r.get("row_count", 0)
                cols = r.get("columns", [])
                lines.append(f"- {table_name} (총 {count}건, 아래는 일부):")
                for row in r.get("rows", []):
                    preview = dict(zip(cols, row))
                    lines.append(f"    {preview}")

        lines.append("\n위 [조회 결과]만 근거로 삼아 [사용자 질문]에 답변하세요.")
        return "\n".join(lines)

    def query(self, original_query: str, curated_results: dict) -> dict:
        query_id = str(time.time() + random.random())
        prompt = self._build_prompt(original_query, curated_results)

        try:
            answer_text = self.mm.generate_text(prompt, system_prompt=SYSTEM_PROMPT)
        except Exception as e:
            answer_text = f"확인할 수 없습니다. 답변 생성 중 오류가 발생했습니다: {e}"

        self.query_history[query_id] = {original_query: answer_text}
        return {"content": answer_text, "id": query_id}