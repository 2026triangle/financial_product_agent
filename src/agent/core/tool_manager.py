"""
================================================================================
도구 관리자 및 실행 엔진 (core/tool_manager.py)
--------------------------------------------------------------------------------
기능:
1. Tool 등록 (데코레이터 @tool, 함수, Callable 클래스, 기정의 스키마)
2. OpenAI Tool Calling 형식 (tools=[{"type": "function", ...}]) 자동 변환
3. LLM tool_call 요청 파싱 및 안전한 실행/결과 직렬화
4. 실행 중 예외 처리 및 에이전트 피드백 메시지 생성
================================================================================
"""

import inspect
import json
import logging
import asyncio
from typing import (
    Callable, Dict, Any, List, Optional, Union, Tuple, get_type_hints
)
from dataclasses import dataclass, field

from .model_manager import ToolCallInfo

logger = logging.getLogger("agent.tool_manager")


# 파이썬 타입 -> JSON Schema 타입 매핑
PYTHON_TO_JSON_SCHEMA_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
    List: "array",
    Dict: "object",
    Any: "string",
}


def python_type_to_schema_type(py_type: Any) -> str:
    """파이썬 타입 어노테이션을 JSON Schema 타입 문자열로 변환"""
    if py_type in PYTHON_TO_JSON_SCHEMA_TYPES:
        return PYTHON_TO_JSON_SCHEMA_TYPES[py_type]
    
    # Optional[T] / Union[T, None] 처리
    origin = getattr(py_type, "__origin__", None)
    if origin is Union:
        args = py_type.__args__
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return python_type_to_schema_type(non_none[0])
    elif origin in (list, List):
        return "array"
    elif origin in (dict, Dict):
        return "object"

    return "string"


def generate_function_schema(
    func: Callable,
    name: Optional[str] = None,
    description: Optional[str] = None
) -> Dict[str, Any]:
    """
    Python 함수의 시그니처, 타입 힌트, docstring을 분석하여 OpenAI Function 스키마를 자동 생성합니다.
    """
    func_name = name or func.__name__
    doc = description or inspect.getdoc(func) or f"Function {func_name}"
    
    sig = inspect.signature(func)
    try:
        type_hints = get_type_hints(func)
    except Exception:
        type_hints = {}

    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        param_type = type_hints.get(param_name, str)
        schema_type = python_type_to_schema_type(param_type)

        param_info: Dict[str, Any] = {"type": schema_type}
        
        # 기본값 체크
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
        else:
            param_info["default"] = param.default

        properties[param_name] = param_info

    parameters_schema = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters_schema["required"] = required

    return {
        "type": "function",
        "function": {
            "name": func_name,
            "description": doc.strip(),
            "parameters": parameters_schema
        }
    }


class Tool:
    """단일 도구 래퍼 클래스"""

    def __init__(
        self,
        name: str,
        func: Callable,
        description: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        is_async: Optional[bool] = None
    ):
        self.name = name
        self.func = func
        self.description = description or inspect.getdoc(func) or f"Tool {name}"
        self.is_async = is_async if is_async is not None else asyncio.iscoroutinefunction(func)

        # 스키마 구성
        if schema:
            if "type" in schema and schema.get("type") == "function":
                self.openai_schema = schema
            elif "name" in schema:
                # function 본체만 넘겨진 경우 OpenAI 래핑
                self.openai_schema = {"type": "function", "function": schema}
            else:
                self.openai_schema = schema
        else:
            self.openai_schema = generate_function_schema(
                func=func,
                name=name,
                description=self.description
            )

    def execute(self, **kwargs) -> Any:
        """동기 도구 실행"""
        try:
            return self.func(**kwargs)
        except Exception as e:
            logger.error(f"❌ 도구 '{self.name}' 실행 오류: {e}", exc_info=True)
            return {
                "status": "error",
                "tool": self.name,
                "error_type": type(e).__name__,
                "message": str(e)
            }

    async def aexecute(self, **kwargs) -> Any:
        """비동기 도구 실행"""
        try:
            if self.is_async:
                return await self.func(**kwargs)
            else:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, lambda: self.func(**kwargs))
        except Exception as e:
            logger.error(f"❌ 비동기 도구 '{self.name}' 실행 오류: {e}", exc_info=True)
            return {
                "status": "error",
                "tool": self.name,
                "error_type": type(e).__name__,
                "message": str(e)
            }

    def __call__(self, *args, **kwargs) -> Any:
        return self.execute(*args, **kwargs)


