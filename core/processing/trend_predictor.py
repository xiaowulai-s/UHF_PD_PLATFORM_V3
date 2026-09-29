# -*- coding: utf-8 -*-
"""
趋势预测分析模块

基于历史分类结果进行趋势预测:
- 简单移动平均 (SMA)
- 线性回归预测
- 异常趋势检测
- 预测结果可视化支持
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TrendPrediction:
    """趋势预测结果"""

    predicted_values: List[Tuple[float, float]]  # [(timestamp, value), ...]
    confidence_upper: List[Tuple[float, float]]  # 置信区间上限
    confidence_lower: List[Tuple[float, float]]  # 置信区间下限
    trend_direction: str  # "上升", "下降", "稳定", "波动"
    anomaly_detected: bool  # 是否检测到异常趋势
    anomaly_score: float  # 异常评分 (0-1)
    message: str  # 预测描述


class TrendPredictor:
    """
    趋势预测器

    提供基于历史数据的趋势预测功能:
    - 移动平均平滑
    - 线性回归外推
    - 异常检测
    """

    def __init__(
        self,
        window_size: int = 10,
        prediction_steps: int = 5,
        anomaly_threshold: float = 2.0,
        confidence_level: float = 0.95,
    ):
        """
        初始化趋势预测器

        Args:
            window_size: 移动平均窗口大小
            prediction_steps: 预测步数
            anomaly_threshold: 异常检测阈值 (标准差倍数)
            confidence_level: 置信区间置信水平
        """
        self._window_size = window_size
        self._prediction_steps = prediction_steps
        self._anomaly_threshold = anomaly_threshold
        self._confidence_level = confidence_level

    def predict(
        self,
        historical_data: List[Tuple[float, float]],
        timestamp_interval: float = 60.0,
    ) -> Optional[TrendPrediction]:
        """
        执行趋势预测

        Args:
            historical_data: 历史数据 [(timestamp, value), ...]
            timestamp_interval: 时间戳间隔 (秒)

        Returns:
            TrendPrediction 或 None (数据不足时)
        """
        if len(historical_data) < self._window_size:
            logger.warning("历史数据不足，至少需要 %d 个数据点", self._window_size)
            return None

        try:
            # 提取时间序列
            timestamps = [t for t, _ in historical_data]
            values = [v for _, v in historical_data]

            # 1. 移动平均平滑
            smoothed_values = self._moving_average(values)

            # 2. 线性回归预测
            predicted_values, confidence_upper, confidence_lower = self._linear_regression_predict(
                timestamps, smoothed_values, timestamp_interval
            )

            # 3. 判断趋势方向
            trend_direction = self._determine_trend_direction(smoothed_values)

            # 4. 异常检测
            anomaly_detected, anomaly_score, anomaly_msg = self._detect_anomaly(values, smoothed_values)

            # 5. 生成描述消息
            message = self._generate_message(trend_direction, anomaly_detected, anomaly_score, len(historical_data))
            if anomaly_msg:
                message += f"\n{anomaly_msg}"

            return TrendPrediction(
                predicted_values=predicted_values,
                confidence_upper=confidence_upper,
                confidence_lower=confidence_lower,
                trend_direction=trend_direction,
                anomaly_detected=anomaly_detected,
                anomaly_score=anomaly_score,
                message=message,
            )

        except (ValueError, TypeError, ZeroDivisionError, OverflowError, ArithmeticError) as e:
            logger.error("趋势预测失败: %s", e, exc_info=True)
            return None

    def _moving_average(self, values: List[float]) -> List[float]:
        """
        计算简单移动平均

        Args:
            values: 原始值序列

        Returns:
            平滑后的值序列
        """
        smoothed = []
        for i in range(len(values)):
            start = max(0, i - self._window_size + 1)
            window = values[start : i + 1]
            smoothed.append(sum(window) / len(window))
        return smoothed

    def _linear_regression_predict(
        self,
        timestamps: List[float],
        values: List[float],
        timestamp_interval: float,
    ) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[Tuple[float, float]]]:
        """
        线性回归预测

        Args:
            timestamps: 时间戳列表
            values: 值列表
            timestamp_interval: 预测间隔

        Returns:
            (预测值列表, 置信区间上限, 置信区间下限)
        """
        n = len(timestamps)

        # 计算线性回归系数 (y = a + b*x)
        x_mean = sum(timestamps) / n
        y_mean = sum(values) / n

        numerator = sum((t - x_mean) * (v - y_mean) for t, v in zip(timestamps, values))
        denominator = sum((t - x_mean) ** 2 for t in timestamps)

        if denominator == 0:
            # 所有时间戳相同，返回常数值
            last_ts = timestamps[-1]
            last_val = values[-1]
            predictions = [(last_ts + (i + 1) * timestamp_interval, last_val) for i in range(self._prediction_steps)]
            return predictions, predictions, predictions

        b = numerator / denominator
        a = y_mean - b * x_mean

        # 计算残差标准差
        residuals = [values[i] - (a + b * timestamps[i]) for i in range(n)]
        residual_std = math.sqrt(sum(r**2 for r in residuals) / (n - 2))

        # 预测未来值
        last_ts = timestamps[-1]
        predictions = []
        confidence_upper = []
        confidence_lower = []

        for i in range(1, self._prediction_steps + 1):
            future_ts = last_ts + i * timestamp_interval
            predicted_val = a + b * future_ts

            # 简化的置信区间 (使用残差标准差)
            z_score = self._get_z_score(self._confidence_level)
            margin = z_score * residual_std

            predictions.append((future_ts, predicted_val))
            confidence_upper.append((future_ts, predicted_val + margin))
            confidence_lower.append((future_ts, predicted_val - margin))

        return predictions, confidence_upper, confidence_lower

    def _get_z_score(self, confidence_level: float) -> float:
        """
        获取 Z 分数

        Args:
            confidence_level: 置信水平 (0-1)

        Returns:
            Z 分数
        """
        # 常用置信水平对应的 Z 分数
        z_scores = {
            0.90: 1.645,
            0.95: 1.96,
            0.99: 2.576,
        }
        return z_scores.get(confidence_level, 1.96)

    def _determine_trend_direction(self, values: List[float]) -> str:
        """
        判断趋势方向

        Args:
            values: 值序列

        Returns:
            "上升", "下降", "稳定", "波动"
        """
        if len(values) < 2:
            return "稳定"

        # 计算最近的变化趋势
        recent = values[-min(5, len(values)) :]
        if len(recent) < 2:
            return "稳定"

        # 计算变化率
        changes = [recent[i + 1] - recent[i] for i in range(len(recent) - 1)]
        avg_change = sum(changes) / len(changes)
        max_val = max(values)
        min_val = min(values)
        range_val = max_val - min_val if max_val != min_val else 1

        # 相对变化率
        relative_change = avg_change / (range_val + 1e-10)

        if relative_change > 0.05:
            return "上升"
        elif relative_change < -0.05:
            return "下降"
        else:
            # 检查波动性
            if len(changes) > 1:
                variance = sum((c - avg_change) ** 2 for c in changes) / len(changes)
                if variance > (range_val * 0.1) ** 2:
                    return "波动"
            return "稳定"

    def _detect_anomaly(self, original_values: List[float], smoothed_values: List[float]) -> Tuple[bool, float, str]:
        """
        异常趋势检测

        Args:
            original_values: 原始值
            smoothed_values: 平滑后的值

        Returns:
            (是否异常, 异常评分, 描述信息)
        """
        if len(original_values) < self._window_size:
            return False, 0.0, ""

        # 计算最近窗口的统计特征
        recent_original = original_values[-self._window_size :]
        recent_smoothed = smoothed_values[-self._window_size :]

        mean_val = sum(recent_smoothed) / len(recent_smoothed)
        std_val = math.sqrt(sum((v - mean_val) ** 2 for v in recent_smoothed) / len(recent_smoothed))

        if std_val == 0:
            return False, 0.0, ""

        # 检查趋势方向
        trend_dir = self._determine_trend_direction(recent_smoothed)

        # 计算最后值与均值的偏差
        last_val = recent_original[-1]
        z_score = abs(last_val - mean_val) / std_val

        # 如果数据有明显趋势，放宽异常检测
        # 趋势数据中，最后值偏离均值是正常的
        if trend_dir in ["上升", "下降"]:
            # 对于趋势数据，检查最后值是否与趋势方向一致
            if len(recent_smoothed) >= 2:
                prev_val = recent_smoothed[-2]
                if (trend_dir == "上升" and last_val >= prev_val) or (trend_dir == "下降" and last_val <= prev_val):
                    # 值与趋势方向一致，降低异常评分
                    z_score = z_score * 0.3

        anomaly_score = min(z_score / self._anomaly_threshold, 1.0)
        anomaly_detected = z_score > self._anomaly_threshold

        message = ""
        if anomaly_detected:
            if last_val > mean_val:
                message = f"Warning: Detected abnormal increase (Z-score: {z_score:.2f})"
            else:
                message = f"Warning: Detected abnormal decrease (Z-score: {z_score:.2f})"

        return anomaly_detected, anomaly_score, message

    def _generate_message(
        self,
        trend_direction: str,
        anomaly_detected: bool,
        anomaly_score: float,
        data_points: int,
    ) -> str:
        """
        生成预测描述消息

        Args:
            trend_direction: 趋势方向
            anomaly_detected: 是否异常
            anomaly_score: 异常评分
            data_points: 数据点数量

        Returns:
            描述消息
        """
        direction_map = {
            "上升": "呈上升趋势",
            "下降": "呈下降趋势",
            "稳定": "保持稳定",
            "波动": "波动较大",
        }

        base_msg = f"基于 {data_points} 个历史数据点分析，{direction_map.get(trend_direction, '未知趋势')}。"

        if anomaly_detected:
            base_msg += f"\n异常评分: {anomaly_score:.2f} (0-1, >0.7 为高风险)"

        return base_msg


class TrendAnalyzer:
    """
    趋势分析器

    整合多个数据系列的预测和异常检测
    """

    def __init__(self):
        self._predictor = TrendPredictor()
        self._analysis_results: Dict[str, Optional[TrendPrediction]] = {}

    def analyze_series(
        self,
        series_name: str,
        historical_data: List[Tuple[float, float]],
        timestamp_interval: float = 60.0,
    ) -> Optional[TrendPrediction]:
        """
        分析单个数据系列

        Args:
            series_name: 系列名称
            historical_data: 历史数据
            timestamp_interval: 时间间隔

        Returns:
            预测结果
        """
        prediction = self._predictor.predict(historical_data, timestamp_interval)
        self._analysis_results[series_name] = prediction
        return prediction

    def get_analysis_summary(self) -> Dict[str, dict]:
        """
        获取所有系列的分析摘要

        Returns:
            {series_name: {trend_direction, anomaly_detected, anomaly_score, message}}
        """
        summary = {}
        for name, prediction in self._analysis_results.items():
            if prediction:
                summary[name] = {
                    "trend_direction": prediction.trend_direction,
                    "anomaly_detected": prediction.anomaly_detected,
                    "anomaly_score": prediction.anomaly_score,
                    "message": prediction.message,
                }
        return summary

    def has_anomalies(self) -> bool:
        """检查是否有任何系列检测到异常"""
        return any(p.anomaly_detected for p in self._analysis_results.values() if p)

    def clear(self) -> None:
        """清空分析结果"""
        self._analysis_results.clear()
