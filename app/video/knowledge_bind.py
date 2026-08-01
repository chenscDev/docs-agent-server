"""知识库更深绑定：引用片段写入口播 + 品牌禁词校验。"""

from __future__ import annotations

import re
from typing import Any

from app.video.schema import Storyboard, validate_storyboard

# 内置兜底禁词（样例文档未解析到时仍生效）
_DEFAULT_FORBIDDEN: tuple[str, ...] = (
    "全网最低",
    "永久有效",
    "百分百有效",
    "包治",
    "国家级",
    "第一品牌",
    "轻松月入",
    "永不涨价",
)


def extract_forbidden_phrases(text: str) -> list[str]:
    """
    从知识库正文提取禁词。

    支持：
    - 【禁用话术示例】「A」「B」
    - 不得宣称「A」「B」
    - 禁止「A」
    """
    raw = text or ""
    found: list[str] = []
    # 专节：禁用话术示例
    section = re.search(
        r"【禁用话术[^\n】]*】\s*([^\n【]+)",
        raw,
    )
    if section:
        for m in re.finditer(r"「([^」]{1,40})」", section.group(1)):
            w = m.group(1).strip()
            # 去掉括号说明，如「明星同款（无授权）」→ 明星同款
            w = re.sub(r"[（(].*?[）)]", "", w).strip()
            if w and w not in found:
                found.append(w)
    # 全文：不得宣称 / 禁止 + 「…」
    for m in re.finditer(
        r"(?:不得宣称|禁止|禁用)[^「\n]{0,20}「([^」]{1,40})」",
        raw,
    ):
        w = re.sub(r"[（(].*?[）)]", "", m.group(1)).strip()
        if w and w not in found:
            found.append(w)
    return found[:40]


def collect_forbidden_from_refs(refs: list[dict[str, Any]]) -> list[str]:
    """汇总引用片段中的禁词，并合并内置兜底。"""
    out: list[str] = []
    for r in refs or []:
        for w in extract_forbidden_phrases(str(r.get("snippet") or "")):
            if w not in out:
                out.append(w)
    for w in _DEFAULT_FORBIDDEN:
        if w not in out:
            out.append(w)
    return out


def _quote_clip(snippet: str, *, max_len: int = 36) -> str:
    """从片段抽一句可口播短句（跳过标题/章节行）。"""
    text = (snippet or "").strip()
    if not text:
        return ""
    skip_kw = (
        "对应场景",
        "适用场景",
        "品牌与合规",
        "口播结构",
        "字幕与画面",
        "禁用话术",
        "不得宣称",
        "禁止",
    )
    candidates: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("【"):
            continue
        # 跳过文档标题行（含间隔点或「规范/约束」收尾）
        if " · " in line or line.endswith(("规范", "约束", "手册")):
            continue
        if any(k in line for k in skip_kw):
            continue
        # 优先带序号的结构句（开场/卖点/行动号召）
        numbered = bool(
            re.match(r"^[\d一二三四五六七八九十]+[\.、．)\]]\s*", line)
        )
        cleaned = re.sub(
            r"^[\d一二三四五六七八九十]+[\.、．)\]]\s*", "", line
        )
        cleaned = re.sub(r"^[-*•]\s*", "", cleaned)
        cleaned = cleaned.strip(" ：:，,")
        if len(cleaned) < 8:
            continue
        for sep in ("。", "；", ";", "！", "!"):
            if sep in cleaned:
                cleaned = cleaned.split(sep, 1)[0].strip()
                break
        if 8 <= len(cleaned) <= 80:
            # 口播结构句优先；合规条款靠后
            score = 0
            if numbered and re.search(
                r"开场|卖点|信任|行动|痛点|场景|下单|领取", cleaned
            ):
                score = 0
            elif numbered:
                score = 2
            else:
                score = 3
            candidates.append((score, cleaned))
        if len(candidates) >= 8:
            break
    candidates.sort(key=lambda x: x[0])
    if not candidates:
        # 兜底：找第一句够长的正文
        flat = re.sub(r"\s+", " ", text)
        for sep in ("。", "；", "\n"):
            if sep in flat:
                flat = flat.split(sep, 1)[0]
                break
        candidates = [(9, flat.strip()[:max_len])]
    pick = candidates[0][1]
    if len(pick) > max_len:
        pick = pick[: max_len - 1] + "…"
    return pick


