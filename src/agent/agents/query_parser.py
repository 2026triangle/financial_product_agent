'''
query_parser.py
v1.0
A1
가장 처음 사용자의 질의를 받고, 질의의 카테고리를 나누고 적절한 쿼리 데이터 첨부 에이전트를 호출하는 에이전트임 
'''

# TODO : 온톨로지를 참고해 논리 검증할 수 있는 쿼리 짜달라
from agents.agent import Agent
PROMPT='''
task : 주어진 질문을 분석해주세요. 
질문은 반드시 "금융 제품(Financial products)" 에 관련한 질문이어야 합니다. 
출력은 반드시 json 형태로 표현하세요. 
분석에서는 질문을 "카테고리", "논리 검증", "수치 검증"으로 분리해야 합니다. 

define : 
* 카테고리 - 카테고리는 "domestic_bond(국내 채권)", "domestic_etf(국내 etf)", "foreign_etf(해외 etf)", "fund(공모 펀드)" 가 있습니다. 
사용자의 요청은 모두 이 카테고리 안에 금융 상품에 대한 질문이어야 하며 하나 이상의 카테고리와 연관된 복합 질문일 수도 있습니다. 
* 논리 검증 - 논리적으로 질문에 모순이 없는지 판단해야 합니다. 
질문에서 가정하고 있는 사실을 ttl 기반 문법의 트리플로 분리하세요. 
* 수치 검증 - 사용자가 수치적으로 확인하고자 하는 주제를 분리하세요. 

배경 지식 : 
기본적으로 질문자는 한국인이며 한국에 세금을 납부합니다. 
따라서 "국내"는 한국을 지칭합니다. 

example :
[query]
{
    "query" : "현재 판매 가능한 원화채권 중 AAAA 이상이면서 수익률 상위 10% 알려줘"
}
[answer]
{
    "category" : ["domestic_bond"],
    "logical_verification" : ["ex:ETF ex:abletoSale ex:True", "ex:ETF ex:hasCreditRating ex:"AAAA"],
    "numerical_verification" : 
    {
        "rate_of_return" : ">10%"
    }
}

query :
{
    "query" : {{query}}
}
answer :

'''


class QueryParser(Agent):
    def __init__(self, mm):
        super().__init__(mm)
        self.prompt = PROMPT

    def query(self, query):
        cp_query = self.prompt.replace('{{query}}', f'"{query}"')
        return super().query(cp_query)
        # TODO : 여기서 json 파싱해서 형식 안 맞으면 재시도 로직
        # TODO : 여기서 온톨지로 질문 모순 검증하는 에이전트 자제적으로 호출, 모순이면 바로 컷
        # TODO : 모순 없으면 category 기반해서 table filter해서 meta column table 조회 tool 호출
        # TODO : 호출 결과 종합해서 main에 넘기는 등 로직 고려. main은 그거 바탕으로 test to sql 에이전트 호출