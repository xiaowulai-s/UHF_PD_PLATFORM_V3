# -*- coding: utf-8 -*-
"""仿真器四种放电类型的相位/幅值模型必须能被 `DischargeClassifier` 区分开。

回归的是一个真实故障：原先每种类型只给**一个窄相位窗**（corona 270~330、
surface 30~90、internal 0~60、floating 全相位），四种类型在分类器眼里长得一样
（top2≈0.99、cv≈0.3），实测四种 `--pd-type` **全部**被判成"沿面放电"——
`discharge_type` 标签因此等于没有。

这里钉的是**方向性**（各扇区占比、cv 的量级）与**可区分性**，不是精确数值：
分类器是统计剖面匹配，换标定就会变，写死数值的测试只会在标定更新时误报。
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from core.data.discharge_classifier import DischargeClassifier
from core.utils.simulator import PDSimulator

TYPES = ("corona", "internal", "surface", "floating")
SEED = 20261012
SAMPLES = 3000


def _sample(pd_type: str, n: int = SAMPLES) -> tuple[np.ndarray, np.ndarray]:
    """按模型抽 n 个脉冲的 (相位, 幅值)。

    直接复用 `PD_TYPES` 的窗口与 `_sample_amplitude`，而不是走 `generate_waveform()`：
    后者每帧只落 0~20 个脉冲（4096 点 @100MHz 只有 41μs），要凑够统计量得跑几千帧，
    而这里要验的正是"窗口与幅值分布"本身。
    """
    cfg = PDSimulator.PD_TYPES[pd_type]
    weights = [w[2] for w in cfg["phase_windows"]]
    phases, amps = [], []
    for _ in range(n):
        start, end, _weight, amp_scale = random.choices(cfg["phase_windows"], weights=weights, k=1)[0]
        phases.append(random.uniform(start, end))
        amps.append(PDSimulator._sample_amplitude(cfg) * amp_scale)
    return np.array(phases, dtype=np.float64), np.array(amps, dtype=np.float64)


@pytest.fixture
def seeded():
    """固定随机种子，并保证退出时恢复全局 RNG 状态（不让别的测试受影响）。"""
    state = random.getstate()
    random.seed(SEED)
    yield
    random.setstate(state)


def _features(pd_type: str) -> dict:
    phases, amps = _sample(pd_type)
    feat = DischargeClassifier._extract_features(phases, amps)
    assert feat, f"{pd_type} 的特征提取返回空"
    return feat


class TestModelShape:
    def test_every_type_is_two_sided(self, seeded):
        """任何类型都不能把脉冲全塞进半周——那正是原故障的形态。

        主判据写在**模型窗口**上而不是去噪后的特征上：`_extract_features` 会按
        `median×2` 去噪，而 corona 的正半周幅值本来就更小（pos_energy 的设定），
        去噪后正半周占比自然低于窗口权重——那不是缺陷，正是"负半周能量占优"。
        """
        for pd_type in TYPES:
            windows = PDSimulator.PD_TYPES[pd_type]["phase_windows"]
            positive = sum(w for s, _e, w, _a in windows if s < 180)
            negative = sum(w for s, e, w, _a in windows if e > 180)
            assert positive > 0.15, f"{pd_type} 正半周窗口权重只有 {positive:.2f}"
            assert negative > 0.15, f"{pd_type} 负半周窗口权重只有 {negative:.2f}"

            # 去噪之后两侧也仍要有残留，否则"双侧"在分类器眼里等于不存在
            feat = _features(pd_type)
            assert feat["S1"] + feat["S2"] + feat["S3"] > 0.05, pd_type
            assert feat["S4"] + feat["S5"] + feat["S6"] > 0.05, pd_type

    def test_windows_cover_the_cycle_and_have_multiple_peaks(self):
        for pd_type in TYPES:
            windows = PDSimulator.PD_TYPES[pd_type]["phase_windows"]
            assert sum(w[2] for w in windows) == pytest.approx(1.0, abs=0.01), pd_type
            meaningful = [w for w in windows if w[2] >= 0.05]
            assert len(meaningful) >= 2, f"{pd_type} 只有一个有效相位窗，等于原故障"
            for start, end, _weight, amp_scale in windows:
                assert 0 <= start < end <= 360, pd_type
                assert amp_scale > 0, pd_type

    def test_sector_shares_follow_the_documented_physics(self, seeded):
        corona = _features("corona")
        internal = _features("internal")
        surface = _features("surface")
        floating = _features("floating")

        # 电晕：相位宽、负半周能量占绝对优势
        assert corona["spread"] > 0.5
        assert corona["pos_energy"] < 0.35
        # 内部气隙：第一/三象限集中，且 S1 > S4
        assert internal["quad13"] > 0.6
        assert internal["s1_s4_ratio"] > 1.2
        # 沿面：集中度更高，且 S1 ≈ S4
        assert surface["quad13"] > internal["quad13"]
        assert surface["s1_s4_ratio"] == pytest.approx(1.0, abs=0.15)
        # 悬浮：相位宽 + 幅值分散
        assert floating["spread"] > 0.5
        assert floating["cv"] > internal["cv"]

    def test_no_type_falls_into_the_noise_gate(self, seeded):
        """任何类型都不能被判成"纯噪声"。

        噪声闸的条件是 `top2 < 0.40 且 max_amp < noise_threshold*5`，只有相位较宽的
        （corona / floating）才会碰到第一个条件，所以幅值上限只需对它们够高。
        踩过：floating 的 cap 取 150 时 max_amp=150 < 阈值×5≈195 → 判成"纯噪声"，
        而那是被自己的上限压进去的，不是模型不对。
        """
        for pd_type in TYPES:
            feat = _features(pd_type)
            if feat["top2"] >= 0.40:
                continue
            assert feat["max_amp"] >= feat["noise_threshold"] * 5, (
                f"{pd_type} 会被噪声闸拦下：max_amp={feat['max_amp']:.1f} < "
                f"阈值×5={feat['noise_threshold'] * 5:.1f}"
            )


class TestClassifierRecoversType:
    def test_verdict_matches_the_requested_type(self, seeded):
        """四种类型必须各判各的——这是 `discharge_type` 标签有没有意义的前提。"""
        for pd_type in TYPES:
            phases, amps = _sample(pd_type)
            verdict = DischargeClassifier.classify(phases, amps)
            assert verdict["type"] == pd_type, (
                f"{pd_type} 被判成 {verdict['type']}（{verdict['name']}，" f"conf={verdict['confidence']:.2f}）"
            )
            assert verdict["confidence"] > 0.4, f"{pd_type} 置信度过低: {verdict['confidence']:.2f}"

    def test_types_are_mutually_distinguishable(self, seeded):
        """四种类型两两不能判成同一个——防止"都退化成同一类"再次发生。"""
        got = {}
        for pd_type in TYPES:
            phases, amps = _sample(pd_type)
            got[pd_type] = DischargeClassifier.classify(phases, amps)["type"]
        assert len(set(got.values())) == len(TYPES), f"判定重复: {got}"
