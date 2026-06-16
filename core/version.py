__version__ = "1.0.7"

VERSION_INFO = {
    "major": 1,
    "minor": 1,
    "patch": 0,
    "releaselevel": "final",
    "serial": 0,
}


def get_version() -> str:
    return __version__


def get_version_info() -> dict:
    return dict(VERSION_INFO)
