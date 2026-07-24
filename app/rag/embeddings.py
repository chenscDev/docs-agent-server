"""千问 Embedding 客户端（OpenAI 兼容协议）。"""

from __future__ import annotations

import logging

from openai import OpenAI

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """批量文本向量化。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.llm_api_key:
            raise ValueError("未配置 LLM_API_KEY，无法调用 Embedding")
        self._client = OpenAI(
            api_key=self._settings.llm_api_key,
            base_url=self._settings.llm_base_url,
        )
        self._model = self._settings.embedding_model
        # 通义兼容接口单批上限 10，这里做硬封顶避免环境变量配大
        self._batch_size = max(1, min(self._settings.embedding_batch_size, 10))

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        返回与 texts 等长的向量列表。

        空字符串会用占位空格，避免部分接口拒空输入。
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = [t if t.strip() else " " for t in texts[start : start + self._batch_size]]
            try:
                resp = self._client.embeddings.create(
                    model=self._model,
                    input=batch,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Embedding 调用失败: {exc}") from exc

            # 按 index 排序，保证与输入对齐
            ordered = sorted(resp.data, key=lambda x: x.index)
            vectors.extend([list(item.embedding) for item in ordered])
            logger.info(
                "embed batch %s-%s model=%s",
                start,
                start + len(batch),
                self._model,
            )
        return vectors
