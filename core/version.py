# 初代版本号（2026-10-08 定）。与 desktop/package.json 保持同一串，
# 否则装机包文件名与界面页脚会显示两个不同的版本。
__version__ = "0.0.1"

VERSION_INFO = {
    "major": 0,
    "minor": 0,
    "patch": 1,
    "releaselevel": "final",
    "serial": 0,
}


def get_version() -> str:
    return __version__


def get_version_info() -> dict:
    return dict(VERSION_INFO)
