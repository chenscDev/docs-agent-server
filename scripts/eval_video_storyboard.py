#!/usr/bin/env python3
"""评测：固定 prompt → Storyboard schema 通过率（不依赖 LLM Key）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.video.planner import plan_storyboard  # noqa: E402
from app.video.schema import validate_storyboard  # noqa: E402


def main() -> int:
    prompts_path = ROOT / "eval" / "video_prompts.json"
    items = json.loads(prompts_path.read_text(encoding="utf-8"))
    ok = 0
    for item in items:
        prompt = item["prompt"]
        # 强制规则路径：临时清掉会触发 LLM 的情况——plan 内部已有兜底
        board = plan_storyboard(prompt, template_id="talking-captions", prefer_rules=True)
        validate_storyboard(board.to_public_dict())
        assert 3 <= len(board.scenes) <= 12
        assert board.total_duration_sec >= 6
        ok += 1
        print(f"OK {item['id']} scenes={len(board.scenes)} dur={board.total_duration_sec:.1f}s")
    print(f"PASS {ok}/{len(items)}")
    return 0 if ok == len(items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
