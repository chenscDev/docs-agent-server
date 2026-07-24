"""文档相关 API Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    """文档对外结构（驼峰字段，便于 RN 直接使用）。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    knowledge_base_id: str = Field(serialization_alias="knowledgeBaseId")
    title: str
    mime_type: str = Field(serialization_alias="mimeType")
    byte_size: int = Field(serialization_alias="byteSize")
    status: str
    progress: float | None = None
    stage_message: str | None = Field(default=None, serialization_alias="stageMessage")
    chunk_count: int = Field(serialization_alias="chunkCount")
    error_code: str | None = Field(default=None, serialization_alias="errorCode")
    error_message: str | None = Field(default=None, serialization_alias="errorMessage")
    created_at: datetime | None = Field(default=None, serialization_alias="createdAt")
    updated_at: datetime | None = Field(default=None, serialization_alias="updatedAt")


class DocumentListOut(BaseModel):
    """文档列表。"""

    items: list[DocumentOut]
