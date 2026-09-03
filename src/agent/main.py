'''
team triangle main agent loop
v 1.0
'''

# model
from core.model_manager import get_model_manager

# agents
from agents.query_parser import QueryParser

# tools
from core.tool_manager import get_tool_manager
from tools.meta_search_tool import register_meta_search_tool


SHOW_THOUTH_PROCESS = True


def user_say(this):
    return {'user' : this}

def agent_say(this):
    return {'agent' : this}

def serious_error(msg):
    print("="*10, "ERROR!!", "*"*10)
    print(msg)
    print("="*27)
    exit(msg)

def get_agents(mm):
    agents = {}
    if "QueryParser" in globals():
        agents["QueryParser"] = QueryParser(mm)
    else :
        serious_error('get_agent : 필수 에이전트 QueryParser 로드에 실패')
    if SHOW_THOUTH_PROCESS:
        print("에이전트 목록 :\n{}".format("\n".join(agents.keys())))
    return agents

class Agent:
    def __init__(self):
        self.mm = get_model_manager()
        self.tm = get_tool_manager()
        register_meta_search_tool(self.tm)

        self.agents = get_agents(self.mm)
    
    def query(self, query):
        massage = []
        massage.append(user_say(query))

        # 우선 쿼리를 분석함
        result = self.agents["QueryParser"].query(query)
        # 원래는 여기서 다음 세션으로 가야하지만 개발을 더 못해서 그냥 결괴 확인...
        print(result)
        return result['content']

if __name__ == "__main__":
    agent = Agent()
    print(agent.query('신용등급 AAAA인 채권 찾아줘'))