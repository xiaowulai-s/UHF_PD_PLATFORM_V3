# -*- coding: utf-8 -*-
"""
数据分析页面 - 导入 PRPD 实验数据并分析

支持:
- CSV/Excel/TXT 文件拖放和选择导入
- 自动识别数据格式和列名映射
- PRPD 图谱显示 (复用 PRPDWidget)
- 幅值分布直方图 + 相位分布柱状图
- 放电类型自动识别 + 置信度评估
- 幅值阈值过滤
- 报告导出 (JSON/Excel/Word)
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import DT

try:
    import pyqtgraph as pg

    pg.setConfigOptions(antialias=True, foreground=DT.C.CHART_FOREGROUND)
    HAS_PYQTGRAPH = True
except ImportError:
    HAS_PYQTGRAPH = False


# ═══════════════════════════════════════════════════════
# 数据导入器
# ═══════════════════════════════════════════════════════


class DataImporter:
    """PRPD 实验数据导入器"""

    SUPPORTED_EXTENSIONS = [".csv", ".xlsx", ".xls", ".txt"]

    @staticmethod
    def load(path: str) -> Tuple[str, np.ndarray, np.ndarray, np.ndarray, dict]:
        """
        加载数据文件

        Returns:
            (format_name, phases, amplitudes, metadata)
        """
        suffix = Path(path).suffix.lower()
        if suffix not in DataImporter.SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {suffix}，支持: CSV/Excel/TXT")

        if suffix == ".csv":
            return DataImporter._load_csv(path)
        elif suffix in (".xlsx", ".xls"):
            return DataImporter._load_excel(path)
        else:
            return DataImporter._load_txt(path)

    @staticmethod
    def _load_csv(path: str) -> Tuple[str, np.ndarray, np.ndarray, np.ndarray, dict]:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = [r for r in reader if r and not r[0].startswith(("#", "%", "//"))]

        header = [h.strip().lower() for h in rows[0]]
        data_rows = rows[1:]

        # 自动映射列
        phase_idx, amp_idx, cycle_idx = None, None, None
        for i, h in enumerate(header):
            if h in ("phase_deg", "phase", "phi", "φ", "相位"):
                phase_idx = i
            if h in ("discharge", "amplitude", "amp", "magnitude", "幅值", "mv", "pc", "放电量"):
                amp_idx = i
            if h in ("cycle", "周期", "n", "period", "工频周期"):
                cycle_idx = i

        phases, amplitudes, cycles = [], [], []
        for row in data_rows:
            try:
                p = float(row[phase_idx]) if phase_idx is not None and len(row) > phase_idx else None
                a = float(row[amp_idx]) if amp_idx is not None and len(row) > amp_idx else None
                c = int(row[cycle_idx]) if cycle_idx is not None and len(row) > cycle_idx else 0
                if p is not None and a is not None and 0 <= p <= 360 and a >= 0:
                    phases.append(p)
                    amplitudes.append(a)
                    cycles.append(c)
            except (ValueError, IndexError, TypeError):
                continue

        if not phases:
            # 回退: 尝试前两列
            for row in data_rows:
                if len(row) >= 2:
                    try:
                        p, a = float(row[0]), float(row[1])
                        if 0 <= p <= 360 and a >= 0:
                            phases.append(p)
                            amplitudes.append(a)
                            cycles.append(0)
                    except (ValueError, IndexError):
                        continue

        return (
            "phase_amplitude",
            np.array(phases),
            np.array(amplitudes),
            np.array(cycles, dtype=np.int32),
            {
                "columns": header,
                "total_rows": len(data_rows),
                "file": path,
            },
        )

    @staticmethod
    def _load_excel(path: str) -> Tuple[str, np.ndarray, np.ndarray, np.ndarray, dict]:
        try:
            import openpyxl
        except ImportError:
            raise ImportError("需要安装 openpyxl: pip install openpyxl")

        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        header = [str(c).strip().lower() if c else "" for c in all_rows[0]]
        data_rows = all_rows[1:]
        wb.close()

        phase_idx, amp_idx, cycle_idx = None, None, None
        for i, h in enumerate(header):
            if h in ("phase_deg", "phase", "phi", "φ", "相位"):
                phase_idx = i
            if h in ("discharge", "amplitude", "amp", "magnitude", "幅值", "mv", "pc"):
                amp_idx = i
            if h in ("cycle", "周期", "n", "period"):
                cycle_idx = i

        phases, amplitudes, cycles = [], [], []
        default_idx = 0
        for row in data_rows:
            try:
                p = (
                    float(row[phase_idx])
                    if phase_idx is not None and len(row) > phase_idx
                    else float(row[default_idx]) if row[default_idx] is not None else None
                )
                a = (
                    float(row[amp_idx])
                    if amp_idx is not None and len(row) > amp_idx
                    else (
                        float(row[default_idx + 1])
                        if len(row) > default_idx + 1 and row[default_idx + 1] is not None
                        else None
                    )
                )
                c = int(row[cycle_idx]) if cycle_idx is not None and len(row) > cycle_idx else 0
                if p is not None and a is not None and 0 <= p <= 360 and a >= 0:
                    phases.append(p)
                    amplitudes.append(a)
                    cycles.append(c)
            except (ValueError, IndexError, TypeError):
                continue

        return (
            "phase_amplitude",
            np.array(phases),
            np.array(amplitudes),
            np.array(cycles, dtype=np.int32),
            {
                "columns": header,
                "total_rows": len(data_rows),
                "file": path,
            },
        )

    @staticmethod
    def _load_txt(path: str) -> Tuple[str, np.ndarray, np.ndarray, np.ndarray, dict]:
        with open(path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith(("#", "%"))]

        phases, amplitudes, cycles = [], [], []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    p, a = float(parts[0]), float(parts[1])
                    if 0 <= p <= 360:
                        phases.append(p)
                        amplitudes.append(a)
                        cycles.append(0)
                except ValueError:
                    continue

        return (
            "phase_amplitude",
            np.array(phases),
            np.array(amplitudes),
            np.array(cycles, dtype=np.int32),
            {
                "columns": ["col1", "col2"],
                "total_rows": len(lines),
                "file": path,
            },
        )


# ═══════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════


class DischargeClassifier:
    """基于 PRPD 统计特征的放电类型分类

    使用参考剖面匹配(Profile Matching)策略:
      1. 提取多维特征向量(相位分布 + 幅值分布 + 能量分布)
      2. 计算与各放电类型参考剖面的高斯核相似度
      3. 归一化得到各类型匹配概率
      4. 最高概率类型为参考结果，概率即为置信度

    四种放电类型的物理特征:
      - 内部气隙: S1+S4高度集中，S1>S4(正半周略强)，低扩散，低CV
      - 电晕:     负半周能量占绝对优势(posE低)，相位分布较宽
      - 沿面:     S1+S4极高度集中，S1≈S4(高对称)，极低扩散，低CV
      - 悬浮:     相位分布宽，CV极高(幅值变异大)，正负能量均衡
    """

    PATTERNS = {
        "internal": {
            "name": "内部气隙放电",
            "name_en": "Internal Discharge",
            "desc": "绝缘介质内部气隙或杂质引起的放电。正负半周对称，集中在0°~60°和180°~240°（第一/三象限）。",
        },
        "corona": {
            "name": "电晕放电",
            "name_en": "Corona Discharge",
            "desc": "高压导体尖端产生的放电。集中在负半周270°~330°，幅值较稳定，正半周极少。",
        },
        "surface": {
            "name": "沿面放电",
            "name_en": "Surface Discharge",
            "desc": "绝缘表面爬电。正半周30°~90°集中，负半周分散或不对称。",
        },
        "floating": {
            "name": "悬浮颗粒放电",
            "name_en": "Floating Discharge",
            "desc": "金属悬浮电位体引起的放电。相位分布宽，无明显集中区间，幅值大且分散。",
        },
    }

    # ── 参考剖面: 各放电类型的典型特征值 ──
    # 基于PRPD物理机理 + 真实实验数据校准
    PROFILES = {
        "corona": {
            "quad13": 0.32,       # 低集中度(相位分散)
            "spread": 0.68,       # 高扩散
            "top2": 0.38,         # 低集中度
            "symmetry": 0.88,     # 可对称可不对称
            "s1_s4_ratio": 1.10,  # S1略大于S4
            "cv": 1.50,           # 中等变异
            "pos_energy": 0.25,   # 核心: 负半周能量占绝对优势
            "neg_tail": 0.33,     # S5+S6显著
        },
        "internal": {
            "quad13": 0.75,       # 高集中度
            "spread": 0.25,       # 低扩散
            "top2": 0.75,         # 高集中度
            "symmetry": 0.70,     # 中等对称(S1>S4)
            "s1_s4_ratio": 1.45,  # 核心: S1明显大于S4
            "cv": 0.80,           # 低变异
            "pos_energy": 0.50,   # 正负能量均衡
            "neg_tail": 0.14,     # S5+S6少
        },
        "surface": {
            "quad13": 0.88,       # 极高集中度
            "spread": 0.12,       # 极低扩散
            "top2": 0.88,         # 极高集中度
            "symmetry": 0.95,     # 极高对称(S1≈S4)
            "s1_s4_ratio": 1.05,  # 核心: S1≈S4
            "cv": 0.50,           # 低变异
            "pos_energy": 0.50,   # 正负能量大致均衡
            "neg_tail": 0.06,     # S5+S6极少
        },
        "floating": {
            "quad13": 0.38,       # 低集中度
            "spread": 0.62,       # 高扩散
            "top2": 0.40,         # 低集中度
            "symmetry": 0.90,     # 较对称
            "s1_s4_ratio": 0.90,  # S4略大于S1
            "cv": 2.50,           # 核心: 极高变异
            "pos_energy": 0.49,   # 正负能量均衡
            "neg_tail": 0.31,     # S5+S6中等
        },
    }

    # 特征权重: 越重要的特征权重越高
    FEATURE_WEIGHTS = {
        "quad13": 1.0,
        "spread": 0.8,
        "top2": 0.6,
        "symmetry": 0.7,
        "s1_s4_ratio": 1.2,      # 区分内部vs沿面的核心
        "cv": 1.2,               # 区分悬浮的核心
        "pos_energy": 1.3,       # 区分电晕的核心
        "neg_tail": 0.8,
    }

    # 高斯核宽度(σ): 控制匹配容忍度, σ越小越严格
    FEATURE_SIGMAS = {
        "quad13": 0.15,
        "spread": 0.15,
        "top2": 0.15,
        "symmetry": 0.15,
        "s1_s4_ratio": 0.25,
        "cv": 0.80,
        "pos_energy": 0.10,
        "neg_tail": 0.12,
    }

    @classmethod
    def _extract_features(cls, phases: np.ndarray, amplitudes: np.ndarray) -> dict:
        """从 PRPD 数据中提取多维特征向量"""
        n = len(phases)
        if n < 10:
            return {}

        # ── 自适应去噪 ──
        amp_median = float(np.median(amplitudes))
        p5 = float(np.percentile(amplitudes, 5))
        min_noise_threshold = max(1.0, float(np.min(amplitudes[amplitudes > 0])) if np.any(amplitudes > 0) else 1.0)
        noise_threshold = max(amp_median * 2.0, p5 * 1.5, min_noise_threshold)
        clean_mask = amplitudes >= noise_threshold
        p_clean = phases[clean_mask]
        a_clean = amplitudes[clean_mask]
        n_clean = len(p_clean)

        if n_clean < max(20, n * 0.3):
            p_clean, a_clean = phases, amplitudes
            n_clean = n
            noise_removed = 0
        else:
            noise_removed = n - n_clean

        # ── 6 扇区分析 ──
        def calc_sectors(ph, amp=None):
            sec = np.zeros(6)
            for i in range(6):
                mask = (ph >= i * 60) & (ph < (i + 1) * 60)
                sec[i] = float(np.sum(amp[mask])) if amp is not None else float(np.sum(mask))
            return sec / (np.sum(sec) + 1e-10)

        sectors_count = calc_sectors(p_clean)
        sectors_weighted = calc_sectors(p_clean, a_clean)
        S1, S2, S3, S4, S5, S6 = sectors_count
        W1, W2, W3, W4, W5, W6 = sectors_weighted

        # ── 相位分布特征 ──
        quad13 = S1 + S4
        spread = S2 + S3 + S5 + S6
        top2 = sum(sorted([S1, S2, S3, S4, S5, S6], reverse=True)[:2])
        symmetry = 1.0 - abs(S1 - S4) / (max(S1, S4) + 1e-10)
        s1_s4_ratio = S1 / (S4 + 1e-10)  # S1/S4 直接比值
        neg_tail = S5 + S6
        pos_ratio = S1 + S2 + S3
        s1_dominance = S1 / (S1 + S2 + S3 + 1e-10)
        corona_tail = S6 / (S5 + S6 + 1e-10)
        s1_ratio = S1 / (S1 + S4 + 1e-10)
        asymmetry_ratio = abs((W1 + W2 + W3) - (W4 + W5 + W6)) / ((W1 + W2 + W3) + (W4 + W5 + W6) + 1e-10)

        # ── 幅值分布特征 ──
        amp_mean = float(np.mean(a_clean))
        amp_std = float(np.std(a_clean))
        amp_cv = amp_std / (amp_mean + 1e-10)
        p75_amp = float(np.percentile(a_clean, 75))
        bimodality = float(np.sum(a_clean > p75_amp)) / (n_clean + 1e-10)

        # ── 能量分布特征 ──
        pos_half_mask = (p_clean >= 0) & (p_clean < 180)
        pos_amp_energy = float(np.sum(a_clean[pos_half_mask]))
        total_amp_energy = float(np.sum(a_clean)) + 1e-10
        pos_energy = pos_amp_energy / total_amp_energy

        return {
            # 匹配用特征
            "quad13": quad13, "spread": spread, "top2": top2,
            "symmetry": symmetry, "s1_s4_ratio": s1_s4_ratio,
            "cv": amp_cv, "pos_energy": pos_energy, "neg_tail": neg_tail,
            # 辅助特征(不参与匹配, 但返回给UI)
            "S1": S1, "S2": S2, "S3": S3, "S4": S4, "S5": S5, "S6": S6,
            "pos_ratio": pos_ratio, "s1_dominance": s1_dominance,
            "corona_tail": corona_tail, "s1_ratio": s1_ratio,
            "asymmetry_ratio": asymmetry_ratio, "bimodality": bimodality,
            # 元信息
            "noise_removed": noise_removed, "clean_events": n_clean,
            "max_amp": float(np.max(amplitudes)),
            "p90": float(np.percentile(amplitudes, 90)),
            "amp_median": amp_median,
        }

    @classmethod
    def _compute_similarity(cls, features: dict, profile: dict) -> float:
        """计算特征向量与参考剖面的加权高斯核相似度"""
        score = 0.0
        total_weight = 0.0
        for fname, weight in cls.FEATURE_WEIGHTS.items():
            if fname not in features or fname not in profile or fname not in cls.FEATURE_SIGMAS:
                continue
            diff = features[fname] - profile[fname]
            sigma = cls.FEATURE_SIGMAS[fname]
            sim = np.exp(-diff ** 2 / (2 * sigma ** 2))
            score += weight * sim
            total_weight += weight
        return score / total_weight if total_weight > 0 else 0.0

    @classmethod
    def classify(cls, phases: np.ndarray, amplitudes: np.ndarray) -> dict:
        if len(phases) < 10:
            return {
                "type": "unknown", "name": "未知", "name_en": "Unknown",
                "confidence": 0, "severity": "normal",
                "symmetry": False, "pos_ratio": 0.5,
                "concentration": 0, "description": "数据不足",
            }

        # ── 特征提取 ──
        feat = cls._extract_features(phases, amplitudes)
        if not feat:
            return {
                "type": "unknown", "name": "未知", "name_en": "Unknown",
                "confidence": 0, "severity": "normal",
                "symmetry": False, "pos_ratio": 0.5,
                "concentration": 0, "description": "特征提取失败",
            }

        max_amp = feat["max_amp"]
        p90 = feat["p90"]
        top2 = feat["top2"]
        spread = feat["spread"]
        pos_ratio = feat["pos_ratio"]
        symmetry = feat["symmetry"]
        amp_cv = feat["cv"]
        noise_removed = feat["noise_removed"]
        n_clean = feat["clean_events"]
        min_noise_threshold = max(1.0, float(np.min(amplitudes[amplitudes > 0])) if np.any(amplitudes > 0) else 1.0)

        # ── 纯噪声拒判 ──
        if top2 < 0.40 and max_amp < min_noise_threshold * 5:
            return {
                "type": "noise", "name": "纯噪声", "name_en": "Noise",
                "description": "信号为随机噪声，无明显放电特征",
                "confidence": float(f"{min(spread, 0.90):.2f}"),
                "severity": "normal", "symmetry": False,
                "pos_ratio": float(f"{pos_ratio:.2f}"),
                "concentration": float(f"{top2:.3f}"),
                "noise_removed": noise_removed, "clean_events": n_clean,
                "features": {k: float(f"{v:.3f}") if isinstance(v, float) else v
                             for k, v in feat.items() if k not in ("noise_removed", "clean_events", "max_amp", "p90", "amp_median")},
                "scores": {t: 0.0 for t in ["corona", "surface", "internal", "floating"]},
            }

        # ── 剖面匹配: 计算与各类型的相似度 ──
        raw_scores = {}
        for type_id in cls.PROFILES:
            raw_scores[type_id] = cls._compute_similarity(feat, cls.PROFILES[type_id])

        # 归一化为概率分布(softmax with temperature)
        score_values = np.array(list(raw_scores.values()))
        temperature = 5.0  # 温度参数: 越大越平滑(区分度越低), 越小越尖锐
        exp_scores = np.exp(score_values * temperature)
        normalized = exp_scores / (np.sum(exp_scores) + 1e-10)

        scores = {}
        for i, type_id in enumerate(cls.PROFILES):
            scores[type_id] = float(normalized[i])

        # ── 决策 ──
        best_type = max(scores, key=scores.get)
        confidence = scores[best_type]

        # 严重等级
        severity = "normal"
        if max_amp > 5000 or p90 > 3000:
            severity = "critical"
        elif max_amp > 2000 or p90 > 1000:
            severity = "warning"
        elif max_amp > 500:
            severity = "attention"

        pattern = cls.PATTERNS[best_type]
        return {
            "type": best_type,
            "name": pattern["name"],
            "name_en": pattern["name_en"],
            "description": pattern["desc"],
            "confidence": float(f"{confidence:.2f}"),
            "severity": severity,
            "symmetry": symmetry > 0.6,
            "pos_ratio": float(f"{pos_ratio:.2f}"),
            "concentration": float(f"{top2:.3f}"),
            "noise_removed": noise_removed,
            "clean_events": n_clean,
            "features": {k: float(f"{v:.3f}") if isinstance(v, float) else v
                         for k, v in feat.items() if k not in ("noise_removed", "clean_events", "max_amp", "p90", "amp_median")},
            "scores": {k: float(f"{v:.3f}") for k, v in scores.items()},
        }

    @classmethod
    def _synthesize_cycles(
        cls,
        phases: np.ndarray,
        amplitudes: np.ndarray,
        events_per_sample: Optional[int] = None,
        min_events_per_sample: int = 10,
        num_samples: int = 50,
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        通过有放回随机抽样（Bootstrap）生成多个代表性子样本。

        每个子样本从全量数据中随机抽取 events_per_sample 个事件，
        保证每个子样本的相位/幅值分布与整体一致，避免相位排序
        导致的分布撕裂问题。

        Args:
            phases: 相位数组 (N,)
            amplitudes: 幅值数组 (N,)
            events_per_sample: 每子样本目标事件数。None=自动检测(max(n//3, 30))
            min_events_per_sample: 单子样本最少事件数
            num_samples: 生成子样本数量（默认50次）

        Returns:
            [(sample_phases, sample_amplitudes), ...] 子样本列表
        """
        n = len(phases)
        if n < min_events_per_sample * 2:
            return [(phases, amplitudes)]

        # 自动检测: 每个样本取总量的 1/8 (兼顾代表性和采样方差)
        if events_per_sample is None:
            events_per_sample = max(min_events_per_sample, n // 8)

        # 有放回随机抽样
        rng = np.random.default_rng(seed=42)
        cycles = []
        for _ in range(num_samples):
            idx = rng.integers(0, n, size=events_per_sample)
            cyc_ph = phases[idx]
            cyc_amp = amplitudes[idx]
            if len(cyc_ph) >= min_events_per_sample:
                cycles.append((cyc_ph, cyc_amp))

        if not cycles:
            cycles = [(phases, amplitudes)]

        return cycles

    @classmethod
    def classify_cycles(
        cls,
        phases: np.ndarray,
        amplitudes: np.ndarray,
        events_per_sample: Optional[int] = None,
    ) -> dict:
        """
        通过 Bootstrap 重采样多次分类，返回统计聚合结果。

        流程:
          1. 从全量数据中有放回随机抽取 50 个子样本（每个含 ~N/3 事件）
          2. 每个子样本独立调用 classify()
          3. 统计各类型出现次数和占比
          4. 产生"参考结果"（多数投票）

        Args:
            phases: 相位数组 (N,)
            amplitudes: 幅值数组 (N,)
            events_per_sample: 每子样本目标事件数

        Returns:
            包含以下字段的字典:
            - total_samples: 总采样数
            - type_counts: {type_id: count} 各类型出现次数
            - type_ratios: {type_id: ratio} 各类型占比 (0~1)
            - reference_result: 多数投票得出的参考结果 dict
              - type, name, name_en, confidence(=最高占比), marked_as="参考结果"
            - severity: 基于全局幅值的严重等级
            - per_sample_results: [dict, ...] 每个子样本的原始 classify() 结果
            - amplitude_stats: {total_events, max, min, avg, median, p90}
        """
        if len(phases) < 10:
            return {
                "total_samples": 0,
                "type_counts": {},
                "type_ratios": {},
                "reference_result": {
                    "type": "unknown", "name": "未知",
                    "name_en": "Unknown", "confidence": 0,
                    "marked_as": "参考结果",
                },
                "severity": "normal",
                "per_sample_results": [],
                "amplitude_stats": cls._calc_amplitude_stats(phases, amplitudes),
            }

        # ── Bootstrap 重采样 ───────────────────
        samples = cls._synthesize_cycles(phases, amplitudes, events_per_sample)

        # ── 逐样本分类 ────────────────────────
        type_counts: dict[str, int] = {}
        per_sample_results = []
        for samp_ph, samp_amp in samples:
            result = cls.classify(samp_ph, samp_amp)
            per_sample_results.append(result)
            t = result.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        total = len(samples)
        type_ratios = {t: c / total for t, c in type_counts.items()}

        # ── 参考结果：多数投票 ─────────────────
        best_type = max(type_counts, key=type_counts.get) if type_counts else "unknown"
        best_count = type_counts.get(best_type, 0)
        best_ratio = best_count / total if total > 0 else 0

        # 置信度 = 最高占比类型投票率
        confidence = best_ratio

        pattern = cls.PATTERNS.get(best_type, {})
        reference_result = {
            "type": best_type,
            "name": pattern.get("name", best_type),
            "name_en": pattern.get("name_en", best_type),
            "confidence": float(f"{confidence:.2f}"),
            "marked_as": "参考结果",
            "description": pattern.get("desc", ""),
        }

        # ── 严重等级（基于全局幅值）──────────────
        amp_stats = cls._calc_amplitude_stats(phases, amplitudes)
        severity = cls._determine_severity(amp_stats)

        return {
            "total_samples": total,
            "type_counts": type_counts,
            "type_ratios": type_ratios,
            "reference_result": reference_result,
            "severity": severity,
            "per_sample_results": per_sample_results,
            "amplitude_stats": amp_stats,
        }

    @classmethod
    def classify_by_cycles(
        cls,
        phases: np.ndarray,
        amplitudes: np.ndarray,
        cycles: np.ndarray,
    ) -> dict:
        """
        按实际工频周期逐周期分类，统计各类型出现次数。

        流程:
          1. 按 cycle 列值将事件分组
          2. 每个 cycle 单独调用 classify()
          3. 统计各类型出现次数和占比
          4. 产生“参考结果”（多数投票）

        Args:
            phases: 相位数组 (N,)
            amplitudes: 幅值数组 (N,)
            cycles: 工频周期号数组 (N,)

        Returns:
            与 classify_cycles 相同结构的字典
        """
        if len(phases) < 10:
            return {
                "total_samples": 0,
                "type_counts": {},
                "type_ratios": {},
                "reference_result": {
                    "type": "unknown", "name": "未知",
                    "name_en": "Unknown", "confidence": 0,
                    "marked_as": "参考结果",
                },
                "severity": "normal",
                "per_sample_results": [],
                "amplitude_stats": cls._calc_amplitude_stats(phases, amplitudes),
            }

        # 按 cycle 分组
        unique_cycles = np.unique(cycles[cycles > 0]) if np.any(cycles > 0) else np.array([0])
        type_counts: dict[str, int] = {}
        per_sample_results = []

        for cyc in unique_cycles:
            mask = cycles == cyc
            cyc_ph = phases[mask]
            cyc_amp = amplitudes[mask]
            if len(cyc_ph) < 5:
                continue
            result = cls.classify(cyc_ph, cyc_amp)
            per_sample_results.append(result)
            t = result.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        total = len(per_sample_results)
        type_ratios = {t: c / total for t, c in type_counts.items()} if total > 0 else {}

        # 参考结果：多数投票
        best_type = max(type_counts, key=type_counts.get) if type_counts else "unknown"
        best_count = type_counts.get(best_type, 0)
        best_ratio = best_count / total if total > 0 else 0

        # 置信度 = 最高占比类型投票率
        confidence = best_ratio

        pattern = cls.PATTERNS.get(best_type, {})
        reference_result = {
            "type": best_type,
            "name": pattern.get("name", best_type),
            "name_en": pattern.get("name_en", best_type),
            "confidence": float(f"{confidence:.2f}"),
            "marked_as": "参考结果",
            "description": pattern.get("desc", ""),
        }

        amp_stats = cls._calc_amplitude_stats(phases, amplitudes)
        severity = cls._determine_severity(amp_stats)

        return {
            "total_samples": total,
            "type_counts": type_counts,
            "type_ratios": type_ratios,
            "reference_result": reference_result,
            "severity": severity,
            "per_sample_results": per_sample_results,
            "amplitude_stats": amp_stats,
            "classification_mode": "per-cycle",  # 标识为按实际周期分类
        }

    @staticmethod
    def _calc_amplitude_stats(phases: np.ndarray, amplitudes: np.ndarray) -> dict:
        """计算幅值统计指标"""
        if len(amplitudes) == 0:
            return {
                "total_events": 0, "max_amplitude": 0, "min_amplitude": 0,
                "avg_amplitude": 0, "median_amplitude": 0, "p90": 0,
            }
        return {
            "total_events": int(len(amplitudes)),
            "max_amplitude": float(np.max(amplitudes)),
            "min_amplitude": float(np.min(amplitudes)),
            "avg_amplitude": float(np.mean(amplitudes)),
            "median_amplitude": float(np.median(amplitudes)),
            "p90": float(np.percentile(amplitudes, 90)),
        }

    @staticmethod
    def _determine_severity(amp_stats: dict) -> str:
        """根据幅值统计确定严重等级"""
        max_amp = amp_stats.get("max_amplitude", 0)
        p90 = amp_stats.get("p90", 0)
        if max_amp > 5000 or p90 > 3000:
            return "critical"
        elif max_amp > 2000 or p90 > 1000:
            return "warning"
        elif max_amp > 500:
            return "attention"
        return "normal"


# ═══════════════════════════════════════════════════════
# 报告生成器
# ═══════════════════════════════════════════════════════


class ReportExporter:
    """报告导出"""

    @staticmethod
    def export_json(data: dict, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        return path

    @staticmethod
    def export_excel(data: dict, path: str) -> str:
        try:
            import openpyxl
        except ImportError:
            raise ImportError("需要安装 openpyxl: pip install openpyxl")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "PRPD分析报告"

        # 标题
        ws.merge_cells("A1:E1")
        ws["A1"] = "PRPD 分析报告"
        ws["A1"].font = openpyxl.styles.Font(size=14, bold=True)

        # 基本信息
        ws["A3"] = "分析时间"
        ws["B3"] = data.get("analysis_time", "")
        ws["A4"] = "放电类型(参考结果)"
        dc = data.get("discharge_classification", {})
        ref = dc.get("reference_result", {}) if isinstance(dc, dict) else {}
        ws["B4"] = ref.get("name", dc.get("name", "") if isinstance(dc, dict) else "")
        ws["A5"] = "置信度(占比)"
        ws["B5"] = f'{ref.get("confidence", dc.get("confidence", 0)):.0%}' if isinstance(dc, dict) else "0%"
        ws["A6"] = "严重等级"
        ws["B6"] = dc.get("severity", "") if isinstance(dc, dict) else ""
        ws["A7"] = "总周期数"
        ws["B7"] = str(dc.get("total_samples", "")) if isinstance(dc, dict) else ""

        # 统计
        stats = data.get("statistics", {})
        ws["A9"] = "统计指标"
        ws["A9"].font = openpyxl.styles.Font(bold=True)
        ws["A10"] = "总事件数"
        ws["B10"] = stats.get("total_events", 0)
        ws["A11"] = "最大幅值"
        ws["B11"] = stats.get("max_amplitude", 0)
        ws["A12"] = "平均幅值"
        ws["B12"] = stats.get("avg_amplitude", 0)
        ws["A13"] = "幅值中位数"
        ws["B13"] = stats.get("median_amplitude", 0)
        ws["A14"] = "幅值P90"
        ws["B14"] = stats.get("p90", 0)
        ws["A15"] = "最小幅值"
        ws["B15"] = stats.get("min_amplitude", 0)

        # 各类型统计
        if isinstance(dc, dict):
            type_counts = dc.get("type_counts", {})
            type_ratios = dc.get("type_ratios", {})
            row = 17
            ws[f"A{row}"] = "放电类型统计"
            ws[f"A{row}"].font = openpyxl.styles.Font(bold=True)
            row += 1
            for t, cnt in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
                pattern = DischargeClassifier.PATTERNS.get(t, {})
                ws[f"A{row}"] = pattern.get("name", t)
                ws[f"B{row}"] = str(cnt)
                ws[f"C{row}"] = f"{type_ratios.get(t, 0):.1%}"
                row += 1

        wb.save(path)
        return path

    @staticmethod
    def export_word(data: dict, path: str, chart_path: Optional[str] = None) -> str:
        """导出 Word 报告（含 PRPD 截图）"""
        try:
            from docx import Document
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.shared import Cm, Inches, Pt, RGBColor
        except ImportError:
            raise ImportError("需要安装 python-docx: pip install python-docx")

        doc = Document()

        # 标题
        title = doc.add_heading("PRPD 局部放电分析报告", level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # 基本信息
        doc.add_heading("1. 基本信息", level=1)
        tbl = doc.add_table(rows=5, cols=2)
        tbl.style = "Light Shading Accent 1"
        items = [
            ("分析时间", data.get("analysis_time", "")),
            ("数据文件", data.get("file_name", "")),
            ("总事件数", str(data.get("statistics", {}).get("total_events", 0))),
            ("放电类型", data.get("discharge_classification", {}).get("name", "")),
            ("严重等级", data.get("discharge_classification", {}).get("severity", "")),
        ]
        for i, (k, v) in enumerate(items):
            tbl.cell(i, 0).text = k
            tbl.cell(i, 1).text = v

        # PRPD 图谱截图
        if chart_path and os.path.exists(chart_path):
            doc.add_heading("2. PRPD 图谱", level=1)
            doc.add_picture(chart_path, width=Inches(5.5))

        # 分类结果
        dc = data.get("discharge_classification", {})
        doc.add_heading("3. 放电类型分析（按周期统计）", level=1)
        if isinstance(dc, dict):
            ref = dc.get("reference_result", {})
            doc.add_paragraph(f"参考结果: {ref.get('name', dc.get('name', ''))} ({ref.get('name_en', '')}) [标记为参考结果]")
            doc.add_paragraph(f"置信度(占比): {ref.get('confidence', dc.get('confidence', 0)):.0%}")
            doc.add_paragraph(f"采样次数: {dc.get('total_samples', 0)}")
            doc.add_paragraph(f"严重等级: {dc.get('severity', 'normal')}")

            # 各类型统计表格
            type_counts = dc.get("type_counts", {})
            type_ratios = dc.get("type_ratios", {})
            if type_counts:
                tbl3 = doc.add_table(rows=len(type_counts)+1, cols=3)
                tbl3.style = "Light Shading Accent 1"
                tbl3.cell(0, 0).text = "放电类型"
                tbl3.cell(0, 1).text = "出现次数"
                tbl3.cell(0, 2).text = "占比"
                for i, (t, cnt) in enumerate(sorted(type_counts.items(), key=lambda x: x[1], reverse=True), 1):
                    pattern = DischargeClassifier.PATTERNS.get(t, {})
                    tbl3.cell(i, 0).text = pattern.get("name", t)
                    tbl3.cell(i, 1).text = str(cnt)
                    tbl3.cell(i, 2).text = f"{type_ratios.get(t, 0):.1%}"
        else:
            doc.add_paragraph(f"识别结果: {dc.get('name', '')}")
            doc.add_paragraph(f"置信度: {dc.get('confidence', 0):.0%}")

        # 统计指标
        stats = data.get("statistics", {})
        doc.add_heading("4. 统计指标", level=1)
        tbl2 = doc.add_table(rows=8, cols=2)
        tbl2.style = "Light Shading Accent 1"
        stat_items = [
            ("总事件数", str(stats.get("total_events", 0))),
            ("最大幅值", f'{stats.get("max_amplitude", 0):.1f}'),
            ("最小幅值", f'{stats.get("min_amplitude", 0):.1f}'),
            ("平均幅值", f'{stats.get("avg_amplitude", 0):.1f}'),
            ("幅值中位数", f'{stats.get("median_amplitude", 0):.1f}'),
            ("幅值P90", f'{stats.get("p90", 0):.1f}'),
        ]
        for i, (k, v) in enumerate(stat_items):
            tbl2.cell(i, 0).text = k
            tbl2.cell(i, 1).text = v

        doc.save(path)
        return path


# ═══════════════════════════════════════════════════════
# 数据分析页面
# ═══════════════════════════════════════════════════════

# 等级颜色
LEVEL_COLORS = {
    "critical": DT.C.STATUS_ERROR,
    "warning": DT.C.STATUS_WARNING,
    "attention": DT.C.ACCENT_PRIMARY,
    "normal": DT.C.STATUS_SUCCESS,
}
LEVEL_NAMES = {
    "critical": "严重",
    "warning": "警告",
    "attention": "注意",
    "normal": "正常",
}


class AnalysisPage(QWidget):
    """数据分析页面"""

    def __init__(self, parent: QWidget = None):
        super().__init__(parent)
        self.setObjectName("analysisPage")
        self.setAcceptDrops(True)

        self._phases: Optional[np.ndarray] = None
        self._amplitudes: Optional[np.ndarray] = None
        self._metadata: dict = {}
        self._analysis_result: dict = {}
        self._threshold_min = 0

        self._setup_ui()

    # ── UI 构建 ───────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(DT.S.XL, DT.S.LG, DT.S.XL, DT.S.LG)
        layout.setSpacing(DT.S.MD)

        self._build_header(layout)
        self._build_import_bar(layout)
        self._build_content(layout)

    def _build_header(self, layout: QVBoxLayout) -> None:
        h = QHBoxLayout()
        title = QLabel("数据分析")
        title.setFont(DT.T.get_font(*DT.T.TITLE_XLARGE[:2], "Bold"))
        title.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        h.addWidget(title)
        h.addStretch()
        self._status_label = QLabel("就绪")
        self._status_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px;")
        h.addWidget(self._status_label)
        layout.addLayout(h)

    def _build_import_bar(self, layout: QVBoxLayout) -> None:
        bar = QFrame()
        bar.setObjectName("cardContainer")
        bar.setStyleSheet(DT.sheet.sheet_card() + f"""
            QFrame#cardContainer {{
                border: 2px dashed {DT.C.BORDER_DEFAULT};
            }}
            QFrame#cardContainer:hover {{
                border-color: {DT.C.ACCENT_PRIMARY};
            }}
        """)
        bar.setFixedHeight(80)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(DT.S.LG, DT.S.MD, DT.S.LG, DT.S.MD)

        btn_import = QPushButton("选择文件")
        btn_import.setFixedHeight(32)
        btn_import.setStyleSheet(
            f"""
            QPushButton {{
                background: {DT.C.ACCENT_PRIMARY}; color: white; border: none;
                border-radius: 4px; padding: 6px 18px; font-size: {DT.FS.BODY}px;
            }}
            QPushButton:hover {{ background: {DT.C.ACCENT_HOVER}; }}
        """
        )
        btn_import.clicked.connect(self._on_import_click)
        bl.addWidget(btn_import)

        # 提示文字（无文件时显示）
        self._prompt_label = QLabel("选择文件或拖放 CSV/Excel/TXT 文件到此处")
        self._prompt_label.setStyleSheet(f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY}px;")
        bl.addWidget(self._prompt_label)

        # 文件信息（有文件时显示）
        self._file_label = QLabel("")
        self._file_label.setStyleSheet(f"color: {DT.C.TEXT_SECONDARY}; font-size: {DT.FS.BODY_SMALL}px;")
        self._file_label.setVisible(False)
        bl.addWidget(self._file_label, 1)

        bl.addStretch()

        # 清除按钮
        self._clear_btn = QPushButton("清除")
        self._clear_btn.setFixedHeight(28)
        self._clear_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent; color: {DT.C.TEXT_SECONDARY};
                border: 1px solid {DT.C.BORDER_DEFAULT}; border-radius: 4px;
                padding: 4px 14px; font-size: {DT.FS.BODY_SMALL}px;
            }}
            QPushButton:hover {{ background: {DT.C.BG_HOVER}; }}
        """
        )
        self._clear_btn.clicked.connect(self._clear_analysis)
        self._clear_btn.setVisible(False)
        bl.addWidget(self._clear_btn)

        # 阈值过滤
        bl.addWidget(QLabel("最低幅值:", styleSheet=f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.BODY_SMALL}px;"))
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(0, 100000)
        self._threshold_spin.setValue(0)
        self._threshold_spin.setSuffix(" (过滤噪声)")
        self._threshold_spin.setFixedHeight(28)
        self._threshold_spin.setStyleSheet(
            f"""
            QSpinBox {{ background: {DT.C.BG_PRIMARY}; border: 1px solid {DT.C.BORDER_DEFAULT};
            border-radius: 4px; padding: 2px 6px; font-size: {DT.FS.BODY_SMALL}px; color: {DT.C.TEXT_PRIMARY}; }}
        """
        )
        self._threshold_spin.valueChanged.connect(self._on_threshold_changed)
        bl.addWidget(self._threshold_spin)

        layout.addWidget(bar)

    def _build_content(self, layout: QVBoxLayout) -> None:
        outer = QHBoxLayout()
        outer.setSpacing(DT.S.MD)

        # 左列: PRPD 图谱 + 幅值分布（上下排列，宽度一致）
        left_col = QVBoxLayout()
        left_col.setSpacing(DT.S.MD)
        self._build_prpd_panel(left_col)
        self._build_distribution_panel(left_col)

        outer.addLayout(left_col, 3)

        # 右列: 分析结果（高度撑满到幅值分布底部）
        self._build_result_panel(outer)

        layout.addLayout(outer)

    def _build_prpd_panel(self, parent: QLayout) -> None:
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        if HAS_PYQTGRAPH:
            from ui.widgets import PRPDWidget

            self._prpd = PRPDWidget(title="PRPD 图谱")
            self._prpd._mode_combo.setCurrentIndex(0)  # 默认散点图
            fl.addWidget(self._prpd)
        else:
            self._prpd = None
            fl.addWidget(QLabel("需要 pyqtgraph 支持", alignment=Qt.AlignmentFlag.AlignCenter))

        parent.addWidget(frame, 3)

    def _build_result_panel(self, parent: QHBoxLayout) -> None:
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        # ── 标题栏 ──
        lbl = QLabel("分析结果")
        lbl.setFont(DT.T.get_font(*DT.T.TITLE_MEDIUM[:2], "SemiBold"))
        lbl.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        fl.addWidget(lbl)

        # ── 参考结果卡片 ──
        self._ref_frame = QFrame()
        self._ref_frame.setStyleSheet(
            f"QFrame {{ background: {DT.C.BG_SECONDARY}; border-radius: {DT.R.MD}px; }}"
        )
        rfl = QHBoxLayout(self._ref_frame)
        rfl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
        self._ref_type_label = QLabel("—")
        self._ref_type_label.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "Bold"))
        self._ref_type_label.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        self._ref_mark_label = QLabel("[参考结果]")
        self._ref_mark_label.setStyleSheet(
            f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION_SMALL}px; background: transparent;"
        )
        rfl.addWidget(self._ref_type_label)
        rfl.addWidget(self._ref_mark_label)
        rfl.addStretch()
        self._ref_conf_label = QLabel("—")
        self._ref_conf_label.setStyleSheet(
            f"color: {DT.C.TEXT_SECONDARY}; font-size: {DT.FS.BODY_SMALL}px; background: transparent;"
        )
        rfl.addWidget(self._ref_conf_label)
        fl.addWidget(self._ref_frame)

        # ── 严重等级 ──
        self._sev_card = self._make_result_card("严重等级", "—")
        fl.addWidget(self._sev_card)

        # ── 采样次数 ──
        self._cycle_card = self._make_result_card("采样次数", "—")
        fl.addWidget(self._cycle_card)

        fl.addWidget(QLabel("", styleSheet=f"color: {DT.C.DIVIDER}; max-height: 1px;"))

        # ── 各放电类型统计表 ──
        self._type_table = QTableWidget()
        self._type_table.setColumnCount(4)
        self._type_table.setHorizontalHeaderLabels(["放电类型", "出现次数", "占比", "说明"])
        self._type_table.setAlternatingRowColors(True)
        self._type_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._type_table.verticalHeader().setVisible(False)
        self._type_table.setStyleSheet(
            f"""
            QTableWidget {{
                background: transparent; border: none; font-size: {DT.FS.BODY_SMALL}px;
            }}
            QTableWidget::item {{ padding: 3px 6px; }}
            QHeaderView::section {{
                background: {DT.C.BG_SECONDARY}; color: {DT.C.TEXT_SECONDARY};
                border: none; font-size: {DT.FS.CAPTION}px; font-weight: 600;
            }}
        """
        )
        self._type_table.horizontalHeader().setStretchLastSection(True)
        fl.addWidget(self._type_table)

        fl.addWidget(QLabel("", styleSheet=f"color: {DT.C.DIVIDER}; max-height: 1px;"))

        # ── 幅值统计表 ──
        self._stat_table = QTableWidget()
        self._stat_table.setColumnCount(2)
        self._stat_table.setHorizontalHeaderLabels(["指标", "值"])
        self._stat_table.setAlternatingRowColors(True)
        self._stat_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._stat_table.verticalHeader().setVisible(False)
        self._stat_table.setStyleSheet(
            f"""
            QTableWidget {{
                background: transparent; border: none; font-size: {DT.FS.BODY_SMALL}px;
            }}
            QTableWidget::item {{ padding: 3px 6px; }}
            QHeaderView::section {{
                background: {DT.C.BG_SECONDARY}; color: {DT.C.TEXT_SECONDARY};
                border: none; font-size: {DT.FS.CAPTION}px; font-weight: 600;
            }}
        """
        )
        self._stat_table.horizontalHeader().setStretchLastSection(True)
        fl.addWidget(self._stat_table, 1)

        # 导出按钮
        btn_row = QHBoxLayout()
        for text, slot in [
            ("导出 JSON", self._export_json),
            ("导出 Excel", self._export_excel),
            ("导出 Word", self._export_word),
        ]:
            btn = QPushButton(text)
            btn.setFixedHeight(28)
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: transparent; color: {DT.C.TEXT_SECONDARY};
                    border: 1px solid {DT.C.BORDER_DEFAULT}; border-radius: 4px;
                    padding: 3px 10px; font-size: {DT.FS.CAPTION}px;
                }}
                QPushButton:hover {{ background: {DT.C.BG_HOVER}; }}
            """
            )
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        fl.addLayout(btn_row)

        parent.addWidget(frame, 2)

    def _build_distribution_panel(self, layout: QVBoxLayout) -> None:
        frame = QFrame()
        frame.setObjectName("cardContainer")
        frame.setStyleSheet(DT.sheet.sheet_card())
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)

        lbl = QLabel("幅值分布")
        lbl.setFont(DT.T.get_font(*DT.T.TITLE_SMALL[:2], "SemiBold"))
        lbl.setStyleSheet(f"color: {DT.C.TEXT_PRIMARY};")
        fl.addWidget(lbl)

        if HAS_PYQTGRAPH:
            self._dist_plot = pg.PlotWidget()
            self._dist_plot.setMinimumHeight(100)
            self._dist_plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._dist_plot.showGrid(x=True, y=True, alpha=0.2)
            self._dist_plot.setLabel("left", "事件数")
            self._dist_plot.setLabel("bottom", "幅值")
            fl.addWidget(self._dist_plot)
        else:
            self._dist_plot = None

        layout.addWidget(frame, 1)

    @staticmethod
    def _make_result_card(title: str, value: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background: {DT.C.BG_SECONDARY}; border-radius: {DT.R.MD}px; }}")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(DT.S.MD, DT.S.SM, DT.S.MD, DT.S.SM)
        cl.addWidget(QLabel(title, styleSheet=f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px;"))
        cl.addWidget(QLabel(value, styleSheet=f"color: {DT.C.TEXT_PRIMARY}; font-size: {DT.FS.DATA_SMALL}px; font-weight: 600;"))
        return card

    # ── 导入 ─────────────────────────────────────────

    def _on_import_click(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 PRPD 数据文件",
            "",
            "数据文件 (*.csv *.xlsx *.xls *.txt);;CSV (*.csv);;Excel (*.xlsx *.xls);;TXT (*.txt)",
        )
        if path:
            self._import_file(path)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in DataImporter.SUPPORTED_EXTENSIONS:
                self._import_file(path)
                break

    def _import_file(self, path: str) -> None:
        try:
            fmt, phases, amplitudes, cycles, meta = DataImporter.load(path)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            self._status_label.setText(f"导入失败: {e}")
            return

        if len(phases) == 0:
            QMessageBox.warning(
                self, "导入失败", "未能从文件中解析到有效数据。\n请确保文件包含相位(0-360°)和幅值两列数据。"
            )
            return

        self._raw_phases = phases
        self._raw_amplitudes = amplitudes
        self._raw_cycles = cycles
        self._metadata = meta
        self._file_path = path

        # 切换显示: 隐藏提示，显示文件信息
        self._prompt_label.setVisible(False)
        self._file_label.setText(f" {Path(path).name} — {len(phases)} 条")
        self._file_label.setVisible(True)
        self._clear_btn.setVisible(True)
        self._status_label.setText(f"已加载 {len(phases)} 条数据")

        # 自动设置阈值建议（P5 作为底噪估计）
        suggested = int(np.percentile(amplitudes, 5))
        self._threshold_spin.setValue(suggested)
        self._threshold_min = suggested

        self._run_analysis()

    def _on_threshold_changed(self, val: int) -> None:
        self._threshold_min = val
        if hasattr(self, "_raw_phases") and len(self._raw_phases) > 0:
            self._run_analysis()

    def _clear_analysis(self) -> None:
        """清除已加载的文件和分析结果，恢复到默认状态"""
        self._raw_phases = np.array([])
        self._raw_amplitudes = np.array([])
        self._raw_cycles = np.array([], dtype=np.int32)
        self._metadata = {}
        self._analysis_result = {}
        self._file_path = ""
        self._threshold_spin.setValue(0)
        self._threshold_min = 0

        # 恢复提示文字，隐藏文件信息和清除按钮
        self._prompt_label.setVisible(True)
        self._file_label.setVisible(False)
        self._file_label.setText("")
        self._clear_btn.setVisible(False)

        # 清除 PRPD 图谱
        if HAS_PYQTGRAPH and self._prpd is not None:
            self._prpd._on_reset()

        # 清除分析结果面板
        self._ref_type_label.setText("—")
        self._ref_conf_label.setText("—")
        self._update_card(self._sev_card, "严重等级", "—", DT.C.TEXT_TERTIARY)
        self._update_card(self._cycle_card, "采样次数", "—", DT.C.TEXT_TERTIARY)
        self._type_table.setRowCount(0)
        self._stat_table.setRowCount(0)

        # 清除幅值分布图
        if HAS_PYQTGRAPH and self._dist_plot is not None:
            self._dist_plot.clear()

        self._status_label.setText("就绪")

    # ── 分析 ─────────────────────────────────────────

    def _run_analysis(self) -> None:
        phases = self._raw_phases
        amps = self._raw_amplitudes
        # 阈值过滤
        mask = amps >= self._threshold_min
        phases = phases[mask]
        amps = amps[mask]
        cycles = self._raw_cycles[mask] if hasattr(self, "_raw_cycles") and self._raw_cycles.size == len(phases) else np.zeros(len(phases), dtype=np.int32)

        if len(phases) < 5:
            self._status_label.setText("过滤后数据不足，请降低阈值")
            return

        # 幅值统计（用于 PRPD 控件参数）
        stats = {
            "total_events": int(len(phases)),
            "max_amplitude": float(np.max(amps)),
            "min_amplitude": float(np.min(amps)),
            "avg_amplitude": float(np.mean(amps)),
            "median_amplitude": float(np.median(amps)),
            "p90": float(np.percentile(amps, 90)),
            "concentration": float(np.max(np.histogram(phases, bins=36, range=(0, 360))[0]) / len(phases)),
        }

        # 放电类型分类（Bootstrap 多次采样，统计各类型占比）
        dc_result = DischargeClassifier.classify_cycles(phases, amps)

        self._analysis_result = {
            "file_name": Path(self._file_path).name if hasattr(self, "_file_path") else "",
            "analysis_time": datetime.now().isoformat(),
            "statistics": dc_result.get("amplitude_stats", stats),
            "discharge_classification": dc_result,
            "threshold": self._threshold_min,
        }

        # 更新 PRPD 图谱（热力图 + 散点数据）
        if HAS_PYQTGRAPH and self._prpd is not None:
            from core.processing import PRPDProcessor
            from core.processing.prpd_processor import PRPDMode

            prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=stats["max_amplitude"] * 1.1)
            for p, a in zip(phases, amps):
                prpd.add_event(phase=float(p), amplitude=float(a))
            prpd_result = prpd.compute(mode=PRPDMode.HEATMAP)
            effective_max = stats.get("max_amplitude", 0) * 1.1
            if effective_max > 0:
                self._prpd.set_max_amplitude(effective_max)
            self._prpd.update_scatter(phases.tolist(), amps.tolist())
            self._prpd.update_heatmap(prpd_result.matrix, 360, 256)

        # 更新分析结果面板（新格式）
        self._update_result_display(dc_result, stats)

        # 更新幅值分布
        self._update_distribution(amps)

        self._status_label.setText(f"分析完成 — {len(phases)} 条事件, {dc_result.get('total_samples', 0)} 次采样")

    def _update_result_display(self, dc_result: dict, stats: dict) -> None:
        """更新分析结果面板（按周期统计格式）"""

        # ── 参考结果 ──
        ref = dc_result.get("reference_result", {})
        ref_name = ref.get("name", "—")
        ref_conf = ref.get("confidence", 0)
        self._ref_type_label.setText(ref_name)
        self._ref_conf_label.setText(f"置信度 {ref_conf:.0%}")

        # ── 严重等级 ──
        sev = dc_result.get("severity", "normal")
        self._update_card(
            self._sev_card, "严重等级",
            LEVEL_NAMES.get(sev, "—"), LEVEL_COLORS.get(sev, DT.C.TEXT_SECONDARY),
        )

        # ── 采样次数 ──
        total_samples = dc_result.get("total_samples", 0)
        self._update_card(self._cycle_card, "采样次数", str(total_samples), DT.C.TEXT_PRIMARY)

        # ── 各放电类型统计表 ──
        type_counts = dc_result.get("type_counts", {})
        type_ratios = dc_result.get("type_ratios", {})
        sorted_types = sorted(type_counts.keys(), key=lambda t: type_counts[t], reverse=True)

        self._type_table.setRowCount(len(sorted_types))
        for i, t in enumerate(sorted_types):
            count = type_counts[t]
            ratio = type_ratios.get(t, 0)
            pattern = DischargeClassifier.PATTERNS.get(t, {})
            is_noise = t == "noise"

            # 类型名称
            display_name = "随机噪声" if is_noise else pattern.get("name", t)
            name_item = QTableWidgetItem(display_name)
            name_item.setForeground(QColor(DT.C.TEXT_TERTIARY) if is_noise else (QColor(DT.C.ACCENT_PRIMARY) if t == ref.get("type") else QColor(DT.C.TEXT_PRIMARY)))
            self._type_table.setItem(i, 0, name_item)

            # 出现次数
            cnt_item = QTableWidgetItem(str(count))
            cnt_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_noise:
                cnt_item.setForeground(QColor(DT.C.TEXT_TERTIARY))
            self._type_table.setItem(i, 1, cnt_item)

            # 占比
            ratio_item = QTableWidgetItem(f"{ratio:.1%}")
            ratio_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if is_noise:
                ratio_item.setForeground(QColor(DT.C.TEXT_TERTIARY))
            self._type_table.setItem(i, 2, ratio_item)

            # 说明
            noise_desc = "信号为随机噪声，无明显放电特征" if is_noise else pattern.get("desc", "")[:40]
            desc_item = QTableWidgetItem(noise_desc)
            desc_item.setForeground(QColor(DT.C.TEXT_TERTIARY))
            self._type_table.setItem(i, 3, desc_item)

        # ── 幅值统计表 ──
        amp_stats = dc_result.get("amplitude_stats", stats)
        stat_items = [
            ("总事件数", str(amp_stats.get("total_events", 0))),
            ("最大幅值", f'{amp_stats.get("max_amplitude", 0):.1f} mV'),
            ("最小幅值", f'{amp_stats.get("min_amplitude", 0):.1f} mV'),
            ("平均幅值", f'{amp_stats.get("avg_amplitude", 0):.1f} mV'),
            ("幅值中位数", f'{amp_stats.get("median_amplitude", 0):.1f} mV'),
            ("幅值 P90", f'{amp_stats.get("p90", 0):.1f} mV'),
        ]
        self._stat_table.setRowCount(len(stat_items))
        for i, (k, v) in enumerate(stat_items):
            self._stat_table.setItem(i, 0, QTableWidgetItem(k))
            val_item = QTableWidgetItem(v)
            val_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._stat_table.setItem(i, 1, val_item)

    def _update_card(self, card: QFrame, title: str, value: str, color: str) -> None:
        cl = card.layout()
        # 删除旧标签重新添加
        while cl.count():
            item = cl.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cl.addWidget(QLabel(title, styleSheet=f"color: {DT.C.TEXT_TERTIARY}; font-size: {DT.FS.CAPTION}px;"))
        cl.addWidget(QLabel(value, styleSheet=f"color: {color}; font-size: {DT.FS.TITLE_MEDIUM}px; font-weight: 700;"))

    def _update_distribution(self, amps: np.ndarray) -> None:
        if not HAS_PYQTGRAPH or self._dist_plot is None:
            return

        self._dist_plot.clear()
        if len(amps) < 2:
            return
        # 自动分箱
        n_bins = min(50, len(amps) // 10)
        n_bins = max(10, n_bins)
        y, x = np.histogram(amps, bins=n_bins)
        # 柱状图
        bg = pg.BarGraphItem(x0=x[:-1], height=y, width=(x[1] - x[0]) * 0.9, brush=QColor(DT.C.ACCENT_PRIMARY))
        self._dist_plot.addItem(bg)

    # ── 导出 ─────────────────────────────────────────

    def _export_json(self) -> None:
        if not self._analysis_result:
            QMessageBox.information(self, "提示", "请先导入数据并完成分析")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 JSON", "prpd_report.json", "JSON (*.json)")
        if path:
            ReportExporter.export_json(self._analysis_result, path)
            self._status_label.setText(f"报告已导出: {path}")

    def _export_excel(self) -> None:
        if not self._analysis_result:
            QMessageBox.information(self, "提示", "请先导入数据并完成分析")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 Excel", "prpd_report.xlsx", "Excel (*.xlsx)")
        if path:
            try:
                ReportExporter.export_excel(self._analysis_result, path)
                self._status_label.setText(f"Excel 已导出: {path}")
            except ImportError as e:
                QMessageBox.warning(self, "导出失败", str(e))

    def _export_word(self) -> None:
        if not self._analysis_result:
            QMessageBox.information(self, "提示", "请先导入数据并完成分析")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 Word", "prpd_report.docx", "Word (*.docx)")
        if path:
            try:
                # 先尝试截图 PRPD 图谱
                chart_path = None
                if HAS_PYQTGRAPH and self._prpd is not None:
                    try:
                        import io

                        # 保存 PRPD 控件截图
                        pixmap = self._prpd.grab()
                        chart_dir = Path(path).parent / ".charts"
                        chart_dir.mkdir(exist_ok=True)
                        chart_path = str(chart_dir / "prpd_chart.png")
                        pixmap.save(chart_path)
                    except Exception:
                        chart_path = None

                ReportExporter.export_word(self._analysis_result, path, chart_path)
                self._status_label.setText(f"Word 已导出: {path}")
            except ImportError as e:
                QMessageBox.warning(self, "导出失败", str(e))
            except Exception as e:
                QMessageBox.warning(self, "导出失败", f"报告导出异常: {e}")
