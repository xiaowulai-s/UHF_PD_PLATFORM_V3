r"""冻结态与开发态的路径解析。

PyInstaller 打出来的 sidecar 有两类目录，且只有开发态时它们是同一个：

- 只读资源（config/*.json 等随包数据）在 `sys._MEIPASS` 解包目录里；
- 可写状态（SQLite 及其 -wal/-shm 边车、日志）必须落在用户目录，
  因为装在 `C:\Program Files` 下时整个安装树对普通用户只读。

开发态维持"相对当前工作目录"的既有行为，避免改变本地跑法的语义。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_STATE_DIR_ENV = "PD_STATE_DIR"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """仓库根（开发态）或 PyInstaller 解包根（冻结态），只放只读资源。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[2]


def state_root() -> Path:
    """可写状态根。`PD_STATE_DIR` 供测试与现场排障覆盖。"""
    override = os.environ.get(_STATE_DIR_ENV)
    if override:
        return Path(override)
    if is_frozen():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "UHF-PD"
    return Path.cwd()


def state_path(*parts: str) -> Path:
    return state_root().joinpath(*parts)


def resource_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)
