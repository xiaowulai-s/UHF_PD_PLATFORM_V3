# -*- coding: utf-8 -*-
"""
FPGA 通信协议解析器

解析 FPGA 采集主机通过 TCP/UDP 发送的数据帧。

数据帧格式:
┌────────┬──────┬────────┬────────┬──────────┬────────┬──────────┬──────┐
│ 帧头   │ 长度 │ 设备ID │ 通道ID │ 时间戳   │数据类型│ 数据内容 │ CRC  │
│ 2Byte  │ 2Byte│ 2Byte  │ 2Byte  │ 8Byte    │ 1Byte  │ N Byte   │ 2Byte│
└────────┴──────┴────────┴────────┴──────────┴────────┴──────────┴──────┘

数据类型:
0x01 = 波形数据 (WAVEFORM)
0x02 = 局放事件 (PD_EVENT)
0x03 = FFT 数据 (FFT_RESULT)
0x04 = PRPD 数据 (PRPD_RESULT)
0x05 = PRPS 数据 (PRPS_RESULT)
0x06 = 设备状态 (DEVICE_STATUS)
0x07 = AE 波形数据 (AE_WAVEFORM)
0x08 = AE 参数数据 (AE_PARAMETERS)
"""

from __future__ import annotations

import logging
import struct
import time
from enum import IntEnum
from typing import Any, Dict, List, Optional

