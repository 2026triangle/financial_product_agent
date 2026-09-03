'''
agent.py
v1.0
모든 에이전트의 기본 메소드를 정의하는 기본적인 상위 클레스임. 
'''
import time
import random

class Agent:
    def __init__(self, mm):
        self.query_history = {}
        self.mm = mm

    def query(self, query : str):
        query_id = str(time.time()+random.random())
        result = self.mm.generate_text(query)
        self.query_history[query_id] = {query:result}
        return {"content":result, "id":query_id}