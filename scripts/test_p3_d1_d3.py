"""P3-D1/D2/D3 轻量单测：不依赖外网与数据库。"""

from __future__ import annotations

from app.agent.citations import build_citations
from app.rag.chunker import split_text
from app.rag.faiss_store import SearchHit
from app.rag.rerank import _local_rerank


def test_citation_no_fallback_when_no_markers() -> None:
    hits = [
        {"index": 1, "text": "a", "chunk_id": "c1", "document_id": "d1", "document_title": "t"},
        {"index": 2, "text": "b", "chunk_id": "c2", "document_id": "d1", "document_title": "t"},
        {"index": 3, "text": "c", "chunk_id": "c3", "document_id": "d1", "document_title": "t"},
    ]
    assert build_citations("没有任何引用标记的回答", hits, fallback_top3=False) == []


def test_citation_with_markers() -> None:
    hits = [
        {"index": 1, "text": "条款一", "chunk_id": "c1", "document_id": "d1", "document_title": "手册"},
        {"index": 2, "text": "条款二", "chunk_id": "c2", "document_id": "d1", "document_title": "手册"},
    ]
    cites = build_citations("试用期为 3 个月[2]。", hits, fallback_top3=False)
    assert len(cites) == 1
    assert cites[0]["index"] == 2
    assert cites[0]["chunkId"] == "c2"


def test_citation_fallback_top3_opt_in() -> None:
    hits = [
        {"index": 1, "text": "a", "chunk_id": "c1", "document_id": "d1", "document_title": "t"},
        {"index": 2, "text": "b", "chunk_id": "c2", "document_id": "d1", "document_title": "t"},
        {"index": 3, "text": "c", "chunk_id": "c3", "document_id": "d1", "document_title": "t"},
        {"index": 4, "text": "d", "chunk_id": "c4", "document_id": "d1", "document_title": "t"},
    ]
    cites = build_citations("无标记", hits, fallback_top3=True)
    assert [c["index"] for c in cites] == [1, 2, 3]


def test_structured_chunk_keeps_heading() -> None:
    text = """# 考勤制度

前言说明。

## 试用期

试用期为三个月。

过长段落应能切分。""" + ("补充。" * 80)
    pieces = split_text(text, chunk_size=120, overlap=20)
    assert pieces
    assert any(p.heading and "试用期" in (p.heading or "") for p in pieces)
    assert any("试用期为三个月" in p.content for p in pieces)


def test_local_rerank_prefers_overlap() -> None:
    hits = [
        SearchHit("c1", "d1", 0.55, "量子计算与超导材料研究综述", "doc", 0),
        SearchHit("c2", "d1", 0.52, "员工试用期为三个月，考核合格转正", "doc", 1),
    ]
    out = _local_rerank("试用期多久", hits, top_n=2)
    assert out[0].chunk_id == "c2"


if __name__ == "__main__":
    test_citation_no_fallback_when_no_markers()
    test_citation_with_markers()
    test_citation_fallback_top3_opt_in()
    test_structured_chunk_keeps_heading()
    test_local_rerank_prefers_overlap()
    print("OK")
