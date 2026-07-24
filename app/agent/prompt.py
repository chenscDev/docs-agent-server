"""System Prompt 与情境短句（D5 非流式 RAG）。"""

from __future__ import annotations

SYSTEM_PROMPT = """你是「文档问答助手」，只根据下方「检索到的文档片段」回答。
你不是通用百科：片段中没有的内容不要编造。

引用规则：
- 片段带有序号 [1]、[2]、…
- 凡来自文档的事实，在分句后添加对应引用，如：……三个月。[1]
- 禁止使用不存在的序号；禁止杜撰文档名
- 使用简洁中文；默认不超过 400 字

若片段为空或与问题明显无关：明确说明当前知识库未找到相关内容，不要编造。
不要输出 JSON，不要复述原始片段结构。"""


def build_scene_line(*, ready_count: int) -> str:
    """每轮动态情境。"""
    return f"当前知识库已就绪文档数：{ready_count}。"


def build_context_block(hits: list[dict]) -> str:
    """
    组装检索上下文。

    hits 项需含：index, document_title, text
    """
    if not hits:
        return "（本次检索未命中任何片段）"

    lines: list[str] = ["检索到的文档片段："]
    for h in hits:
        title = h.get("document_title") or "未命名文档"
        text = (h.get("text") or "")[:400]
        lines.append(f"[{h['index']}] 《{title}》\n{text}")
    return "\n\n".join(lines)


REFUSAL_TEXT = (
    "当前知识库中没有找到与该问题直接相关的内容，无法根据已有文档给出准确结论。"
    "你可以换个关键词，或确认相关文档已上传并显示为「可问答」。"
)
