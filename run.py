#!/usr/bin/env python3
"""本地一键启动入口。

在 Cursor / VS Code 中打开本文件，点击右上角 ▶「Run Python File」即可启动服务。
也可在「运行和调试」面板选择「Docs Agent Server」后按 F5。

若未在仓库 .venv 中运行，会自动切换到 .venv（避免缺 pymupdf/fitz）。
注意：macOS 上 .venv/bin/python 常软链到系统 Python，不能用 resolve() 比路径。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_VENV_ROOT = (_ROOT / ".venv").resolve()
_VENV_PYTHON = _VENV_ROOT / "bin" / "python"


def _in_project_venv() -> bool:
    """以 sys.prefix 判断是否已在本仓库 .venv（比 executable resolve 可靠）。"""
    try:
        return Path(sys.prefix).resolve() == _VENV_ROOT
    except OSError:
        return False


def _reexec_with_venv_if_needed() -> None:
    """IDE 误选系统 Python 时，切到项目 .venv 再跑。"""
    if not _VENV_PYTHON.is_file():
        print(
            "未找到 .venv，请先执行：python3 -m venv .venv && "
            ".venv/bin/pip install -r requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if _in_project_venv():
        return
    print(
        f"切换解释器到项目 .venv\n"
        f"  当前 prefix: {sys.prefix}\n"
        f"  目标: {_VENV_PYTHON}",
        flush=True,
    )
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])


def _assert_runtime_deps() -> None:
    """启动前自检关键依赖，失败则给出明确提示。"""
    try:
        import fitz  # noqa: F401  # pymupdf
    except ModuleNotFoundError:
        print(
            "当前解释器缺少 PyMuPDF（fitz）。\n"
            f"  executable={sys.executable}\n"
            f"  prefix={sys.prefix}\n"
            "请执行：.venv/bin/pip install -r requirements.txt\n"
            "并用 .venv/bin/python run.py 启动。",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main() -> None:
    import uvicorn

    print(
        f"Docs Agent 启动中…\n"
        f"  python={sys.executable}\n"
        f"  prefix={sys.prefix}",
        flush=True,
    )
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    _reexec_with_venv_if_needed()
    _assert_runtime_deps()
    main()
