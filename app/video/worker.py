#!/usr/bin/env python3
"""独立视频渲染 Worker：与 API 进程分离时使用。

用法：
  # API 侧 .env：VIDEO_QUEUE_EXTERNAL=true
  python -m app.video.worker
"""

from __future__ import annotations

import logging
import signal
import time

from app.db.session import init_db
from app.video.render_queue import start_video_queue, stop_video_queue, video_queue_busy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("docs_agent.video_worker")

_stop = False


def _handle_signal(_signum, _frame) -> None:  # type: ignore[no-untyped-def]
    global _stop
    _stop = True
    logger.info("收到停止信号，准备退出…")


def main() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    init_db()
    n = start_video_queue(recover=True, force=True)
    logger.info("video worker ready recovered=%s", n)
    while not _stop:
        time.sleep(1.0)
    stop_video_queue(wait=True, timeout_sec=150.0)
    logger.info("video worker stopped busy=%s", video_queue_busy())


if __name__ == "__main__":
    main()
