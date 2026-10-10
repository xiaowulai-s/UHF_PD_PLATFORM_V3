# -*- coding: utf-8 -*-
"""`core/protocol/data_rpc.py` 的行为测试。

这是把旧 ui/ 的"实验数据导入"接回前端的唯一通路（data.import_file），
测的重点不是 DataImporter 本身（它有自己的测试），而是 RPC 这一层新加的口径：
截断要回报、非有限值要丢弃并计数、CSV 被列映射过滤的行数要上报、分类失败不能连累数据。
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from core.protocol.data_rpc import MAX_POINTS, DataRpc

SAMPLE = Path(__file__).parent / "PD_data.csv"


@pytest.fixture(scope="module")
def imported() -> dict:
    return DataRpc().data_import_file({"path": str(SAMPLE)})


class TestImportFile:
    """样例 CSV 的完整导入。"""

    def test_shape_and_identity(self, imported: dict) -> None:
        assert imported["format"] == "phase_amplitude"
        assert imported["file_name"] == "PD_data.csv"
        assert imported["total_points"] > 0
        assert len(imported["phases"]) == imported["returned_points"]
        assert len(imported["amplitudes"]) == imported["returned_points"]

    def test_arrays_are_plain_python(self, imported: dict) -> None:
        """msgpack 不能序列化 np.float64，必须已是 Python 标量。"""
        assert all(isinstance(x, float) for x in imported["phases"][:20])
        assert all(isinstance(x, float) for x in imported["amplitudes"][:20])

    def test_filtered_rows_reported(self, imported: dict) -> None:
        """样例 CSV 6657 数据行，列映射过滤后剩下的行数差必须如实上报。"""
        assert imported["source_rows"] == 6657
        assert imported["filtered_rows"] == imported["source_rows"] - imported["total_points"]
        assert imported["filtered_rows"] >= 0

    def test_columns_detected(self, imported: dict) -> None:
        assert "phase_deg" in (imported["columns"] or [])

    def test_validation_report(self, imported: dict) -> None:
        v = imported["validation"]
        assert v["valid"] is True
        assert v["total_events"] == imported["total_points"]

    def test_classification_present(self, imported: dict) -> None:
        c = imported["classification"]
        assert c is not None
        assert imported["classification_error"] is None
        assert c["type"]
        assert c["name"]
        assert isinstance(c["confidence"], float)
        assert 0 <= c["confidence"] <= 1

    def test_type_distribution_from_cycles(self, imported: dict) -> None:
        """样例带 cycle 列 → 逐周期分类给出类型分布（设计稿"放电类型分布"的数据源）。"""
        d = imported["distribution"]
        assert d is not None
        assert imported["distribution_error"] is None
        assert d["total_samples"] > 0
        assert isinstance(d["type_counts"], dict) and d["type_counts"]
        assert isinstance(d["type_ratios"], dict) and d["type_ratios"]
        assert isinstance(d["cycle_types"], dict)
        assert len(d["cycle_types"]) == d["total_samples"]
        assert set(d["cycle_types"]).issubset({str(c) for c in imported["cycles"]})
        assert set(d["cycle_types"].values()).issubset(set(d["type_counts"]))
        # 占比加起来应当接近 1（各类型次数 / 总周期数）
        assert abs(sum(d["type_ratios"].values()) - 1.0) < 1e-6
        assert "per_sample_results" not in d  # 大字段必须裁掉

    def test_no_truncation_on_small_file(self, imported: dict) -> None:
        assert imported["truncated"] == 0
        assert imported["nonfinite_dropped"] == 0


class TestTruncationAndBadValues:
    """超限截断与非有限值丢弃。"""

    def test_truncated_when_over_limit(self, tmp_path: Path) -> None:
        p = tmp_path / "big.csv"
        n = MAX_POINTS + 5
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["phase_deg", "amplitude", "cycle"])
            for i in range(n):
                w.writerow([round((i % 360) + 0.5, 3), round(10 + (i % 50), 3), i])
        out = DataRpc().data_import_file({"path": str(p)})
        assert out["total_points"] == n
        assert out["returned_points"] == MAX_POINTS
        assert out["truncated"] == 5
        assert len(out["phases"]) == MAX_POINTS

    def test_csv_nan_rows_dropped_by_loader(self, tmp_path: Path) -> None:
        """CSV 路径：加载器的范围过滤（0<=p<=360 and a>=0）对 NaN 的比较全是 False，
        所以 NaN 行在 DataImporter 里就被丢了，到不了 RPC 层——nonfinite_dropped 恒为 0。
        这是对现状的如实固定，别误改成"能计数"。"""
        p = tmp_path / "nan.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["phase_deg", "amplitude"])
            for i in range(30):
                w.writerow([float(i * 12), float(i)])
            w.writerow(["10", "nan"])  # 幅值非有限
            w.writerow(["nan", "5"])  # 相位非有限
        out = DataRpc().data_import_file({"path": str(p)})
        assert out["nonfinite_dropped"] == 0
        assert out["returned_points"] == 30
        assert out["filtered_rows"] == 2  # 被加载器过滤的行数如实上报

    def test_npy_nonfinite_dropped_and_counted(self, tmp_path: Path) -> None:
        """npy 路径没有范围过滤，NaN 会到 RPC 层——守卫真正服务的就是这条路径。"""
        p = tmp_path / "nan.npy"
        arr = np.zeros((32, 2), dtype=np.float64)
        arr[:, 0] = [float(i * 11) % 360 for i in range(32)]
        arr[:, 1] = [5.0 + (i % 20) for i in range(32)]
        arr[30, 1] = np.nan  # 幅值非有限
        arr[31, 0] = np.nan  # 相位非有限
        np.save(p, arr)
        out = DataRpc().data_import_file({"path": str(p)})
        assert out["nonfinite_dropped"] == 2
        assert out["returned_points"] == 30
        assert not any(x != x for x in out["phases"])  # NaN 判别：x != x
        assert not any(x != x for x in out["amplitudes"])

    def test_classification_failure_does_not_kill_data(self, tmp_path: Path, monkeypatch) -> None:
        """分类炸了要带回错误并把数据完整还给前端，不能整次导入作废。"""
        p = tmp_path / "ok.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["phase_deg", "amplitude"])
            for i in range(50):
                w.writerow([float(i * 7 % 360), float(5 + i % 20)])

        from core.protocol import data_rpc

        def boom(*_a, **_k):
            raise RuntimeError("分类器炸了")

        monkeypatch.setattr(data_rpc.DischargeClassifier, "classify", classmethod(boom))
        out = data_rpc.DataRpc().data_import_file({"path": str(p)})
        assert out["classification"] is None
        assert "RuntimeError" in (out["classification_error"] or "")
        assert out["returned_points"] == 50  # 数据还在


class TestInputValidation:
    def test_missing_path(self) -> None:
        with pytest.raises(ValueError, match="缺少 path"):
            DataRpc().data_import_file({})

    def test_nonexistent_file(self) -> None:
        with pytest.raises(ValueError, match="文件不存在"):
            DataRpc().data_import_file({"path": "Z:/no/such/file.csv"})

    def test_unsupported_suffix(self, tmp_path: Path) -> None:
        p = tmp_path / "x.docx"
        p.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError, match="不支持的文件格式"):
            DataRpc().data_import_file({"path": str(p)})