from core.communication.protocol_data_structures import (
    AEParametersData,
    AEWaveformData,
    DataFrame,
    DeviceStatusData,
    FFTData,
    PRPDData,
    PRPSData,
    WaveformData,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# 常量定义
# ═══════════════════════════════════════════════════════

FRAME_HEADER = 0xA5A5  # 帧头标记
FRAME_HEADER_BYTES = b"\xA5\xA5"

DATA_TYPE_WAVEFORM = 0x01
DATA_TYPE_PD_EVENT = 0x02
DATA_TYPE_FFT_RESULT = 0x03
DATA_TYPE_PRPD_RESULT = 0x04
DATA_TYPE_PRPS_RESULT = 0x05
DATA_TYPE_DEVICE_STATUS = 0x06
DATA_TYPE_AE_WAVEFORM = 0x07
DATA_TYPE_AE_PARAMETERS = 0x08


class DataType(IntEnum):
    """数据类型枚举"""

    WAVEFORM = DATA_TYPE_WAVEFORM
    PD_EVENT = DATA_TYPE_PD_EVENT
    FFT_RESULT = DATA_TYPE_FFT_RESULT
    PRPD_RESULT = DATA_TYPE_PRPD_RESULT
    PRPS_RESULT = DATA_TYPE_PRPS_RESULT
    DEVICE_STATUS = DATA_TYPE_DEVICE_STATUS
    AE_WAVEFORM = DATA_TYPE_AE_WAVEFORM
    AE_PARAMETERS = DATA_TYPE_AE_PARAMETERS


DATA_TYPE_NAMES = {
    DATA_TYPE_WAVEFORM: "波形数据",
    DATA_TYPE_PD_EVENT: "局放事件",
    DATA_TYPE_FFT_RESULT: "FFT 频谱",
    DATA_TYPE_PRPD_RESULT: "PRPD 图谱",
    DATA_TYPE_PRPS_RESULT: "PRPS 图谱",
    DATA_TYPE_DEVICE_STATUS: "设备状态",
    DATA_TYPE_AE_WAVEFORM: "AE 波形数据",
    DATA_TYPE_AE_PARAMETERS: "AE 参数数据",
}

# 最小帧长度: 帧头(2) + 长度(2) + 设备ID(2) + 通道ID(2) + 时间戳(8) + 类型(1) + CRC(2) = 19
MIN_FRAME_LENGTH = 19

# 最大载荷长度: 1MB — 防止恶意/损坏帧导致内存耗尽
MAX_PAYLOAD_LENGTH = 1_048_576


# ═══════════════════════════════════════════════════════
# 协议解析器
# ═══════════════════════════════════════════════════════


class FpgaProtocol:
    """
    FPGA 通信协议解析器

    负责:
    - 数据帧解析
    - CRC 校验
    - 数据帧封装
    """

    def __init__(self):
        self._buffer = bytearray()
        self._stats = {
            "frames_received": 0,
            "crc_errors": 0,
            "parse_errors": 0,
            "bytes_received": 0,
        }

    # ── 解析 ─────────────────────────────────────────

    def feed(self, data: bytes) -> List[DataFrame]:
        """
        喂入原始字节数据，返回解析到的数据帧列表

        Args:
            data: 原始字节数据

        Returns:
            解析出的数据帧列表
        """
        self._buffer.extend(data)
        self._stats["bytes_received"] += len(data)

        frames: List[DataFrame] = []
        while True:
            frame = self._try_parse()
            if frame is None:
                break
            frames.append(frame)
            self._stats["frames_received"] += 1

        return frames

    def _try_parse(self) -> Optional[DataFrame]:
        """尝试从缓冲区解析一个完整帧"""
        buf = self._buffer
        if len(buf) < MIN_FRAME_LENGTH:
            return None

        # 寻找帧头
        header_idx = self._find_header(buf)
        if header_idx > 0:
            del buf[:header_idx]
            return None  # 重新尝试

        if header_idx < 0:
            # 未找到帧头，清空缓冲区（防止无效数据堆积）
            buf.clear()
            return None

        # 读取长度字段 (偏移 2, 大端 2 字节)
        payload_length = struct.unpack_from(">H", buf, 2)[0]

        # 长度安全检查: 拒绝超大 payload
        if payload_length > MAX_PAYLOAD_LENGTH:
            logger.warning("帧 payload 超限: %d bytes (上限 %d), 丢弃此帧", payload_length, MAX_PAYLOAD_LENGTH)
            self._stats["parse_errors"] += 1
            del buf[:2]
            return None

        total_length = MIN_FRAME_LENGTH + payload_length

        if len(buf) < total_length:
            return None  # 数据不足，等待更多

        try:
            frame = self._parse_frame(buf[:total_length])
            del buf[:total_length]
            return frame
        except (struct.error, ValueError) as e:
            logger.debug("帧解析失败 - 数据格式错误: %s", e)
            self._stats["parse_errors"] += 1
            del buf[:2]  # 跳过帧头后重试
            return None
        except Exception as e:
            logger.warning("帧解析失败 - 未知错误: %s", e, exc_info=True)
            self._stats["parse_errors"] += 1
            del buf[:2]
            return None

    def _find_header(self, buf: bytearray) -> int:
        """寻找帧头标记 A5A5"""
        for i in range(len(buf) - 1):
            if buf[i] == 0xA5 and buf[i + 1] == 0xA5:
                return i
        return -1

    def _parse_frame(self, frame_data: bytes) -> DataFrame:
        """解析完整帧数据"""
        offset = 0

        # 帧头 (2B)
        header = struct.unpack_from(">H", frame_data, offset)[0]
        offset += 2
        if header != FRAME_HEADER:
            raise ValueError(f"无效帧头: 0x{header:04X}")

        # 数据长度 (2B) — payload 长度
        payload_len = struct.unpack_from(">H", frame_data, offset)[0]
        offset += 2

        # 设备 ID (2B)
        device_id = struct.unpack_from(">H", frame_data, offset)[0]
        offset += 2

        # 通道 ID (2B)
        channel_id = struct.unpack_from(">H", frame_data, offset)[0]
        offset += 2

        # 时间戳 (8B) — Unix 微秒
        timestamp_us = struct.unpack_from(">Q", frame_data, offset)[0]
        offset += 8
        timestamp = timestamp_us / 1_000_000.0

        # 数据类型 (1B)
        data_type = struct.unpack_from("B", frame_data, offset)[0]
        offset += 1

        # 数据内容 (N 字节)
        data_payload = frame_data[offset : offset + payload_len]

        # CRC (2B) — data_payload 之后
        crc_received = struct.unpack_from(">H", frame_data, offset + payload_len)[0]
        crc_calculated = self._calculate_crc(frame_data[:-2])
        if crc_received != crc_calculated:
            self._stats["crc_errors"] += 1
            raise ValueError(f"CRC 校验失败: 收到 0x{crc_received:04X}, 计算 0x{crc_calculated:04X}")

        # 解析数据内容
        data = self._parse_payload(data_type, data_payload)

        return DataFrame(
            frame_type=DataType(data_type),
            device_id=device_id,
            channel_id=channel_id,
            timestamp=timestamp,
            data=data,
            raw_length=len(frame_data),
        )

    def _parse_payload(self, data_type: int, payload: bytes) -> Any:
        """根据数据类型解析 payload"""
        if data_type == DATA_TYPE_WAVEFORM:
            return self._parse_waveform(payload)
        elif data_type == DATA_TYPE_PD_EVENT:
            return self._parse_pd_event(payload)
        elif data_type == DATA_TYPE_FFT_RESULT:
            return self._parse_fft_result(payload)
        elif data_type == DATA_TYPE_PRPD_RESULT:
            return self._parse_prpd_result(payload)
        elif data_type == DATA_TYPE_PRPS_RESULT:
            return self._parse_prps_result(payload)
        elif data_type == DATA_TYPE_DEVICE_STATUS:
            return self._parse_device_status(payload)
        elif data_type == DATA_TYPE_AE_WAVEFORM:
            return self._parse_ae_waveform(payload)
        elif data_type == DATA_TYPE_AE_PARAMETERS:
            return self._parse_ae_parameters(payload)
        else:
            logger.debug("未知数据类型: 0x%02X, 长度=%d", data_type, len(payload))
            return payload  # 返回原始字节

    # ── 各数据类型解析 ──────────────────────────────

    def _parse_waveform(self, payload: bytes) -> WaveformData:
        """解析波形数据"""
        # 前 8 字节: 采样率 (4B) + 触发位置 (4B)
        sample_rate = struct.unpack_from(">I", payload, 0)[0]
        trigger_pos = struct.unpack_from(">I", payload, 4)[0]

        # 剩余: int16 采样点
        sample_bytes = payload[8:]
        n_samples = len(sample_bytes) // 2
        samples = list(struct.unpack_from(f">{n_samples}h", sample_bytes, 0)) if n_samples > 0 else []

        return WaveformData(
            samples=samples,
            sample_rate=sample_rate,
            trigger_position=trigger_pos,
        )

    def _parse_pd_event(self, payload: bytes) -> Dict[str, Any]:
        """解析局放事件"""
        # 固定 24 字节: 相位(4B float) + 幅值(4B float) + 能量(4B float) + 极性(2B) + 主频(4B float) + 质量(2B) + 保留(4B)
        if len(payload) < 24:
            return {"raw": payload.hex()}

        phase, amplitude, energy = struct.unpack_from(">fff", payload, 0)
        polarity = struct.unpack_from(">H", payload, 12)[0]
        frequency = struct.unpack_from(">f", payload, 14)[0]
        quality = struct.unpack_from(">H", payload, 18)[0]

        return {
            "phase": phase,
            "amplitude": amplitude,
            "energy": energy,
            "polarity": polarity,
            "frequency_mhz": frequency,
            "quality": quality,
            "timestamp": time.time(),
        }

    def _parse_fft_result(self, payload: bytes) -> FFTData:
        """解析 FFT 结果"""
        # 前 8 字节: 采样率 (4B) + 点数 (4B)
        sample_rate = struct.unpack_from(">I", payload, 0)[0]
        n_points = struct.unpack_from(">I", payload, 4)[0]

        # 之后: 频率 (4B float × n) + 幅值 (4B float × n)
        header_size = 8
        float_size = 4
        avail_points = (len(payload) - header_size) // (float_size * 2)
        n = min(n_points, avail_points)

        frequencies = list(struct.unpack_from(f">{n}f", payload, header_size))
        magnitudes = list(struct.unpack_from(f">{n}f", payload, header_size + n * float_size))

        return FFTData(
            frequencies=frequencies,
            magnitudes=magnitudes,
            sample_rate=sample_rate,
        )

    def _parse_prpd_result(self, payload: bytes) -> PRPDData:
        """解析 PRPD 结果"""
        # 前 8 字节: 相位bin数(2B) + 幅值bin数(2B) + 预览点数(4B)
        phase_bins = struct.unpack_from(">H", payload, 0)[0]
        amp_bins = struct.unpack_from(">H", payload, 2)[0]
        n_values = struct.unpack_from(">I", payload, 4)[0]

        expected = phase_bins * amp_bins
        n = min(n_values, expected)
        header_size = 8

        values = list(struct.unpack_from(f">{n}f", payload, header_size)) if n > 0 else []

        # 重建矩阵
        matrix = []
        for i in range(phase_bins):
            row = values[i * amp_bins : (i + 1) * amp_bins]
            matrix.append(row)

        return PRPDData(
            matrix=matrix,
            phase_bins=phase_bins,
            amplitude_bins=amp_bins,
        )

    def _parse_prps_result(self, payload: bytes) -> PRPSData:
        """解析 PRPS 结果"""
        rows = struct.unpack_from(">H", payload, 0)[0]
        cols = struct.unpack_from(">H", payload, 2)[0]
        n_values = struct.unpack_from(">I", payload, 4)[0]

        expected = rows * cols
        n = min(n_values, expected)
        header_size = 8

        values = list(struct.unpack_from(f">{n}f", payload, header_size)) if n > 0 else []

        matrix = []
        for i in range(rows):
            row = values[i * cols : (i + 1) * cols]
            matrix.append(row)

        return PRPSData(matrix=matrix, rows=rows, cols=cols)

    def _parse_device_status(self, payload: bytes) -> DeviceStatusData:
        """解析设备状态"""
        result = DeviceStatusData(status=0)

        if len(payload) >= 2:
            result.status = struct.unpack_from(">H", payload, 0)[0]
        if len(payload) >= 6:
            result.temperature = struct.unpack_from(">f", payload, 2)[0]
        if len(payload) >= 10:
            result.humidity = struct.unpack_from(">f", payload, 6)[0]
        if len(payload) >= 18:
            result.voltage = struct.unpack_from(">f", payload, 10)[0]
            result.current = struct.unpack_from(">f", payload, 14)[0]
        if len(payload) >= 22:
            result.adc_status = struct.unpack_from(">I", payload, 18)[0]
        if len(payload) >= 26:
            result.optical_status = struct.unpack_from(">I", payload, 22)[0]
        if len(payload) >= 30:
            result.sync_status = struct.unpack_from(">I", payload, 26)[0]
        if len(payload) >= 38:
            result.memory_usage = struct.unpack_from(">f", payload, 30)[0]
            result.cpu_usage = struct.unpack_from(">f", payload, 34)[0]
        if len(payload) >= 42:
            result.uptime = struct.unpack_from(">I", payload, 38)[0]
        if len(payload) >= 46:
            result.error_code = struct.unpack_from(">I", payload, 42)[0]

        return result

    def _parse_ae_waveform(self, payload: bytes) -> AEWaveformData:
        """解析 AE 波形数据"""
        # 前 8 字节: 采样率 (4B) + 触发位置 (4B)
        sample_rate = struct.unpack_from(">I", payload, 0)[0]
        trigger_pos = struct.unpack_from(">I", payload, 4)[0]

        # 剩余: int16 采样点
        sample_bytes = payload[8:]
        n_samples = len(sample_bytes) // 2
        samples = list(struct.unpack_from(f">{n_samples}h", sample_bytes, 0)) if n_samples > 0 else []

        return AEWaveformData(
            samples=samples,
            sample_rate=sample_rate,
            trigger_position=trigger_pos,
        )

    def _parse_ae_parameters(self, payload: bytes) -> AEParametersData:
        """解析 AE 参数数据"""
        # 固定 32 字节: 峰值幅值(4B) + 上升时间(4B) + 持续时间(4B) + 振铃计数(2B)
        #           + MARSE(4B) + RMS(4B) + 到达时间(4B) + 相位(4B) + 平均频率(2B)
        result = AEParametersData()
        if len(payload) >= 32:
            result.peak_amplitude = struct.unpack_from(">f", payload, 0)[0]
            result.rise_time_us = struct.unpack_from(">f", payload, 4)[0]
            result.duration_us = struct.unpack_from(">f", payload, 8)[0]
            result.counts = struct.unpack_from(">H", payload, 12)[0]
            result.marse_energy = struct.unpack_from(">f", payload, 14)[0]
            result.rms = struct.unpack_from(">f", payload, 18)[0]
            result.arrival_time = struct.unpack_from(">f", payload, 22)[0]
            result.phase_deg = struct.unpack_from(">f", payload, 26)[0]
            result.avg_frequency_khz = struct.unpack_from(">f", payload, 30)[0]
        return result

    # ── 封装 ─────────────────────────────────────────

    def build_frame(self, device_id: int, channel_id: int, data_type: int, payload: bytes) -> bytes:
        """
        构建发送帧

        Args:
            device_id: 设备 ID
            channel_id: 通道 ID
            data_type: 数据类型
            payload: 数据内容

        Returns:
            完整的帧字节流
        """
        timestamp_us = int(time.time() * 1_000_000)

        frame = bytearray()
        frame.extend(struct.pack(">H", FRAME_HEADER))
        # 长度字段 = payload 长度
        frame.extend(struct.pack(">H", len(payload)))
        frame.extend(struct.pack(">H", device_id))
        frame.extend(struct.pack(">H", channel_id))
        frame.extend(struct.pack(">Q", timestamp_us))
        frame.extend(struct.pack("B", data_type))
        frame.extend(payload)

        # CRC (不含 crc 字段自身)
        crc = self._calculate_crc(frame)
        frame.extend(struct.pack(">H", crc))

        return bytes(frame)

    # ── CRC ──────────────────────────────────────────

    @staticmethod
    def _calculate_crc(data: bytes) -> int:
        """
        CRC-16/MODBUS 校验

        Args:
            data: 待校验数据

        Returns:
            16 位 CRC 值
        """
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    # ── 统计 ─────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        """获取协议统计信息"""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """重置统计信息"""
        for k in self._stats:
            self._stats[k] = 0

    def __repr__(self) -> str:
        return (
            f"FpgaProtocol(frames={self._stats['frames_received']}, "
            f"crc_errors={self._stats['crc_errors']}, "
            f"bytes={self._stats['bytes_received']})"
        )
