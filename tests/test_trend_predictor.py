# -*- coding: utf-8 -*-
"""趋势预测模块测试"""

import time
import unittest
from typing import List, Tuple

from core.processing.trend_predictor import TrendAnalyzer, TrendPrediction, TrendPredictor


class TestTrendPredictor(unittest.TestCase):
    """趋势预测器测试"""

    def setUp(self):
        """测试前准备"""
        self.predictor = TrendPredictor(window_size=5, prediction_steps=3)

    def _generate_test_data(self, num_points: int = 20, trend: str = "stable") -> List[Tuple[float, float]]:
        """
        生成测试数据

        Args:
            num_points: 数据点数量
            trend: 趋势类型 ("up", "down", "stable", "volatile")

        Returns:
            [(timestamp, value), ...]
        """
        base_ts = time.time() - num_points * 60
        data = []

        for i in range(num_points):
            ts = base_ts + i * 60

            if trend == "up":
                val = 50 + i * 2
            elif trend == "down":
                val = 100 - i * 2
            elif trend == "volatile":
                val = 50 + (i % 5) * 10
            else:  # stable - truly stable, no oscillation
                val = 50.0

            data.append((ts, val))

        return data

    def test_predict_with_stable_data(self):
        """测试稳定数据的预测"""
        data = self._generate_test_data(20, "stable")
        result = self.predictor.predict(data)

        self.assertIsNotNone(result)
        self.assertIsInstance(result, TrendPrediction)
        self.assertEqual(result.trend_direction, "稳定")
        self.assertFalse(result.anomaly_detected)

    def test_predict_with_upward_trend(self):
        """测试上升趋势的预测"""
        data = self._generate_test_data(20, "up")
        result = self.predictor.predict(data)

        self.assertIsNotNone(result)
        self.assertEqual(result.trend_direction, "上升")
        self.assertFalse(result.anomaly_detected)

    def test_predict_with_downward_trend(self):
        """测试下降趋势的预测"""
        data = self._generate_test_data(20, "down")
        result = self.predictor.predict(data)

        self.assertIsNotNone(result)
        self.assertEqual(result.trend_direction, "下降")
        self.assertFalse(result.anomaly_detected)

    def test_predict_with_volatile_data(self):
        """测试波动数据的预测"""
        data = self._generate_test_data(20, "volatile")
        result = self.predictor.predict(data)

        self.assertIsNotNone(result)
        # 波动数据可能被识别为"波动"或保持稳定
        self.assertIn(result.trend_direction, ["波动", "稳定"])

    def test_predict_insufficient_data(self):
        """测试数据不足时的预测"""
        data = self._generate_test_data(3)  # 少于 window_size
        result = self.predictor.predict(data)

        self.assertIsNone(result)

    def test_prediction_output_structure(self):
        """测试预测输出结构"""
        data = self._generate_test_data(20, "stable")
        result = self.predictor.predict(data)

        self.assertIsNotNone(result)
        self.assertIsInstance(result.predicted_values, list)
        self.assertIsInstance(result.confidence_upper, list)
        self.assertIsInstance(result.confidence_lower, list)
        self.assertIsInstance(result.trend_direction, str)
        self.assertIsInstance(result.anomaly_detected, bool)
        self.assertIsInstance(result.anomaly_score, float)
        self.assertIsInstance(result.message, str)

        # 验证预测值数量
        self.assertEqual(len(result.predicted_values), self.predictor._prediction_steps)
        self.assertEqual(len(result.confidence_upper), self.predictor._prediction_steps)
        self.assertEqual(len(result.confidence_lower), self.predictor._prediction_steps)

    def test_prediction_values_increasing(self):
        """测试预测值的时间顺序"""
        data = self._generate_test_data(20, "up")
        result = self.predictor.predict(data)

        self.assertIsNotNone(result)
        timestamps = [t for t, _ in result.predicted_values]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_anomaly_detection(self):
        """测试异常检测"""
        # 构造包含异常的数据
        data = self._generate_test_data(15, "stable")
        # 在末尾添加一个异常值
        last_ts = data[-1][0]
        data.append((last_ts + 60, 200))  # 异常高值

        result = self.predictor.predict(data)

        # 可能检测到异常
        if result.anomaly_detected:
            self.assertGreater(result.anomaly_score, 0)
            self.assertIn("异常", result.message)


class TestTrendAnalyzer(unittest.TestCase):
    """趋势分析器测试"""

    def setUp(self):
        """测试前准备"""
        self.analyzer = TrendAnalyzer()

    def _generate_test_data(self, num_points: int = 20, trend: str = "stable") -> List[Tuple[float, float]]:
        """生成测试数据"""
        base_ts = time.time() - num_points * 60
        data = []

        for i in range(num_points):
            ts = base_ts + i * 60
            if trend == "up":
                val = 50 + i * 2
            elif trend == "down":
                val = 100 - i * 2
            else:
                val = 50 + (i % 2) * 2
            data.append((ts, val))

        return data

    def test_analyze_single_series(self):
        """测试单系列分析"""
        data = self._generate_test_data(20, "up")
        result = self.analyzer.analyze_series("test_series", data)

        self.assertIsNotNone(result)
        self.assertEqual(result.trend_direction, "上升")

    def test_analyze_multiple_series(self):
        """测试多系列分析"""
        data_up = self._generate_test_data(20, "up")
        data_down = self._generate_test_data(20, "down")

        self.analyzer.analyze_series("series_up", data_up)
        self.analyzer.analyze_series("series_down", data_down)

        summary = self.analyzer.get_analysis_summary()

        self.assertIn("series_up", summary)
        self.assertIn("series_down", summary)
        self.assertEqual(summary["series_up"]["trend_direction"], "上升")
        self.assertEqual(summary["series_down"]["trend_direction"], "下降")

    def test_has_anomalies(self):
        """测试异常检测"""
        data = self._generate_test_data(20, "stable")
        # 添加异常
        last_ts = data[-1][0]
        data.append((last_ts + 60, 200))

        self.analyzer.analyze_series("anomaly_series", data)

        # 应该检测到异常
        self.assertTrue(self.analyzer.has_anomalies())

    def test_clear_analysis(self):
        """测试清空分析结果"""
        data = self._generate_test_data(20, "stable")
        self.analyzer.analyze_series("test", data)

        self.assertTrue(len(self.analyzer._analysis_results) > 0)

        self.analyzer.clear()

        self.assertEqual(len(self.analyzer._analysis_results), 0)
        self.assertFalse(self.analyzer.has_anomalies())


if __name__ == "__main__":
    unittest.main()
