"""
================================================================================
에이전트 코어 (ModelManager, ToolManager, MCT 연동) 검증 테스트 스크립트
================================================================================
"""

import os
import sys
import json
import logging

# Windows 콘솔 utf-8 출력 설정
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

# 1. 환경 및 설정 로드 검증
print("\n" + "="*60)
print("1. 환경 설정 (config.py) 검증")
print("="*60)
from config import config, AGENT_DIR, TRIANGLE_ROOT, DATA_DIR, DEFAULT_DB_PATH
print(f"AGENT_DIR: {AGENT_DIR}")
print(f"TRIANGLE_ROOT: {TRIANGLE_ROOT}")
print(f"DATA_DIR: {DATA_DIR}")
print(f"DB_PATH: {config.db.db_path} (존재 여부: {os.path.exists(config.db.db_path)})")
print(f"Default Model: {config.model.default_model}")
print(f"Base URL: {config.model.base_url}")

# 2. ModelManager 검증
print("\n" + "="*60)
print("2. ModelManager 초기화 및 설정 변경 검증")
print("="*60)
from core.model_manager import ModelManager, get_model_manager, ToolCallInfo, LLMResponse

mm = get_model_manager()
print("초기 ModelManager 상태:")
print(json.dumps(mm.get_config(), indent=2, ensure_ascii=False))

# 모델 및 파라미터 동적 변경 테스트
mm.set_model("gpt-4o")
mm.set_parameters(temperature=0.2, max_tokens=2048)
print("\n모델 및 파라미터 변경 후 상태:")
print(json.dumps(mm.get_config(), indent=2, ensure_ascii=False))
assert mm.model_name == "gpt-4o"
assert mm.temperature == 0.2
assert mm.max_tokens == 2048
print("✅ ModelManager 설정 및 제어 기능 정상 작동")

# 3. ToolManager 기본 기능 검증
print("\n" + "="*60)
print("3. ToolManager 등록 및 OpenAI 스키마 생성 검증")
print("="*60)
from core.tool_manager import ToolManager, tool, get_tool_manager

tm = get_tool_manager(reset=True)

# 데코레이터 도구 등록 테스트
@tool(name="calculate_tax", description="금융 상품의 이자 및 배당 소득세를 계산합니다.")
def calculate_tax(amount: float, tax_rate: float = 0.154) -> float:
    return amount * tax_rate

print(f"등록된 도구 목록: {tm.list_tools()}")
assert "calculate_tax" in tm.list_tools()

# 도구 직접 실행 테스트
tax_result = tm.execute("calculate_tax", {"amount": 1000000})
print(f"calculate_tax(1,000,000) 실행 결과: {tax_result}원")
assert tax_result == 154000.0

# 4. MCT 메타데이터 검색 도구 연동 검증
print("\n" + "="*60)
print("4. MCT (search_finance_metadata) 도구 등록 및 벡터 검색 검증")
print("="*60)
from tools.meta_search_tool import register_meta_search_tool, search_finance_metadata

meta_tool = register_meta_search_tool(tm)
print(f"도구 등록 후 전체 목록: {tm.list_tools()}")
assert "search_finance_metadata" in tm.list_tools()

# OpenAI 도구 스키마 검증
openai_tools = tm.get_openai_tools()
print(f"\n생성된 OpenAI Tool 스키마 총 {len(openai_tools)}개:")
print(json.dumps(openai_tools, indent=2, ensure_ascii=False))

# 5. Tool Call 시뮬레이션 및 실제 검색 테스트
print("\n" + "="*60)
print("5. LLM Tool Call 시뮬레이션 실행 (채권 질의)")
print("="*60)

test_tool_call = ToolCallInfo(
    call_id="call_test_12345",
    tool_name="search_finance_metadata",
    arguments={
        "query": "만기 1년 미만 신용도 우량한 채권 매수금리",
        "table_name": "tb_domestic_bond",
        "top_k": 3,
        "alpha": 0.7
    },
    raw_arguments=json.dumps({
        "query": "만기 1년 미만 신용도 우량한 채권 매수금리",
        "table_name": "tb_domestic_bond",
        "top_k": 3,
        "alpha": 0.7
    })
)

tool_message = tm.execute_tool_call(test_tool_call)
print("반환된 OpenAI Tool Response Message:")
print(f"Role: {tool_message['role']}")
print(f"Tool Call ID: {tool_message['tool_call_id']}")
print(f"Content (일부 미리보기):\n{tool_message['content'][:600]}...\n")

parsed_res = json.loads(tool_message["content"])
assert parsed_res["status"] == "success"
assert len(parsed_res["columns"]) > 0
print(f"✅ 검색된 상위 컬럼 {len(parsed_res['columns'])}개:")
for i, col in enumerate(parsed_res["columns"], 1):
    print(f"  [{i}] {col['table_name']}.{col['column_name']} ({col['column_korean_name']}) - Score: {col['hybrid_score']}")

print("\n" + "="*60)
print("6. ETF 질의 검색 테스트")
print("="*60)
etf_res = tm.execute("search_finance_metadata", {
    "query": "총보수 수수료 저렴한 미국 배당 ETF",
    "table_name": "tb_foreign_etf",
    "top_k": 3
})
assert etf_res["status"] == "success"
print(f"✅ ETF 검색된 상위 컬럼 {len(etf_res['columns'])}개:")
for i, col in enumerate(etf_res["columns"], 1):
    print(f"  [{i}] {col['table_name']}.{col['column_name']} ({col['column_korean_name']}) - Score: {col['hybrid_score']}")

print("\n" + "="*60)
print("🎉 모든 검증 테스트가 성공적으로 통과되었습니다!")
print("="*60)
