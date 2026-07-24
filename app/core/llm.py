"""通义千问 LLM 客户端（OpenAI 兼容：chat / tools / stream）。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from openai import OpenAI

from app.core.config import Settings, get_settings


class LLMClient:
    """封装 Chat Completions。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.llm_api_key:
            raise ValueError(
                "未配置 LLM_API_KEY：请复制 .env.example 为 .env 并填入千问 Key"
            )
        self._client = OpenAI(
            api_key=self._settings.llm_api_key,
            base_url=self._settings.llm_base_url,
        )
        self._model = self._settings.llm_model

    def chat(self, messages: list[dict[str, Any]]) -> str:
        """非流式、无工具对话。"""
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"调用千问失败: {exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("千问返回空内容")
        return content

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        """
        非流式 + tools，返回 message 对象（可能含 tool_calls）。

        用于 Agent 决策轮；最终正文再走 stream_chat 或直接使用 content。
        """
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"调用千问(tools)失败: {exc}") from exc
        return response.choices[0].message

    def stream_chat(self, messages: list[dict[str, Any]]) -> Iterator[str]:
        """流式输出增量文本（无 tools）。"""
        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"调用千问(stream)失败: {exc}") from exc

        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
