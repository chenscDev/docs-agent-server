#!/usr/bin/env python3
"""评测：固定 prompt → Storyboard schema；覆盖三模板与渲染选项字段。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.video.planner import plan_storyboard  # noqa: E402
from app.video.schema import TEMPLATE_CATALOG, validate_storyboard  # noqa: E402


def main() -> int:
    prompts_path = ROOT / "eval" / "video_prompts.json"
    items = json.loads(prompts_path.read_text(encoding="utf-8"))
    ok = 0
    total = 0
    templates = [t["id"] for t in TEMPLATE_CATALOG]

    for item in items:
        prompt = item["prompt"]
        for tid in templates:
            total += 1
            board = plan_storyboard(
                prompt,
                template_id=tid,  # type: ignore[arg-type]
                prefer_rules=True,
                knowledge_hint="品牌禁止夸大宣传",
            )
            # 渲染选项字段应可写入
            data = board.to_public_dict()
            data["speechRate"] = 1.2
            data["bgmEnabled"] = True
            data["bgmVolume"] = 0.2
            board2 = validate_storyboard(data)
            assert board2.templateId == tid
            assert 3 <= len(board2.scenes) <= 12
            assert board2.total_duration_sec >= 6
            assert abs(board2.speechRate - 1.2) < 1e-6
            ok += 1
            print(
                f"OK {item['id']}/{tid} scenes={len(board2.scenes)} "
                f"dur={board2.total_duration_sec:.1f}s"
            )

    print(f"PASS {ok}/{total}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
