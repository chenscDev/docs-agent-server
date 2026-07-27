#!/usr/bin/env python3
"""
D16：跑固定评测集（默认非流式 POST /v1/chat）。

用法（服务已启动、已配置 LLM_API_KEY）：
  cd docs-agent-server
  .venv/bin/python scripts/run_eval.py
  .venv/bin/python scripts/run_eval.py --base-url http://127.0.0.1:8000 --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"
DEFAULT_QUESTIONS = EVAL_DIR / "questions.json"
RESULTS_DIR = EVAL_DIR / "results"


def _load_api_token() -> str:
    """优先环境变量，其次读取仓库 .env 中的 API_TOKEN。"""
    env = (os.environ.get("API_TOKEN") or "").strip()
    if env:
        return env
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return ""
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "API_TOKEN":
                return value.strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


_API_TOKEN = _load_api_token()


def _with_auth(headers: dict[str, str]) -> dict[str, str]:
    """附加 Bearer Token（若已配置）。"""
    out = dict(headers)
    if _API_TOKEN:
        out["Authorization"] = f"Bearer {_API_TOKEN}"
    return out


def http_json(
    method: str,
    url: str,
    *,
    data: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """发起 JSON 请求；失败抛 RuntimeError。"""
    body = None
    headers = _with_auth({"Accept": "application/json"})
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"请求失败 {url}: {exc}") from exc


def upload_corpus(base_url: str, kb_id: str, corpus_path: Path) -> str:
    """上传评测语料，返回 documentId。"""
    boundary = f"----EvalBoundary{int(time.time())}"
    file_bytes = corpus_path.read_bytes()
    filename = corpus_path.name
    parts: list[bytes] = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        (
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode()
    )
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    url = f"{base_url}/v1/knowledge-bases/{kb_id}/documents"
    req = urllib.request.Request(
        url,
        data=body,
        headers=_with_auth(
            {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            }
        ),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"上传失败 HTTP {exc.code}: {detail}") from exc

    doc_id = payload.get("id") or payload.get("documentId")
    if not doc_id:
        raise RuntimeError(f"上传响应缺少 id: {payload}")
    return str(doc_id)


def wait_ready(base_url: str, doc_id: str, *, timeout_s: float = 180.0) -> dict[str, Any]:
    """轮询文档直到 ready / failed。"""
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = http_json("GET", f"{base_url}/v1/documents/{doc_id}", timeout=30)
        status = last.get("status")
        if status == "ready":
            return last
        if status == "failed":
            raise RuntimeError(
                f"文档解析失败: {last.get('errorCode') or last.get('error_code')} "
                f"{last.get('errorMessage') or last.get('error_message') or last}"
            )
        time.sleep(1.5)
    raise RuntimeError(f"等待 ready 超时，最后状态: {last.get('status')}")


def create_session(base_url: str, kb_id: str) -> str:
    """创建评测会话。"""
    out = http_json(
        "POST",
        f"{base_url}/v1/sessions",
        data={"knowledgeBaseId": kb_id, "title": "D16-eval"},
    )
    sid = out.get("id")
    if not sid:
        raise RuntimeError(f"创建会话失败: {out}")
    return str(sid)


def ask_chat(base_url: str, session_id: str, message: str) -> dict[str, Any]:
    """非流式问答（便于评测落盘）。"""
    return http_json(
        "POST",
        f"{base_url}/v1/chat",
        data={"sessionId": session_id, "message": message},
        timeout=180,
    )


def ask_turns(
    base_url: str, session_id: str, turns: list[dict[str, Any]]
) -> dict[str, Any]:
    """
    多轮同会话提问，返回最后一轮结果（用于 followup 粗判）。
    """
    last: dict[str, Any] = {}
    for turn in turns:
        msg = str(turn.get("message") or "").strip()
        if not msg:
            raise RuntimeError(f"turns 中存在空 message: {turns}")
        last = ask_chat(base_url, session_id, msg)
    if not last:
        raise RuntimeError("turns 为空，无法评测")
    return last


def _norm(text: str) -> str:
    """去掉空白，降低「30天」vs「30 天」类误杀。"""
    return re.sub(r"\s+", "", text or "")


def _contains_any(text: str, patterns: list[str] | None) -> bool:
    if not patterns:
        return True
    n = _norm(text)
    return any(_norm(p) in n for p in patterns)


def _contains_all(text: str, patterns: list[str] | None) -> bool:
    if not patterns:
        return True
    n = _norm(text)
    return all(_norm(p) in n for p in patterns)


def score_answer(q: dict[str, Any], answer: str, citations: list[Any]) -> dict[str, Any]:
    """
    自动粗判。

    返回：pass / fail / unclear + reasons
    """
    answer = answer or ""
    reasons: list[str] = []
    ok = True

    if q.get("refuse"):
        if not _contains_any(answer, q.get("expectAny")):
            ok = False
            reasons.append("拒答话术未命中 expectAny")
        forbidden = q.get("forbidden") or []
        hit_forbidden = [f for f in forbidden if f in answer]
        # 拒答场景：若同时出现「未找到」类话术，允许少量禁词出现在否定句中；
        # 这里采用更严：出现禁词且未出现拒答关键词 → fail；出现禁词也记 warn。
        if hit_forbidden:
            if _contains_any(answer, q.get("expectAny")):
                reasons.append(f"含禁词但已拒答（人工复核）: {hit_forbidden}")
            else:
                ok = False
                reasons.append(f"疑似编造，命中禁词: {hit_forbidden}")
        if not reasons and ok:
            reasons.append("拒答粗判通过")
        return {
            "verdict": "pass" if ok else "fail",
            "reasons": reasons,
            "citationCount": len(citations or []),
        }

    if not _contains_all(answer, q.get("expectMust")):
        ok = False
        missing = [p for p in (q.get("expectMust") or []) if p not in answer]
        reasons.append(f"缺少 expectMust: {missing}")
    if not _contains_any(answer, q.get("expectAny")):
        ok = False
        reasons.append(f"未命中 expectAny: {q.get('expectAny')}")

    if q.get("expectCitations"):
        if not citations:
            # 答案里也可能写了 [1]
            if not re.search(r"\[\d+\]", answer):
                ok = False
                reasons.append("期望有引用但 citations 为空且正文无 [n]")
            else:
                reasons.append("citations 字段空，但正文含 [n]（人工复核）")

    if ok and not reasons:
        reasons.append("要点粗判通过")

    return {
        "verdict": "pass" if ok else "fail",
        "reasons": reasons,
        "citationCount": len(citations or []),
    }


def write_markdown_summary(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """写可读的评测摘要。"""
    lines = [
        f"# 评测结果 {summary['ranAt']}",
        "",
        f"- baseUrl: `{summary['baseUrl']}`",
        f"- documentId: `{summary.get('documentId')}`",
        f"- 题数: {summary['total']}  pass: **{summary['pass']}**  fail: **{summary['fail']}**",
        f"- 通过率: **{summary['passRate']}**",
        "",
        "| ID | 类别 | 粗判 | 延迟ms | 问题 |",
        "|----|------|------|--------|------|",
    ]
    for r in rows:
        q = r["question"].replace("|", "\\|")
        lines.append(
            f"| {r['id']} | {r['category']} | {r['verdict']} | "
            f"{r.get('latencyMs', '')} | {q} |"
        )
    lines.extend(["", "## 失败明细", ""])
    fails = [r for r in rows if r["verdict"] != "pass"]
    if not fails:
        lines.append("无失败题。")
    else:
        for r in fails:
            lines.append(f"### {r['id']} · {r['question']}")
            lines.append("")
            lines.append(f"- reasons: {', '.join(r.get('reasons') or [])}")
            lines.append(f"- answer: {r.get('answer', '')[:500]}")
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="D16 文档问答评测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（调试）")
    parser.add_argument("--skip-upload", action="store_true", help="不上传语料，直接用现有 KB")
    parser.add_argument("--document-id", default="", help="已 ready 的文档 id（配合 --skip-upload）")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    spec = json.loads(args.questions.read_text(encoding="utf-8"))
    kb_id = spec.get("kbId") or "kb_default"
    questions: list[dict[str, Any]] = list(spec.get("questions") or [])
    if args.limit and args.limit > 0:
        questions = questions[: args.limit]

    # 健康检查
    try:
        health = http_json("GET", f"{base_url}/health", timeout=5)
    except RuntimeError as exc:
        print(f"服务不可用: {exc}", file=sys.stderr)
        return 2
    if health.get("status") != "ok":
        print(f"health 异常: {health}", file=sys.stderr)
        return 2

    doc_id = args.document_id
    if not args.skip_upload:
        corpus_rel = spec.get("corpusFile") or "corpus/acme_handbook.md"
        corpus_path = (EVAL_DIR / corpus_rel).resolve()
        if not corpus_path.is_file():
            print(f"语料不存在: {corpus_path}", file=sys.stderr)
            return 2
        print(f"上传语料: {corpus_path.name}")
        doc_id = upload_corpus(base_url, kb_id, corpus_path)
        print(f"等待 ready: {doc_id}")
        wait_ready(base_url, doc_id)
        print("文档已 ready")
    elif not doc_id:
        print("使用 --skip-upload 时建议传 --document-id", file=sys.stderr)

    session_id = create_session(base_url, kb_id)
    print(f"评测会话: {session_id}")

    rows: list[dict[str, Any]] = []
    pass_n = 0
    fail_n = 0

    for i, q in enumerate(questions, start=1):
        qid = q.get("id") or f"Q{i}"
        turns_preview = q.get("turns")
        q_label = q.get("question") or (
            " → ".join(str(t.get("message") or "") for t in (turns_preview or []))
        )
        print(f"[{i}/{len(questions)}] {qid} {q_label}")
        t0 = time.time()
        try:
            # 每题独立会话，避免历史污染（followup 题在同一会话内走 turns）
            sid = create_session(base_url, kb_id)
            turns = q.get("turns")
            if turns:
                result = ask_turns(base_url, sid, list(turns))
            else:
                result = ask_chat(base_url, sid, str(q["question"]))
            answer = str(result.get("answer") or "")
            citations = result.get("citations") or []
            usage = result.get("usage") or {}
            latency = usage.get("latencyMs")
            if latency is None:
                latency = int((time.time() - t0) * 1000)
            scored = score_answer(q, answer, citations)
            row = {
                "id": qid,
                "category": q.get("category"),
                "question": q_label,
                "answer": answer,
                "citations": citations,
                "requestId": result.get("requestId"),
                "usage": usage,
                "latencyMs": latency,
                "verdict": scored["verdict"],
                "reasons": scored["reasons"],
                "sessionId": sid,
                "followupRewriteUsed": usage.get("followupRewriteUsed"),
            }
        except Exception as exc:  # noqa: BLE001
            row = {
                "id": qid,
                "category": q.get("category"),
                "question": q_label,
                "answer": "",
                "citations": [],
                "verdict": "fail",
                "reasons": [f"请求异常: {exc}"],
                "latencyMs": int((time.time() - t0) * 1000),
            }
        if row["verdict"] == "pass":
            pass_n += 1
        else:
            fail_n += 1
        print(f"  -> {row['verdict']}: {'; '.join(row.get('reasons') or [])}")
        rows.append(row)
        # 轻微限速，避免打爆 API
        time.sleep(0.3)

    total = len(rows)
    rate = f"{(pass_n / total * 100):.1f}%" if total else "0%"
    ran_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    summary = {
        "ranAt": ran_at,
        "baseUrl": base_url,
        "kbId": kb_id,
        "documentId": doc_id,
        "sessionId": session_id,
        "total": total,
        "pass": pass_n,
        "fail": fail_n,
        "passRate": rate,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = RESULTS_DIR / f"eval_{stamp}.json"
    md_path = RESULTS_DIR / f"eval_{stamp}.md"
    payload = {"summary": summary, "items": rows}
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_markdown_summary(md_path, summary, rows)

    print("")
    print(f"完成: {pass_n}/{total} pass ({rate})")
    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    print("请将失败题人工复核后记入 eval/FAILURE_CASES.md")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
