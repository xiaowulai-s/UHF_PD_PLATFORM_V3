"""跨进程协议帧定义与 msgpack 编解码。

Electron 前端与 Python 处理内核之间的唯一契约来源。前端侧的镜像类型定义见
同目录 frames.ts，两者必须同步修改。
"""

from __future__ import annotations

import time
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

import msgpack
import numpy as np

PROTOCOL_VERSION = 1

MAX_MESSAGE_BYTES = 16 * 1024 * 1024

# 实测热图数据上 L6 与 L9 体积接近（5.5KB vs 4.4KB）但快约 5 倍，取 6
ZLIB_LEVEL = 6

_DTYPE_CODES: dict[np.dtype, str] = {
    np.dtype(np.float32): "f4",
    np.dtype(np.float64): "f8",
    np.dtype(np.int32): "i4",
    np.dtype(np.int16): "i2",
    np.dtype(np.int8): "i1",
    np.dtype(np.uint8): "u1",
}

_CODE_DTYPES: dict[str, np.dtype] = {code: dt for dt, code in _DTYPE_CODES.items()}


class Qos(str, Enum):
    """帧的投递等级。"""

    LOSSY = "lossy"
    RELIABLE = "reliable"


# topic -> Qos。lossy 主题在传输层按 (topic, device, channel) 合并为最新帧。
TOPICS: dict[str, Qos] = {
    "waveform": Qos.LOSSY,
    "fft": Qos.LOSSY,
    "prpd": Qos.LOSSY,
    "prps": Qos.LOSSY,
    "ae_envelope": Qos.LOSSY,
    "ae_hit": Qos.LOSSY,
    "stats": Qos.LOSSY,
    # 日志刻意做成"有损但成批"：单帧携带一个时间窗内的日志行。客户端落后时
    # 丢掉的是最旧的一批，而不是零散日志行；若设成 reliable，DEBUG 刷屏会积压
    # 无损通道并把告警一起拖慢。
    "log": Qos.LOSSY,
    "alarm": Qos.RELIABLE,
    "alarm_cleared": Qos.RELIABLE,
    "device_status": Qos.RELIABLE,
    "acquisition": Qos.RELIABLE,
    "source": Qos.RELIABLE,
    # 放电类型标签：频率低（跟随 PRPD 发布节奏，单通道约 0.33Hz）但语义是状态变更，
    # 丢一帧会让前端长时间停在旧类型上，所以走无损。
    "discharge_type": Qos.RELIABLE,
}


def qos_of(topic: str) -> Qos:
    return TOPICS.get(topic, Qos.RELIABLE)


@dataclass
class Frame:
    """一帧遥测或事件。kind 为 "tele"（遥测）或 "event"（不可丢事件）。"""

    topic: str
    kind: str = "tele"
    device: str = ""
    channel: int = -1
    seq: int = 0
    ts: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)
    payload: bytes | None = None

    def key(self) -> tuple[str, str, int]:
        return (self.topic, self.device, self.channel)

    def to_bytes(self) -> bytes:
        body: dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "k": self.kind,
            "t": self.topic,
            "d": self.device,
            "c": self.channel,
            "q": self.seq,
            "ts": self.ts,
            "m": self.meta,
        }
        if self.payload is not None:
            body["p"] = self.payload
        return msgpack.packb(body, use_bin_type=True)

    @classmethod
    def from_bytes(cls, raw: bytes | bytearray) -> Frame:
        body = msgpack.unpackb(raw, raw=False, strict_map_key=False)
        payload = body.get("p")
        return cls(
            topic=body["t"],
            kind=body.get("k", "tele"),
            device=body.get("d", ""),
            channel=body.get("c", -1),
            seq=body.get("q", 0),
            ts=body.get("ts", 0.0),
            meta=body.get("m") or {},
            payload=payload,
        )


def pack_array(
    arr: Any,
    dtype: type[np.generic] = np.float32,
    *,
    compress: bool = False,
    quantize_max: float | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """把 ndarray 打包成小端二进制 + 描述元信息。

    数值负载走二进制而非 JSON：避开浮点十进制的精度与体积开销，且 NaN/Inf 可无损表达。

    quantize_max 给定时按 value/quantize_max*dtype_max 线性量化到整数 dtype，
    用于热图这类"精度要求低于显示分辨率"的大矩阵——PRPS 实测 720KB -> 19KB。
    """
    a = np.asarray(arr)
    meta: dict[str, Any] = {"endian": "le"}

    if quantize_max is not None and np.issubdtype(np.dtype(dtype), np.integer):
        info = np.iinfo(np.dtype(dtype))
        scale = info.max / float(quantize_max) if quantize_max else 0.0
        q = np.clip(np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0) * scale, 0, info.max)
        # 必须先 rint 再转整：astype 直接向零截断，会让误差从半个步长膨胀到一整个步长。
        a = np.ascontiguousarray(np.rint(q), dtype=dtype)
        meta["qmax"] = float(quantize_max)
    else:
        a = np.ascontiguousarray(a, dtype=dtype)

    code = _DTYPE_CODES.get(a.dtype)
    if code is None:
        raise ValueError(f"不支持的数组 dtype: {a.dtype}")

    payload = a.tobytes(order="C")
    if compress:
        payload = zlib.compress(payload, ZLIB_LEVEL)
        meta["codec"] = "zlib"

    meta["shape"] = list(a.shape)
    meta["dtype"] = code
    return payload, meta


def unpack_array(payload: bytes | None, meta: dict[str, Any]) -> np.ndarray:
    """还原数组，返回 float64（量化过的会反量化回原始量纲）。"""
    if payload is None:
        return np.zeros(0, dtype=np.float32)

    raw = zlib.decompress(payload) if meta.get("codec") == "zlib" else payload
    dtype = _CODE_DTYPES.get(meta.get("dtype", "f4"))
    if dtype is None:
        raise ValueError(f"未知数组编码: {meta.get('dtype')}")

    a = np.frombuffer(raw, dtype=dtype)
    shape = meta.get("shape")
    if shape:
        a = a.reshape(shape)

    qmax = meta.get("qmax")
    if qmax is not None:
        return a.astype(np.float64) / float(np.iinfo(dtype).max) * float(qmax)
    return a


@dataclass
class RpcRequest:
    """客户端 -> 服务端的调用请求。"""

    id: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_body(cls, body: dict[str, Any]) -> RpcRequest:
        params = body.get("p")
        return cls(id=str(body.get("i", "")), method=body["m"], params=params if isinstance(params, dict) else {})


def encode_result(req_id: str, ok: bool, data: Any = None, error: str = "") -> bytes:
    body: dict[str, Any] = {"v": PROTOCOL_VERSION, "k": "result", "i": req_id, "ok": ok}
    if ok:
        body["d"] = data
    else:
        body["e"] = error
    return msgpack.packb(body, use_bin_type=True, default=_json_default)


def encode_error(message: str, req_id: str = "") -> bytes:
    return encode_result(req_id, False, error=message)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, bytes):
        return list(obj)
    raise TypeError(f"无法序列化: {type(obj).__name__}")


def iter_rows(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """把 sqlite3.Row / dict 序列化成 msgpack 可接受的普通字典列表。"""
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        else:
            out.append(dict(zip(row.keys(), tuple(row))))
    return out
