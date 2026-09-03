# 🚀 금융 질의응답 AI 에이전트 (Agent Core)

사용자의 금융 질의(채권, ETF, 펀드 등)에 대해 정밀한 데이터를 기반으로 응답하는 AI 에이전트의 핵심 코어 모듈입니다.

---

## 📁 디렉터리 구조

```
triangle/agent/
├── config.py                 # 환경설정 (.env 로드, 기본 모델, 경로 관리)
├── .env.example              # 환경설정 예시 파일
├── core/
│   ├── __init__.py
│   ├── model_manager.py      # OpenAI 호환 단일 모델 관리자 (LLM Client)
│   └── tool_manager.py       # 도구 등록 및 Tool Calling 자동 실행 엔진
├── tools/
│   ├── __init__.py
│   └── meta_search_tool.py   # MCT(Meta Column Table) BGE-M3 하이브리드 벡터 검색 Tool
└── test_core.py              # 연동 검증 및 시뮬레이션 테스트 스크립트
```

---

## 🌟 주요 모듈 및 기능

### 1. 단일 모델 관리자 (`core/model_manager.py`)
- **OpenAI 호환성**: OpenAI 공식 API 및 vLLM, Ollama, OpenRouter, Azure OpenAI 등 모든 OpenAI 호환 엔드포인트 지원 (`base_url`, `api_key` 지원).
- **싱글톤 & 동적 제어**: 애플리케이션 전역에서 `get_model_manager()`로 인스턴스를 공유하며, `set_model()`, `set_parameters()`를 통해 런타임에 모델과 파라미터를 동적으로 변경 가능.
- **동기/비동기 API**: `chat_completion()`, `achat_completion()`, `generate_text()`, `agenerate_text()` 지원.
- **정형화된 응답 객체**: `LLMResponse`를 통해 `content`, `tool_calls`, `finish_reason`, `usage` 등을 간편하게 제어.

### 2. 도구 관리자 (`core/tool_manager.py`)
- **유연한 도구 등록**: `@tool` 데코레이터, 함수 직접 등록, 사전 정의된 OpenAI JSON Schema 기반 등록 지원.
- **자동 스키마 추출**: Python 타입 힌트와 docstring을 분석하여 OpenAI Function Calling 스키마 자동 생성.
- **도구 실행 및 피드백**: LLM이 호출한 `tool_call`을 안전하게 실행하고 OpenAI 호환 `role: "tool"` 메시지로 변환.

### 3. 메타데이터 검색 도구 연동 (`tools/meta_search_tool.py`)
- `../data/endpoint/meta_column_table.py`의 `MCT` 인스턴스를 ToolManager에 `search_finance_metadata` 도구로 연결.
- 203개 금융 마스터 DB 컬럼에 대해 **BGE-M3 Dense(의미) + Sparse(키워드) 하이브리드 벡터 검색** 제공.

---

## 💻 사용 예시

### 1) 기본 모델 관리자 및 텍스트 생성
```python
from core.model_manager import get_model_manager

mm = get_model_manager()
mm.set_model("gpt-4o-mini")

# 단순 텍스트 질의
response = mm.generate_text("채권 듀레이션의 개념을 1줄로 설명해줘.")
print(response)
```

### 2) 도구 등록 및 Tool Calling 에이전트 루프
```python
from core.model_manager import get_model_manager
from core.tool_manager import get_tool_manager
from tools.meta_search_tool import register_meta_search_tool

# 매니저 초기화 및 도구 등록
mm = get_model_manager()
tm = get_tool_manager()
register_meta_search_tool(tm)

# 사용자 질의 및 메시지 구성
messages = [
    {"role": "system", "content": "당신은 금융 데이터 전문 AI 에이전트입니다. 사용자 질문에 답변하기 위해 필요한 메타데이터를 검색하세요."},
    {"role": "user", "content": "만기 1년 미만 안전한 채권 금리 알고 싶어."}
]

# LLM 1차 추론 (도구 스키마 전달)
response = mm.chat_completion(
    messages=messages,
    tools=tm.get_openai_tools()
)

# Tool Call 처리
if response.has_tool_calls:
    # 1. 어시스턴트 호출 메시지 히스토리에 추가
    messages.append(response.to_assistant_message())
    
    # 2. 도구 실행 및 tool 결과 메시지 생성
    tool_messages = tm.execute_tool_calls(response.tool_calls)
    messages.extend(tool_messages)
    
    # 3. 도구 실행 결과를 바탕으로 최종 답변 생성
    final_response = mm.chat_completion(messages=messages)
    print(final_response.content)
```

---

## 🧪 검증 테스트 실행

```bash
cd triangle/agent
python test_core.py
```
