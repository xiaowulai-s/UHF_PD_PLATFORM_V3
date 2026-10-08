r"""冻结态路径解析。

装机版落在 `C:\Program Files` 时整个安装树对普通用户只读，SQLite 连 -wal 边车都建不
出来；这类问题只在打包后现场才暴露，所以在此用 PD_STATE_DIR 与 sys.frozen 打桩锁住。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.foundation import runtime_paths as rp
from sidecar import _anchor


@pytest.fixture
def clean_env(monkeypatch):
    for key in ("PD_STATE_DIR", "LOCALAPPDATA", "HOME", "USERPROFILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


def test_dev_mode_state_root_is_cwd(clean_env, tmp_path, monkeypatch):
    """开发态维持"相对当前目录"的既有语义，不改变本地跑法。"""
    monkeypatch.chdir(tmp_path)
    assert rp.state_root() == tmp_path
    assert not rp.is_frozen()


def test_pd_state_dir_overrides_everything(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv("PD_STATE_DIR", str(tmp_path / "elsewhere"))
    assert rp.state_root() == tmp_path / "elsewhere"
    assert rp.state_path("logs", "sidecar.log") == tmp_path / "elsewhere" / "logs" / "sidecar.log"


def test_frozen_state_root_moves_to_user_dir(clean_env, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    local = str(Path("C:/Users/op/AppData/Local"))
    monkeypatch.setenv("LOCALAPPDATA", local)
    assert rp.state_root() == Path(local) / "UHF-PD"


def test_frozen_without_localappdata_falls_back_to_home(clean_env, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("HOME", str(Path.home()))
    assert rp.state_root().name == "UHF-PD"
    assert rp.state_root().is_absolute()


def test_frozen_resources_come_from_meipass(clean_env, monkeypatch, tmp_path):
    """只读 config/ 在 _MEIPASS 里，不在 exe 目录下。"""
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    assert rp.resource_path("config", "hardware_config.json") == (
        tmp_path / "_internal" / "config" / "hardware_config.json"
    )


def test_dev_resource_root_is_repo_root(clean_env):
    assert rp.resource_path("sidecar.py").is_file()
    assert rp.project_root() == Path(__file__).resolve().parents[1]


def test_anchor_keeps_explicit_absolute_path(clean_env, tmp_path):
    got = _anchor(str(tmp_path / "custom.db"))
    assert Path(got) == tmp_path / "custom.db"


def test_anchor_relativises_under_state_root(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("PD_STATE_DIR", str(tmp_path))
    assert Path(_anchor("data/monitor.db")) == tmp_path / "data" / "monitor.db"


def test_anchor_default_lands_under_state_root(clean_env, monkeypatch, tmp_path):
    """sidecar 不传 --db 时也不能往安装目录里写。"""
    monkeypatch.setenv("PD_STATE_DIR", str(tmp_path))
    assert Path(_anchor(None, "data", "monitor.db")) == tmp_path / "data" / "monitor.db"
