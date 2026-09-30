"""把 PDDataBus 的 object 负载归一化成协议契约字段。

signal_bus.py 里重负载信号全部声明为 Signal(..., object)，实际携带的是
WaveformData / FFTResult / PRPDResult 等鸭子类型对象。本模块是这种无声明
契约的唯一收敛点：字段名以前端可依赖的形式固定下来。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .frames import Frame, pack_array

# PRPD 结果携带变长 events 列表，突发时可达数十万条；超限即截断并置标记。
MAX_PRPD_EVENTS = 2000

_WAVEFORM_DTYPE = np.float32


def coerce_array(obj: Any) -> np.ndarray:
    """从 ndarray / WaveformData / 序列中提取样本数组。"""
    samples = getattr(obj, "samples", None)
    if samples is not None:
        return np.asarray(samples)
    return np.asarray(obj)


def build_waveform(device: str, channel: int, sensor_type: str, obj: Any) -> Frame | None:
    data = coerce_array(obj)
    if data.size < 2:
        return None
    payload, meta = pack_array(data, _WAVEFORM_DTYPE)
    meta["sensor_type"] = sensor_type
    meta["sample_rate"] = int(getattr(obj, "sample_rate", 0) or 0)
    return Frame(topic="waveform", device=device, channel=channel, meta=meta, payload=payload)


def build_fft(device: str, channel: int, sensor_type: str, result: Any) -> Frame | None:
    freqs = np.asarray(getattr(result, "frequencies", []))
    mags = np.asarray(getattr(result, "magnitudes", []))
    if freqs.size < 2 or mags.size < 2:
        return None

    payload, meta = pack_array(np.column_stack([freqs, mags]), _WAVEFORM_DTYPE)
    meta["sensor_type"] = sensor_type
    meta["peaks"] = [
        {
            "frequency": float(p.frequency),
            "magnitude": float(p.magnitude),
            "is_harmonic": bool(p.is_harmonic),
            "harmonic_order": int(p.harmonic_order),
        }
        for p in (getattr(result, "peaks", None) or [])
    ]
    meta["noise_floor"] = _float(getattr(result, "noise_floor", 0.0))
    meta["snr_db"] = _float(getattr(result, "snr_db", 0.0))
    # 频段带：fft_processor 每帧都算了 band_stats，但协议层原先把它丢了。
    #
    # BandStats 自己的单位是不一致的：f_min/f_max 在 compute_band_stats 里除了 1e6
    # 成了 MHz，peak_frequency 却还是 Hz（旧 Qt 控件只读前者所以没暴露）。协议里
    # 频率一律统一到 Hz——与负载 x 列和 peaks.frequency 同量纲，前端不需要按字段猜单位。
    meta["bands"] = [
        {
            "name": str(b.band_name),
            "f_min_hz": _float(b.f_min) * 1e6,
            "f_max_hz": _float(b.f_max) * 1e6,
            "energy": _float(b.energy),
            "peak_magnitude": _float(b.peak_magnitude),
            "peak_frequency_hz": _float(b.peak_frequency),
            "avg_magnitude": _float(b.avg_magnitude),
        }
        for b in (getattr(result, "band_stats", None) or {}).values()
    ]
    return Frame(topic="fft", device=device, channel=channel, meta=meta, payload=payload)


def build_prpd(device: str, channel: int, result: Any) -> Frame | None:
    matrix = np.asarray(getattr(result, "matrix", []))
    if matrix.size == 0:
        return None

    # 热图矩阵按"精度低于显示分辨率"处理：量化到 uint8 再 zlib。
    # 计数模式 360KB -> 3~35KB；密度模式（scipy 高斯平滑后）float32+zlib 会退化到
    # 322KB，因为平滑让全矩阵非零且值连续，zlib 无从下手——uint8 是唯一稳的档位。
    peak = float(np.nanmax(matrix)) if matrix.size else 0.0
    if peak <= 0:
        peak = 1.0
    payload, meta = pack_array(matrix, np.uint8, compress=True, quantize_max=peak)
    meta["max_amplitude"] = _float(getattr(result, "max_amplitude", 0.0))
    meta["min_amplitude"] = _float(getattr(result, "min_amplitude", 0.0))
    # 矩阵行的实际分箱轴范围，与上面的 max_amplitude 是**两个不同的量**：
    # max_amplitude 是"本窗口观测到的最大事件幅值"(24.7)，而矩阵是按处理器的
    # 量程轴分箱的 (100.0)。客户端若拿观测值去铺矩阵，会把数据带纵向压到
    # 观测值/量程轴 的比例，实测电晕事件带因此贴死在底部。
    amp_axis = np.asarray(getattr(result, "amplitude_bins", []), dtype=np.float64)
    if amp_axis.size:
        meta["amp_axis_min"] = _float(amp_axis[0])
        meta["amp_axis_max"] = _float(amp_axis[-1])
    phase_axis = np.asarray(getattr(result, "phase_bins", []), dtype=np.float64)
    if phase_axis.size:
        meta["phase_axis_max"] = _float(phase_axis[-1])
    meta["total_events"] = int(getattr(result, "total_events", 0) or 0)
    # 矩阵是按哪种模式算的要随帧带上：密度与热力的区别在内核 compute 里，
    # 前端只有拿到这个才能确认"我按下的开关真的生效了"，而不是自己猜。
    mode = getattr(result, "mode", None)
    meta["mode"] = getattr(mode, "value", None) or (str(mode) if mode else "")

    events = getattr(result, "events", None) or []
    if len(events) > MAX_PRPD_EVENTS:
        meta["events_truncated"] = len(events)
        events = events[:MAX_PRPD_EVENTS]
    meta["events"] = [
        {
            "phase": _float(getattr(e, "phase", 0.0)),
            "amplitude": _float(getattr(e, "amplitude", 0.0)),
            "polarity": int(getattr(e, "polarity", 0) or 0),
            "energy": _float(getattr(e, "energy", 0.0)),
            "cycle": int(getattr(e, "cycle", 0) or 0),
        }
        for e in events
    ]
    return Frame(topic="prpd", device=device, channel=channel, meta=meta, payload=payload)


def build_prps(device: str, channel: int, result: Any) -> Frame | None:
    matrix = np.asarray(getattr(result, "matrix", []))
    if matrix.size == 0:
        return None
    # 512x360 的 float32 原样是 720KB，而 PRPS 发布频率约 2Hz，独占过 69% 边界带宽。
    # 量化到 uint8 后 zlib 压到约 19KB（38x），还原误差最大 0.32mV/均值 0.019mV，
    # 远低于热图可分辨粒度。qmax 用矩阵自身峰值，客户端据此反量化。
    peak = float(np.nanmax(matrix)) if matrix.size else 0.0
    if peak <= 0:
        peak = 1.0
    payload, meta = pack_array(matrix, np.uint8, compress=True, quantize_max=peak)
    meta["total_cycles"] = int(getattr(result, "total_cycles", 0) or 0)
    return Frame(topic="prps", device=device, channel=channel, meta=meta, payload=payload)


def build_ae_envelope(device: str, channel: int, obj: Any) -> Frame | None:
    data = coerce_array(obj)
    if data.size < 2:
        return None
    payload, meta = pack_array(data, _WAVEFORM_DTYPE)
    # 与 build_waveform 同一套元信息：没有时间基准的包络在前端只能靠猜，
    # 而猜错是静默的（横轴照样画得出来，只是单位不对）。
    meta["sensor_type"] = "ae"
    meta["sample_rate"] = int(getattr(obj, "sample_rate", 0) or 0)
    return Frame(topic="ae_envelope", device=device, channel=channel, meta=meta, payload=payload)


def build_ae_hit(device: str, channel: int, hit: Any) -> Frame:
    return Frame(
        topic="ae_hit",
        device=device,
        channel=channel,
        meta={"hit": _plain(hit)},
    )


def build_alarm(device: str, alarm: Any) -> Frame:
    return Frame(
        topic="alarm",
        kind="event",
        device=device,
        channel=int(_float(_get(alarm, "channel_id", -1))),
        meta={"alarm": _plain(alarm)},
    )


def build_alarm_cleared(device: str, alarm_type: str, description: str) -> Frame:
    return Frame(
        topic="alarm_cleared",
        kind="event",
        device=device,
        meta={"alarm_type": alarm_type, "description": description},
    )


def build_device_status(device: str, status: int, status_data: Any) -> Frame:
    return Frame(
        topic="device_status",
        kind="event",
        device=device,
        meta={"status": int(status), "data": _plain(status_data)},
    )


def build_acquisition(state: str, device: str, error: str = "") -> Frame:
    return Frame(
        topic="acquisition",
        kind="event",
        device=device,
        meta={"state": state, "error": error},
    )


def build_source(state: str, source_id: str) -> Frame:
    return Frame(topic="source", kind="event", meta={"state": state, "source_id": source_id})


def build_stats(device: str, stats: Any) -> Frame:
    return Frame(topic="stats", device=device, meta={"stats": _plain(stats)})


def build_log(lines: list[dict[str, Any]]) -> Frame:
    """把一段时间窗内的日志行打成一批。"""
    return Frame(topic="log", meta={"lines": _plain(lines), "count": len(lines)})


def _get(obj: Any, key: str, default: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if np.isnan(out) else out


def _plain(value: Any) -> Any:
    """递归转成 msgpack 可序列化的基础类型。"""
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
            return None
        return value
    if isinstance(value, np.generic):
        return _plain(value.item())
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    if hasattr(value, "__dict__"):
        return {k: _plain(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)
