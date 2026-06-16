# -*- coding: utf-8 -*-
"""PRPD 放电类型分类器 — 统计特征参考剖面匹配"""
from __future__ import annotations
from typing import Optional
import numpy as np


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


