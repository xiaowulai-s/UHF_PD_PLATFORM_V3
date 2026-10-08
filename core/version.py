__version__ = "1.0.8"

VERSION_INFO = {
    "major": 1,
    "minor": 0,
    "patch": 8,
    "releaselevel": "final",
    "serial": 0,
}


def get_version() -> str:
    return __version__


def get_version_info() -> dict:
    return dict(VERSION_INFO)