def _body_covers_quote(body: str, quote: str) -> bool:
    if not quote:
        return True
    b = (body or "").replace(" ", "")
    q = quote.replace(" ", "").rstrip("…")
    if len(q) < 4:
        return q in b
    # 取前 8 字是否已出现
    return q[: min(8, len(q))] in b


def inject_reference_quotes(
    board: Storyboard,
    refs: list[dict[str, Any]],
    *,
    force: bool = False,
) -> Storyboard:
    """
    把引用片段短句写入口播 body，并写入 sourceSnippet。

    - visual-cut（无口播）跳过 body 注入，仍打标签；
    - 已有 body 且已覆盖引用关键句时不重复拼接。
    """
    if not refs:
        return board
    by_idx = {int(r["index"]): r for r in refs if r.get("index")}
    no_tts = getattr(board, "generationType", None) == "visual-cut"
    data = board.model_dump()
    for i, sc in enumerate(data.get("scenes") or []):
        raw_idx = sc.get("sourceIndex")
        ref = None
        if raw_idx is not None:
            try:
                ref = by_idx.get(int(raw_idx))
            except (TypeError, ValueError):
                ref = None
        if ref is None:
            ref = refs[i % len(refs)]
        sc["sourceIndex"] = int(ref["index"])
        sc["sourceChunkId"] = str(ref.get("chunkId") or "")[:64]
        title = str(ref.get("documentTitle") or "知识库")
        sc["sourceLabel"] = f"[{ref['index']}] {title}"[:120]
        quote = _quote_clip(str(ref.get("snippet") or ""))
        sc["sourceSnippet"] = quote[:120]
        if no_tts or not quote:
            continue
        body = str(sc.get("body") or "").strip()
        if force or not body:
            sc["body"] = quote[:200]
        elif not _body_covers_quote(body, quote):
            # 保留原口播，末尾补引用要点（控制长度）
            merged = f"{body.rstrip('。')}。{quote}"
            sc["body"] = merged[:200]
    return validate_storyboard(data)


def check_forbidden_words(
    board: Storyboard,
    forbidden: list[str],
) -> list[dict[str, Any]]:
    """扫描 headline/body，返回命中列表。"""
    hits: list[dict[str, Any]] = []
    if not forbidden:
        return hits
    for sc in board.scenes:
        blob = f"{sc.headline}\n{sc.body}"
        for w in forbidden:
            if w and w in blob:
                hits.append(
                    {
                        "sceneId": sc.id,
                        "sceneIndex": sc.index,
                        "word": w,
                        "field": "headline" if w in sc.headline else "body",
                    }
                )
    return hits


def scrub_forbidden_words(
    board: Storyboard,
    forbidden: list[str],
) -> tuple[Storyboard, list[str]]:
    """
    自动替换禁词为中性表述，返回 (新分镜, 警告文案列表)。
    """
    hits = check_forbidden_words(board, forbidden)
    if not hits:
        return board, []
    data = board.model_dump()
    warnings: list[str] = []
    seen: set[str] = set()
    for h in hits:
        w = str(h["word"])
        key = f"{h['sceneId']}:{w}"
        if key in seen:
            continue
        seen.add(key)
        for sc in data.get("scenes") or []:
            if sc.get("id") != h["sceneId"]:
                continue
            for field in ("headline", "body"):
                val = str(sc.get(field) or "")
                if w in val:
                    sc[field] = val.replace(w, "（已合规改写）")[:200]
            warnings.append(
                f"第{int(h['sceneIndex']) + 1}镜含禁词「{w}」，已自动改写"
            )
            break
    data["complianceWarnings"] = warnings[:12]
    return validate_storyboard(data), warnings


def bind_knowledge_to_storyboard(
    board: Storyboard,
    refs: list[dict[str, Any]],
    *,
    scrub_forbidden: bool = True,
) -> tuple[Storyboard, list[str]]:
    """打标 + 引用写入口播 + 禁词清洗，返回 (分镜, 警告)。"""
    if not refs:
        return board, []
    next_board = inject_reference_quotes(board, refs)
    warnings: list[str] = []
    if scrub_forbidden:
        forbidden = collect_forbidden_from_refs(refs)
        next_board, warnings = scrub_forbidden_words(next_board, forbidden)
    else:
        data = next_board.model_dump()
        data["complianceWarnings"] = []
        next_board = validate_storyboard(data)
    return next_board, warnings
