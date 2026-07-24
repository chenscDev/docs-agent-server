"""本地一键启动入口。

在 Cursor / VS Code 中打开本文件，点击右上角 ▶「Run Python File」即可启动服务。
也可在「运行和调试」面板选择「Docs Agent Server」后按 F5。
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