class ToolManager:
    """
    도구(Tool) 레지스트리 및 실행 관리자
    """

    _instance: Optional["ToolManager"] = None

    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        logger.info("✅ ToolManager 초기화 완료")

    # --------------------------------------------------------------------------
    # 도구 등록 인터페이스
    # --------------------------------------------------------------------------
    def register(
        self,
        tool_or_func: Union[Tool, Callable],
        name: Optional[str] = None,
        description: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None
    ) -> Tool:
        """
        도구를 레지스트리에 등록합니다.
        
        :param tool_or_func: Tool 인스턴스 또는 실행할 Callable 함수
        :param name: 도구명 (생략 시 함수명)
        :param description: 도구 설명 (생략 시 docstring)
        :param schema: 사전 정의된 OpenAI 도구 스키마 dict (선택)
        :return: 등록된 Tool 인스턴스
        """
        if isinstance(tool_or_func, Tool):
            tool_obj = tool_or_func
        else:
            t_name = name or getattr(tool_or_func, "__name__", "custom_tool")
            tool_obj = Tool(
                name=t_name,
                func=tool_or_func,
                description=description,
                schema=schema
            )

        self._tools[tool_obj.name] = tool_obj
        logger.info(f"🔧 도구 등록 완료: '{tool_obj.name}'")
        return tool_obj

    def unregister(self, tool_name: str) -> bool:
        """도구 등록 해제"""
        if tool_name in self._tools:
            del self._tools[tool_name]
            logger.info(f"🗑️ 도구 등록 해제: '{tool_name}'")
            return True
        return False

    def get_tool(self, name: str) -> Optional[Tool]:
        """도구명으로 Tool 객체 조회"""
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        """등록된 모든 도구명 리스트 반환"""
        return list(self._tools.keys())

    # --------------------------------------------------------------------------
    # OpenAI 스키마 출력
    # --------------------------------------------------------------------------
    def get_openai_tools(
        self,
        include_tools: Optional[List[str]] = None,
        exclude_tools: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        OpenAI Chat Completion API의 `tools` 인자에 전달할 스키마 리스트를 반환합니다.
        """
        schemas = []
        for name, tool_obj in self._tools.items():
            if include_tools and name not in include_tools:
                continue
            if exclude_tools and name in exclude_tools:
                continue
            schemas.append(tool_obj.openai_schema)
        return schemas

    # --------------------------------------------------------------------------
    # 도구 호출 실행 엔진 (Tool Calling Execution)
    # --------------------------------------------------------------------------
    def execute(
        self,
        tool_name: str,
        arguments: Union[str, Dict[str, Any]]
    ) -> Any:
        """
        도구명과 인자를 받아 동기 실행하고 결과를 반환합니다.
        """
        tool_obj = self.get_tool(tool_name)
        if not tool_obj:
            err_msg = f"등록되지 않은 도구입니다: '{tool_name}' (사용 가능: {self.list_tools()})"
            logger.warning(err_msg)
            return {"status": "error", "message": err_msg}

        if isinstance(arguments, str):
            try:
                parsed_args = json.loads(arguments) if arguments.strip() else {}
            except Exception as e:
                logger.error(f"도구 인자 JSON 파싱 실패 ({arguments}): {e}")
                return {"status": "error", "message": f"인자 JSON 파싱 실패: {str(e)}"}
        elif isinstance(arguments, dict):
            parsed_args = arguments
        else:
            parsed_args = {}

        return tool_obj.execute(**parsed_args)

    async def aexecute(
        self,
        tool_name: str,
        arguments: Union[str, Dict[str, Any]]
    ) -> Any:
        """
        도구명과 인자를 받아 비동기 실행하고 결과를 반환합니다.
        """
        tool_obj = self.get_tool(tool_name)
        if not tool_obj:
            return {"status": "error", "message": f"등록되지 않은 도구: '{tool_name}'"}

        if isinstance(arguments, str):
            try:
                parsed_args = json.loads(arguments) if arguments.strip() else {}
            except Exception as e:
                return {"status": "error", "message": f"인자 JSON 파싱 실패: {str(e)}"}
        elif isinstance(arguments, dict):
            parsed_args = arguments
        else:
            parsed_args = {}

        return await tool_obj.aexecute(**parsed_args)

    def execute_tool_call(self, tool_call: ToolCallInfo) -> Dict[str, Any]:
        """
        단일 ToolCallInfo 객체를 실행하고, OpenAI 메시지 히스토리에 추가할
        'role': 'tool' 형식의 응답 메시지 딕셔너리를 생성합니다.
        """
        result = self.execute(tool_call.tool_name, tool_call.arguments)
        
        # 결과를 문자열로 직렬화
        if isinstance(result, (dict, list)):
            content_str = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            content_str = str(result)

        return {
            "role": "tool",
            "tool_call_id": tool_call.call_id,
            "name": tool_call.tool_name,
            "content": content_str
        }

    async def aexecute_tool_call(self, tool_call: ToolCallInfo) -> Dict[str, Any]:
        """비동기 ToolCallInfo 실행 및 OpenAI tool 응답 메시지 생성"""
        result = await self.aexecute(tool_call.tool_name, tool_call.arguments)
        
        if isinstance(result, (dict, list)):
            content_str = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            content_str = str(result)

        return {
            "role": "tool",
            "tool_call_id": tool_call.call_id,
            "name": tool_call.tool_name,
            "content": content_str
        }

    def execute_tool_calls(self, tool_calls: List[ToolCallInfo]) -> List[Dict[str, Any]]:
        """복수개의 ToolCallInfo를 순차 실행하고 tool 메시지 리스트를 반환합니다."""
        results = []
        for tc in tool_calls:
            msg = self.execute_tool_call(tc)
            results.append(msg)
        return results

    async def aexecute_tool_calls(self, tool_calls: List[ToolCallInfo]) -> List[Dict[str, Any]]:
        """복수개의 ToolCallInfo를 병렬 비동기 실행하고 tool 메시지 리스트를 반환합니다."""
        tasks = [self.aexecute_tool_call(tc) for tc in tool_calls]
        return await asyncio.gather(*tasks)


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    schema: Optional[Dict[str, Any]] = None
):
    """
    일반 Python 함수를 에이전트 Tool로 등록하는 데코레이터
    
    예시:
    @tool(name="calculate_tax", description="이자소득세를 계산합니다.")
    def calculate_tax(amount: float, rate: float = 0.154) -> float:
        return amount * rate
    """
    def decorator(func: Callable) -> Tool:
        tool_obj = Tool(
            name=name or func.__name__,
            func=func,
            description=description,
            schema=schema
        )
        # 전역 기본 ToolManager에 자동 등록
        get_tool_manager().register(tool_obj)
        return tool_obj
    return decorator


def get_tool_manager(reset: bool = False) -> ToolManager:
    """ToolManager의 전역 싱글톤 인스턴스를 가져옵니다."""
    if ToolManager._instance is None or reset:
        ToolManager._instance = ToolManager()
    return ToolManager._instance
