"""
================================================================================
OpenAI 호환 단일 모델 관리자 (core/model_manager.py)
--------------------------------------------------------------------------------
기능:
1. OpenAI Python SDK 기반 연결 객체 관리 (OpenAI, AsyncOpenAI)
2. OpenAI 공식 API 및 로컬/사설 OpenAI 호환 API (vLLM, Ollama, OpenRouter 등) 지원
3. API 키 로드, 기본 모델 선택 및 동적 모델/파라미터 변경
4. 동기/비동기 Chat Completion & Tool Calling 네이티브 인터페이스
5. 대화 히스토리 및 LLM 응답 객체 정형화 (LLMResponse)
================================================================================
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional, Union, Generator, AsyncGenerator
from dataclasses import dataclass, field

import openai
from openai import OpenAI, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage

from config import config, ModelConfig

logger = logging.getLogger("agent.model_manager")


@dataclass
class ToolCallInfo:
    """도구 호출 요청 정보 데이터클래스"""
    call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    raw_arguments: str


@dataclass
class LLMResponse:
    """LLM 응답 표준화 래퍼 객체"""
    content: Optional[str] = None
    role: str = "assistant"
    tool_calls: List[ToolCallInfo] = field(default_factory=list)
    finish_reason: Optional[str] = None
    model: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)
    raw_message: Optional[ChatCompletionMessage] = None

    @property
    def has_tool_calls(self) -> bool:
        """도구 호출 요청이 포함되어 있는지 여부"""
        return bool(self.tool_calls)

    def to_assistant_message(self) -> Dict[str, Any]:
        """대화 히스토리에 다시 추가할 수 있는 OpenAI 어시스턴트 메시지 딕셔너리로 변환"""
        msg: Dict[str, Any] = {"role": "assistant"}
        if self.content is not None:
            msg["content"] = self.content
        else:
            msg["content"] = None

        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.call_id,
                    "type": "function",
                    "function": {
                        "name": tc.tool_name,
                        "arguments": tc.raw_arguments
                    }
                }
                for tc in self.tool_calls
            ]
        return msg


class ModelManager:
    """
    OpenAI 호환 단일 모델 관리자 클래스
    
    애플리케이션 전역에서 LLM 연결 객체를 관리하고, 모델 선택 및 추론 인터페이스를 제공합니다.
    """

    _instance: Optional["ModelManager"] = None

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None
    ):
        """
        ModelManager 초기화
        
        :param api_key: OpenAI 또는 호환 API 키 (기본: config.model.api_key)
        :param base_url: API 엔드포인트 Base URL (기본: config.model.base_url)
        :param model_name: 사용할 기본 LLM 모델명 (기본: config.model.default_model)
        :param temperature: 샘플링 온도 (기본: config.model.temperature)
        :param max_tokens: 최대 생성 토큰 수 (기본: config.model.max_tokens)
        :param timeout: 요청 타임아웃 초 (기본: config.model.timeout)
        :param max_retries: 최대 재시도 횟수 (기본: config.model.max_retries)
        """
        cfg: ModelConfig = config.model

        self.api_key = api_key or cfg.api_key or os.getenv("OPENAI_API_KEY", "EMPTY")
        self.base_url = base_url or cfg.base_url
        self.model_name = model_name or cfg.default_model
        self.temperature = temperature if temperature is not None else cfg.temperature
        self.max_tokens = max_tokens if max_tokens is not None else cfg.max_tokens
        self.timeout = timeout if timeout is not None else cfg.timeout
        self.max_retries = max_retries if max_retries is not None else cfg.max_retries

        # OpenAI 클라이언트 초기화 (동기 / 비동기)
        self._init_clients()

        logger.info(
            f"✅ ModelManager 초기화 완료 [Model: {self.model_name}, BaseURL: {self.base_url}]"
        )

    def _init_clients(self) -> None:
        """내부 동기/비동기 OpenAI 클라이언트 인스턴스 생성"""
        client_kwargs = {
            "api_key": self.api_key,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        self.client: OpenAI = OpenAI(**client_kwargs)
        self.async_client: AsyncOpenAI = AsyncOpenAI(**client_kwargs)

    # --------------------------------------------------------------------------
    # 동적 모델 및 하이퍼파라미터 설정 인터페이스
    # --------------------------------------------------------------------------
    def set_model(self, model_name: str) -> str:
        """
        사용할 LLM 모델명을 동적으로 변경합니다.
        
        :param model_name: 변경할 모델명 (예: "gpt-4o", "gpt-4o-mini", "claude-3-5-sonnet")
        :return: 변경된 모델명
        """
        if not model_name or not model_name.strip():
            raise ValueError("모델명은 비어 있을 수 없습니다.")
        
        old_model = self.model_name
        self.model_name = model_name.strip()
        logger.info(f"🔄 LLM 모델 변경: {old_model} -> {self.model_name}")
        return self.model_name

    def set_parameters(
        self,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        런타임에 모델 생성 파라미터를 변경합니다.
        """
        if temperature is not None:
            if not (0.0 <= temperature <= 2.0):
                raise ValueError("temperature는 0.0과 2.0 사이의 값이어야 합니다.")
            self.temperature = temperature

        if max_tokens is not None:
            if max_tokens <= 0:
                raise ValueError("max_tokens는 양의 정수여야 합니다.")
            self.max_tokens = max_tokens

        if timeout is not None:
            self.timeout = timeout
            self._init_clients()

        if max_retries is not None:
            self.max_retries = max_retries
            self._init_clients()

        return self.get_config()

    def set_api_credentials(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None
    ) -> None:
        """API Key 또는 Base URL 변경 시 클라이언트 재초기화"""
        if api_key:
            self.api_key = api_key
        if base_url:
            self.base_url = base_url
        self._init_clients()
        logger.info(f"🔄 API 자격증명 업데이트 완료 [BaseURL: {self.base_url}]")

    def get_config(self) -> Dict[str, Any]:
        """현재 모델 관리자의 설정 상태를 반환합니다."""
        return {
            "model_name": self.model_name,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "api_key_configured": bool(self.api_key and self.api_key != "EMPTY")
        }

    # --------------------------------------------------------------------------
    # 응답 파싱 내부 헬퍼
    # --------------------------------------------------------------------------
    def _parse_completion_response(self, completion: ChatCompletion) -> LLMResponse:
        """ChatCompletion 객체를 표준 LLMResponse로 변환"""
        choice = completion.choices[0]
        msg = choice.message
        
        tool_calls: List[ToolCallInfo] = []
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args_str = tc.function.arguments
                try:
                    fn_args = json.loads(fn_args_str)
                except Exception:
                    fn_args = {"_raw_argument": fn_args_str}

                tool_calls.append(
                    ToolCallInfo(
                        call_id=tc.id,
                        tool_name=fn_name,
                        arguments=fn_args,
                        raw_arguments=fn_args_str
                    )
                )

        usage_dict = {}
        if completion.usage:
            usage_dict = {
                "prompt_tokens": completion.usage.prompt_tokens,
                "completion_tokens": completion.usage.completion_tokens,
                "total_tokens": completion.usage.total_tokens
            }

        return LLMResponse(
            content=msg.content,
            role=msg.role or "assistant",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            model=completion.model,
            usage=usage_dict,
            raw_message=msg
        )

    # --------------------------------------------------------------------------
    # 동기 호출 메서드 (Sync Chat Completion & Tool Calling)
    # --------------------------------------------------------------------------
    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> LLMResponse:
        """
        OpenAI 호환 동기 대화 추론 및 Tool Calling 수행
        
        :param messages: 대화 히스토리 리스트 (예: [{"role": "user", "content": "..."}])
        :param tools: OpenAI 포맷 도구 스키마 리스트 (선택)
        :param tool_choice: "auto", "required", "none" 또는 특정 도구 지정
        :param model: 1회성 모델 오버라이드
        :param temperature: 1회성 온도 오버라이드
        :param max_tokens: 1회성 최대 토큰 오버라이드
        :param response_format: 응답 형식 지정 (예: {"type": "json_object"})
        :return: LLMResponse 응답 객체
        """
        target_model = model or self.model_name
        temp = temperature if temperature is not None else self.temperature
        max_toks = max_tokens if max_tokens is not None else self.max_tokens

        req_params: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_toks,
            **kwargs
        }

        if tools:
            req_params["tools"] = tools
            if tool_choice is not None:
                req_params["tool_choice"] = tool_choice

        if response_format:
            req_params["response_format"] = response_format

        try:
            logger.debug(f"LLM 호출 시작 [Model: {target_model}, Messages: {len(messages)}건, Tools: {len(tools) if tools else 0}개]")
            completion = self.client.chat.completions.create(**req_params)
            response = self._parse_completion_response(completion)
            logger.debug(f"LLM 응답 수신 완료 [Finish: {response.finish_reason}, ToolCalls: {len(response.tool_calls)}]")
            return response
        except Exception as e:
            logger.error(f"LLM 호출 실패 ({target_model}): {e}")
            raise e

    def generate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> str:
        """단일 텍스트 프롬프트에 대한 단순 텍스트 답변 생성 편의 메서드"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = self.chat_completion(messages=messages, model=model, **kwargs)
        return response.content or ""

    # --------------------------------------------------------------------------
    # 비동기 호출 메서드 (Async Chat Completion & Tool Calling)
    # --------------------------------------------------------------------------
    async def achat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> LLMResponse:
        """OpenAI 호환 비동기 대화 추론 및 Tool Calling 수행"""
        target_model = model or self.model_name
        temp = temperature if temperature is not None else self.temperature
        max_toks = max_tokens if max_tokens is not None else self.max_tokens

        req_params: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": max_toks,
            **kwargs
        }

        if tools:
            req_params["tools"] = tools
            if tool_choice is not None:
                req_params["tool_choice"] = tool_choice

        if response_format:
            req_params["response_format"] = response_format

        try:
            logger.debug(f"Async LLM 호출 시작 [Model: {target_model}]")
            completion = await self.async_client.chat.completions.create(**req_params)
            response = self._parse_completion_response(completion)
            return response
        except Exception as e:
            logger.error(f"Async LLM 호출 실패 ({target_model}): {e}")
            raise e

    async def agenerate_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs
    ) -> str:
        """단일 텍스트 프롬프트에 대한 비동기 단순 텍스트 답변 생성 편의 메서드"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await self.achat_completion(messages=messages, model=model, **kwargs)
        return response.content or ""


# 전역 싱글톤 인스턴스 팩토리
def get_model_manager(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model_name: Optional[str] = None,
    reset: bool = False
) -> ModelManager:
    """
    ModelManager의 전역 싱글톤 인스턴스를 가져옵니다.
    
    :param api_key: 필요시 API 키 지정
    :param base_url: 필요시 Base URL 지정
    :param model_name: 필요시 기본 모델 지정
    :param reset: True일 경우 기존 인스턴스를 새로고침하여 재생성
    """
    if ModelManager._instance is None or reset:
        ModelManager._instance = ModelManager(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name
        )
    else:
        if model_name:
            ModelManager._instance.set_model(model_name)
        if api_key or base_url:
            ModelManager._instance.set_api_credentials(api_key, base_url)

    return ModelManager._instance
