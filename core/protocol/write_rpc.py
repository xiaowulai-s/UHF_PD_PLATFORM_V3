"""配置写入与设备 CRUD 的 RPC。

与只读那侧同样的约束：命名方法 + 参数字段白名单，前端不能塞进任意列名
（否则等于开放批量赋值，能把 created_at/status 改成任何值）。

设备落库走 background 线程；运行时配置（耦合类型、阈值）只是改内存中的服务状态，
留在主线程即可。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

COUPLING_TYPES = ("uhf", "hfct", "tev", "ae")

DEVICE_FIELDS = (
    "id",
    "name",
    "device_type",
    "host",
    "tcp_port",
    "udp_port",
    "firmware_version",
    "channel_count",
    "sample_rate_hz",
    "use_simulator",
)
CHANNEL_FIELDS = (
    "channel_index",
    "name",
    "enabled",
    "coupling_type",
    "sensor_type",
    "gain",
    "attenuation_db",
    "frequency_min_mhz",
    "frequency_max_mhz",
)


def _require(params: dict, key: str) -> Any:
    value = params.get(key)
    if value is None or value == "":
        raise ValueError(f"缺少必填参数: {key}")
    return value


def _pick(source: dict, allowed: tuple[str, ...]) -> dict:
    return {k: source[k] for k in allowed if k in source}


def _positive(value: Any, name: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是数字")
    if out <= 0:
        raise ValueError(f"{name} 必须为正数")
    return out


class WriteRpc:
    """把写操作映射到既有核心服务，不自己实现任何业务规则。"""

    def __init__(self, db_manager: Any = None, acquisition: Any = None, alarm: Any = None):
        self._db = db_manager
        self._acq = acquisition
        self._alarm = alarm

    def runtime_handlers(self) -> list[tuple[str, Callable[[dict], Any]]]:
        """内存态配置，主线程执行。"""
        out: list[tuple[str, Callable[[dict], Any]]] = []
        if self._acq is not None:
            out.append(("config.set_channel_coupling", self.set_channel_coupling))
            out.append(("config.set_ae_thresholds", self.set_ae_thresholds))
        if self._alarm is not None:
            out.append(("config.set_amplitude_threshold", self.set_amplitude_threshold))
            out.append(("config.set_channel_max_amplitude", self.set_channel_max_amplitude))
            out.append(("config.get_thresholds", lambda _p: self._alarm.get_thresholds()))
        return out

    def storage_handlers(self) -> list[tuple[str, Callable[[dict], Any]]]:
        """需要落库的，走后台线程。"""
        if self._db is None:
            return []
        return [("device.save", self.device_save), ("device.delete", self.device_delete)]

    # ── 运行时配置 ───────────────────────────────────

    def set_channel_coupling(self, params: dict) -> dict:
        device_id = str(_require(params, "device_id"))
        channel_id = int(_require(params, "channel_id"))
        coupling = str(_require(params, "coupling_type"))
        if coupling not in COUPLING_TYPES:
            raise ValueError(f"coupling_type 只能是 {COUPLING_TYPES} 之一，收到 {coupling!r}")
        self._acq.set_channel_coupling_type(device_id, channel_id, coupling)
        return {"device_id": device_id, "channel_id": channel_id, "coupling_type": coupling}

    def set_ae_thresholds(self, params: dict) -> dict:
        allowed = ("signal_loss_frames", "noise_threshold_mv", "noise_clear_mv", "noise_frame_count")
        kwargs = _pick(params, allowed)
        if not kwargs:
            raise ValueError("至少要提供一个 AE 阈值字段")
        for key, value in kwargs.items():
            if key.endswith("_mv"):
                kwargs[key] = float(value)
            else:
                kwargs[key] = int(value)
        self._acq.set_ae_alarm_thresholds(**kwargs)
        return kwargs

    def set_amplitude_threshold(self, params: dict) -> dict:
        """设置 PD 幅值量程。

        scope=global 改全局量程（设置页的那个字段就是全局项，且 config
        .get_thresholds 报告的也是全局值）；否则改指定设备的覆盖值。两者写入的
        不是同一份状态，混用会造成"写一处读另一处"。
        """
        max_amplitude = _positive(params.get("max_amplitude"), "max_amplitude")
        # 未显式给 scope 时按 device_id 推断：传了设备却静默改到全局，是最难查的那种错
        scope = str(params.get("scope") or ("device" if params.get("device_id") else "global"))
        if scope == "global":
            self._alarm.set_global_max_amplitude(max_amplitude)
            if "critical" in params:
                self._alarm.set_amplitude_thresholds(
                    critical=float(params["critical"]),
                    high=float(params["high"]),
                    medium=float(params["medium"]),
                    low=float(params["low"]),
                    max_amplitude=max_amplitude,
                )
            return {"scope": "global", "max_amplitude": max_amplitude}

        device_id = str(_require(params, "device_id"))
        self._alarm.set_amplitude_threshold(device_id, max_amplitude=max_amplitude)
        return {"scope": "device", "device_id": device_id, "max_amplitude": max_amplitude}

    def set_channel_max_amplitude(self, params: dict) -> dict:
        device_id = str(_require(params, "device_id"))
        channel_id = int(_require(params, "channel_id"))
        max_amplitude = _positive(params.get("max_amplitude"), "max_amplitude")
        self._alarm.set_channel_max_amplitude(device_id, channel_id, max_amplitude)
        return {"device_id": device_id, "channel_id": channel_id, "max_amplitude": max_amplitude}

    # ── 设备 CRUD ────────────────────────────────────

    def _repo(self):
        from core.data.repository.device_repository import PDDeviceRepository

        return PDDeviceRepository(self._db)

    def device_save(self, params: dict) -> dict:
        from core.data.monitor_models import PDChannelModel, PDDeviceModel

        body = params.get("device")
        if not isinstance(body, dict):
            raise ValueError("device 必须是对象")
        device_id = str(_require(body, "id"))
        _require(body, "name")

        channels = params.get("channels", [])
        if not isinstance(channels, list):
            raise ValueError("channels 必须是数组")
        seen: set[int] = set()
        models = []
        for entry in channels:
            if not isinstance(entry, dict):
                raise ValueError("channel 项必须是对象")
            index = int(_require(entry, "channel_index"))
            if index in seen:
                raise ValueError(f"channel_index 重复: {index}")
            seen.add(index)
            coupling = entry.get("coupling_type", "uhf")
            if coupling not in COUPLING_TYPES:
                raise ValueError(f"coupling_type 非法: {coupling!r}")
            models.append(PDChannelModel(device_id=device_id, **_pick(entry, CHANNEL_FIELDS)))

        device = PDDeviceModel(**_pick(body, DEVICE_FIELDS))
        device.channels = models
        saved = self._repo().save_device(device)
        return {"id": saved.id, "channel_count": len(models)}

    def device_delete(self, params: dict) -> dict:
        device_id = str(_require(params, "device_id"))
        return {"deleted": self._repo().delete_device(device_id), "device_id": device_id}


def register_write_rpc(
    server: Any,
    *,
    db_manager: Any = None,
    acquisition: Any = None,
    alarm: Any = None,
) -> list[str]:
    rpc = WriteRpc(db_manager=db_manager, acquisition=acquisition, alarm=alarm)
    registered = []
    for name, handler in rpc.runtime_handlers():
        server.register_rpc(name, handler)
        registered.append(name)
    for name, handler in rpc.storage_handlers():
        server.register_rpc(name, handler, background=True)
        registered.append(name)
    logger.info("写入 RPC 已注册: %s", ", ".join(registered) or "<无>")
    return registered
