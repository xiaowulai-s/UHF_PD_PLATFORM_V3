"""Electron 前端与 Python 处理内核之间的跨进程边界。"""

from __future__ import annotations

from .frames import PROTOCOL_VERSION, TOPICS, Frame, Qos, RpcRequest, encode_result, pack_array, qos_of, unpack_array
from .sidecar_server import CTRL_PATH, STREAM_PATH, SidecarServer

__all__ = [
    "CTRL_PATH",
    "PROTOCOL_VERSION",
    "STREAM_PATH",
    "TOPICS",
    "Frame",
    "Qos",
    "RpcRequest",
    "SidecarServer",
    "encode_result",
    "pack_array",
    "qos_of",
    "unpack_array",
]
