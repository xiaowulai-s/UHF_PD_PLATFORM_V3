# -*- coding: utf-8 -*-
"""
AE 声源定位器 — TDOA 双曲线定位

使用 3+ 传感器阵列的到达时间差 (TDOA) 计算声源位置。
适用于平面 (2D) 定位，例如变压器箱体表面。

算法:
    1. 选第一个传感器为参考，计算各传感器与参考的到达时间差 (TDOA)
    2. 将 TDOA 转换为距离差: d_i = v_sound * tdoa_i
    3. 构建双曲线方程组，用最小二乘法求解

参考: Chan & Ho (1994) 改进算法
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 声速 (m/s) — 空气中 20°C
SOUND_SPEED_AIR = 343.0
# 声速 (m/s) — 变压器油中 20°C
SOUND_SPEED_OIL = 1420.0
# 声速 (m/s) — SF6 气体中 20°C
SOUND_SPEED_SF6 = 135.0


@dataclass
class SensorPosition:
    """传感器位置"""

    sensor_id: int
    x: float  # X 坐标 (m)
    y: float  # Y 坐标 (m)


@dataclass
class LocalizationResult:
    """定位结果"""

    x: float  # 声源 X 坐标 (m)
    y: float  # 声源 Y 坐标 (m)
    residual: float  # 定位残差 (越小越准确)
    confidence: float  # 置信度 0-1
    sensors_used: int  # 参与计算的传感器数


class AELocalizer:
    """
    AE 声源定位器

    基于到达时间差 (TDOA) 的平面定位。

    Args:
        sensors: 传感器位置列表 (至少 3 个)
        sound_speed: 介质声速 (m/s), 默认空气中 343 m/s
    """

    def __init__(
        self,
        sensors: List[SensorPosition],
        sound_speed: float = SOUND_SPEED_AIR,
    ):
        if len(sensors) < 3:
            raise ValueError(f"至少需要 3 个传感器，当前 {len(sensors)}")
        self._sensors = {s.sensor_id: s for s in sensors}
        self._sound_speed = sound_speed
        self._sensor_list = sorted(sensors, key=lambda s: s.sensor_id)

    @property
    def sensor_ids(self) -> List[int]:
        return [s.sensor_id for s in self._sensor_list]

    @property
    def sensor_positions(self) -> List[Tuple[float, float]]:
        return [(s.x, s.y) for s in self._sensor_list]

    def set_sound_speed_by_medium(self, medium: str) -> None:
        """
        按介质设置声速

        Args:
            medium: "air", "oil", "sf6"
        """
        mapping = {
            "air": SOUND_SPEED_AIR,
            "oil": SOUND_SPEED_OIL,
            "sf6": SOUND_SPEED_SF6,
        }
        self._sound_speed = mapping.get(medium, SOUND_SPEED_AIR)

    def locate(self, arrival_times: Dict[int, float]) -> Optional[LocalizationResult]:
        """
        计算声源位置 (Chan-Ho 最小二乘法)

        Args:
            arrival_times: {sensor_id: 到达时间 (秒)}

        Returns:
            LocalizationResult 或 None (无法定位)
        """
        if len(arrival_times) < 3:
            logger.warning("TDOA 定位需要至少 3 个到达时间")
            return None

        # 按传感器列表排序到达时间
        times = []
        positions = []
        for s in self._sensor_list:
            if s.sensor_id in arrival_times:
                times.append(arrival_times[s.sensor_id])
                positions.append((s.x, s.y))
            else:
                logger.debug("传感器 %d 无到达时间数据", s.sensor_id)
                return None

        n = len(times)
        if n < 3:
            return None

        positions = np.array(positions, dtype=np.float64)
        times = np.array(times, dtype=np.float64)

        # 选第一个为参考
        t0 = times[0]
        p0 = positions[0]

        # TDOA → 距离差
        dt = times[1:] - t0
        d = dt * self._sound_speed

        # 构建矩阵 A 和 b: A * [x, y, r0]^T = b
        # 其中 r0 是声源到参考传感器的距离
        A = np.zeros((n - 1, 3))
        b = np.zeros(n - 1)

        for i in range(n - 1):
            pi = positions[i + 1]
            A[i, 0] = 2 * (pi[0] - p0[0])
            A[i, 1] = 2 * (pi[1] - p0[1])
            A[i, 2] = -2 * d[i]
            b[i] = d[i] ** 2 - (pi[0] ** 2 + pi[1] ** 2) + (p0[0] ** 2 + p0[1] ** 2)

        try:
            # 最小二乘解
            x, residuals, rank, s = np.linalg.lstsq(A, b, rcond=None)
            # 秩不足检测: rank < 2 意味着传感器高度共线 (只有1个独立方向约束)
            if rank < 2:
                logger.warning("TDOA 矩阵秩严重不足 (rank=%d), 传感器可能高度共线", rank)
                return None
            # 残差过大检测: 解的发散保护
            if len(residuals) > 0 and residuals[0] > 1e6:
                logger.warning("TDOA 残差过大 (%f), 定位发散", float(residuals[0]))
                return None
        except np.linalg.LinAlgError:
            logger.warning("TDOA 矩阵求解失败")
            return None

        source_x, source_y = float(x[0]), float(x[1])

        # 解决符号歧义: TDOA 系统产生两个解, 选距离传感器质心更近的那个
        centroid = np.mean(positions, axis=0)
        dist_to_centroid = np.sqrt((source_x - centroid[0]) ** 2 + (source_y - centroid[1]) ** 2)
        # 镜像解
        mirror_x = p0[0] - (source_x - p0[0])
        mirror_y = p0[1] - (source_y - p0[1])
        dist_to_centroid_mirror = np.sqrt((mirror_x - centroid[0]) ** 2 + (mirror_y - centroid[1]) ** 2)
        if dist_to_centroid_mirror < dist_to_centroid:
            source_x, source_y = mirror_x, mirror_y

        # 残差计算
        residual = float(np.sqrt(np.mean((A @ x - b) ** 2))) if len(b) > 0 else 0.0

        # 置信度估计 (基于残差和传感器数量)
        confidence = self._estimate_confidence(residual, n)

        return LocalizationResult(
            x=source_x,
            y=source_y,
            residual=residual,
            confidence=confidence,
            sensors_used=n,
        )

    def _estimate_confidence(self, residual: float, n_sensors: int) -> float:
        """
        估计定位置信度

        综合考虑:
        - 残差越小，置信度越高
        - 传感器越多，置信度越高
        """
        # 残差因子: 残差 < 0.01m 为 1.0, > 1m 趋于 0
        res_factor = np.exp(-residual * 5)

        # 传感器数量因子
        sensor_factor = min(1.0, (n_sensors - 2) / 4.0)

        confidence = res_factor * sensor_factor
        return float(min(1.0, max(0.0, confidence)))

    def locate_multiple(self, arrivals_batch: List[Dict[int, float]]) -> List[LocalizationResult]:
        """
        批量定位（对多组到达时间分别计算）

        Args:
            arrivals_batch: 到达时间字典列表

        Returns:
            LocalizationResult 列表
        """
        results = []
        for arrivals in arrivals_batch:
            result = self.locate(arrivals)
            if result is not None:
                results.append(result)
        return results

    def get_cluster_center(self, results: List[LocalizationResult]) -> Optional[LocalizationResult]:
        """
        计算多个定位结果的聚类中心（加权平均）

        Args:
            results: 定位结果列表

        Returns:
            加权平均后的定位结果
        """
        if not results:
            return None

        valid = [r for r in results if r.confidence > 0.3]
        if not valid:
            return None

        weights = np.array([r.confidence for r in valid])
        weights /= weights.sum()

        cx = float(np.average([r.x for r in valid], weights=weights))
        cy = float(np.average([r.y for r in valid], weights=weights))
        avg_residual = float(np.average([r.residual for r in valid], weights=weights))
        avg_confidence = float(np.mean([r.confidence for r in valid]))

        return LocalizationResult(
            x=cx,
            y=cy,
            residual=avg_residual,
            confidence=avg_confidence,
            sensors_used=max(r.sensors_used for r in valid),
        )

    def simulate_arrival_times(
        self,
        source_x: float,
        source_y: float,
        noise_std: float = 1e-7,
    ) -> Dict[int, float]:
        """
        模拟声源到达各传感器的时间（用于演示/测试）

        Args:
            source_x: 声源 X 坐标 (m)
            source_y: 声源 Y 坐标 (m)
            noise_std: 时间噪声标准差 (秒)

        Returns:
            {sensor_id: 到达时间 (秒)}
        """
        arrival_times: Dict[int, float] = {}
        for s in self._sensor_list:
            dist = float(np.sqrt((source_x - s.x) ** 2 + (source_y - s.y) ** 2))
            t_arrival = dist / self._sound_speed
            if noise_std > 0:
                t_arrival += float(np.random.normal(0, noise_std))
            arrival_times[s.sensor_id] = t_arrival
        return arrival_times

    @property
    def sound_speed(self) -> float:
        """当前声速 (m/s)"""
        return self._sound_speed
