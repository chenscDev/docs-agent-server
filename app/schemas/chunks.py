"""Chunk 列表 Schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChunkOut(BaseModel):
    """片段列表项（仅 snippet，全文走后续 Citation 接口）。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    index: int
    snippet: str
    token_estimate: int | None = Field(default=None, serialization_alias="tokenEstimate")


class ChunkListOut(BaseModel):
    """片段列表。"""

    items: list[ChunkOut]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")
