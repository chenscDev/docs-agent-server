"""通义千问 LLM 客户端（OpenAI 兼容：chat / tools / stream）。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Iterator as TypingIterator

import httpx
from openai import OpenAI

from app.agent.cancel_registry import (
    GenerationCancelled,
    bind_abort,
    is_cancelled,
    unbind_abort,
)
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# 厂商调用超时：连接短、读长（工具决策与长答）
_DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=180.0, write=60.0, pool=30.0)


class LLMClient:
    """封装 Chat Completions；可选 request_id 以支持取消时掐断 HTTP。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.llm_api_key:
            raise ValueError(
                "未配置 LLM_API_KEY：请复制 .env.example 为 .env 并填入千问 Key"
            )
        self._api_key = self._settings.llm_api_key
        self._base_url = self._settings.llm_base_url
        self._model = self._settings.llm_model
        # 无 request_id 时复用默认客户端（debug 等短调用）
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    @contextmanager
    def _openai_for_request(
        self, request_id: str | None
    ) -> TypingIterator[OpenAI]:
        """
        有 request_id 时使用独立 httpx.Client，cancel 可 close 打断阻塞请求。
        """
        if not request_id:
            yield self._client
            return

        http = httpx.Client(timeout=_DEFAULT_TIMEOUT)
        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            http_client=http,
        )

        def _abort() -> None:
            try:
                http.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("httpx close on abort: %s", exc)

        bind_abort(request_id, _abort)
        try:
            # 进入调用前若已取消，直接抛，避免发起无用请求
            if is_cancelled(request_id):
                raise GenerationCancelled()
            yield client
        finally:
            unbind_abort(request_id)
            try:
                http.close()
            except Exception:  # noqa: BLE001
                pass

    def _reraise_if_cancelled(
        self, request_id: str | None, exc: BaseException
    ) -> None:
        """取消导致的连接错误统一转为 GenerationCancelled。"""
        if request_id and is_cancelled(request_id):
            raise GenerationCancelled() from exc

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        request_id: str | None = None,
    ) -> str:
        """非流式、无工具对话。"""
        try:
            with self._openai_for_request(request_id) as client:
                response = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                )
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self._reraise_if_cancelled(request_id, exc)
            raise RuntimeError(f"调用千问失败: {exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("千问返回空内容")
        return content

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        request_id: str | None = None,
    ) -> Any:
        """
        非流式 + tools，返回 message 对象（可能含 tool_calls）。

        用于 Agent 决策轮；最终正文再走 stream_chat 或直接使用 content。
        """
        try:
            with self._openai_for_request(request_id) as client:
                response = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self._reraise_if_cancelled(request_id, exc)
            raise RuntimeError(f"调用千问(tools)失败: {exc}") from exc
        return response.choices[0].message

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        request_id: str | None = None,
    ) -> Iterator[str]:
        """流式输出增量文本（无 tools）；chunk 间隙可被 cancel 打断。"""
        try:
            with self._openai_for_request(request_id) as client:
                stream = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    stream=True,
                )
                for chunk in stream:
                    if request_id and is_cancelled(request_id):
                        raise GenerationCancelled()
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self._reraise_if_cancelled(request_id, exc)
            raise RuntimeError(f"调用千问(stream)失败: {exc}") from exc
