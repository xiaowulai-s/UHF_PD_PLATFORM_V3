# -*- coding: utf-8 -*-
"""DataImporter 增强功能测试"""

import json

import numpy as np
import pytest

from core.data.data_importer import DataImporter


@pytest.fixture
def sample_csv_data(tmp_path):
    """示例 CSV 文件"""
    path = tmp_path / "test.csv"
    with open(path, "w", encoding="utf-8") as f:
        f.write("phase_deg,amplitude_mv,cycle\n")
        for i in range(100):
            phase = (i * 3.6) % 360
            amp = 50 + 20 * np.sin(np.radians(phase))
            f.write(f"{phase:.1f},{amp:.1f},{i // 10}\n")
    return str(path)


@pytest.fixture
def sample_json_data(tmp_path):
    """示例 JSON 文件"""
    path = tmp_path / "test.json"
    data = []
    for i in range(50):
        data.append(
            {
                "phase": (i * 7.2) % 360,
                "amplitude": 30 + 10 * np.random.rand(),
                "cycle": i // 5,
            }
        )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return str(path)


@pytest.fixture
def sample_numpy_data(tmp_path):
    """示例 NumPy 文件"""
    path = tmp_path / "test.npy"
    phases = np.linspace(0, 360, 100)
    amplitudes = 50 + 20 * np.sin(np.radians(phases))
    data = np.column_stack([phases, amplitudes])
    np.save(path, data)
    return str(path)


class TestDataImporterEnhanced:
    """增强的数据导入测试"""

    def test_load_csv_valid(self, sample_csv_data):
        """测试 CSV 导入"""
        fmt, phases, amps, cycles, meta = DataImporter.load(sample_csv_data)
        assert fmt == "phase_amplitude"
        assert len(phases) == 100
        assert len(amps) == 100
        assert len(cycles) == 100
        assert meta["total_rows"] == 100

    def test_load_json_valid(self, sample_json_data):
        """测试 JSON 导入"""
        fmt, phases, amps, cycles, meta = DataImporter.load(sample_json_data)
        assert fmt == "phase_amplitude"
        assert len(phases) == 50
        assert len(amps) == 50
        assert meta["format"] == "json"

    def test_load_numpy_valid(self, sample_numpy_data):
        """测试 NumPy .npy 导入"""
        fmt, phases, amps, cycles, meta = DataImporter.load(sample_numpy_data)
        assert fmt == "phase_amplitude"
        assert len(phases) == 100
        assert len(amps) == 100
        assert meta["format"] == "numpy"

    def test_load_numpy_npz(self, tmp_path):
        """测试 NumPy .npz 导入"""
        path = tmp_path / "test.npz"
        phases = np.linspace(0, 360, 50)
        amplitudes = 50 + 20 * np.random.rand(50)
        np.savez(path, phases=phases, amplitudes=amplitudes)

        fmt, p, a, c, meta = DataImporter.load(str(path))
        assert fmt == "phase_amplitude"
        assert len(p) == 50
        assert len(a) == 50

    def test_unsupported_format(self):
        """测试不支持的文件格式"""
        with pytest.raises(ValueError, match="不支持的文件格式"):
            DataImporter.load("test.xyz")

    def test_validate_data_valid(self):
        """测试有效数据验证"""
        phases = np.linspace(0, 360, 100)
        amplitudes = 50 + 20 * np.random.rand(100)
        report = DataImporter.validate_data(phases, amplitudes)

        assert report["valid"] is True
        assert report["total_events"] == 100
        assert report["valid_events"] == 100
        assert report["invalid_events"] == 0

    def test_validate_data_with_invalid(self):
        """测试包含无效数据的验证"""
        phases = np.array([100, 200, -10, 400, 180])  # -10 和 400 无效
        amplitudes = np.array([50, 60, 70, 80, -5])  # -5 无效
        report = DataImporter.validate_data(phases, amplitudes)

        assert report["invalid_events"] == 3  # -10, 400, -5
        assert report["valid_events"] == 2

    def test_validate_data_empty(self):
        """测试空数据验证"""
        phases = np.array([])
        amplitudes = np.array([])
        report = DataImporter.validate_data(phases, amplitudes)

        assert report["valid"] is False
        assert report["warnings"] == ["数据为空"]

    def test_preview_csv(self, sample_csv_data):
        """测试 CSV 预览"""
        preview = DataImporter.preview_file(sample_csv_data, max_rows=5)

        assert preview["format"] == "CSV"
        assert "columns" in preview
        assert "preview" in preview
        assert len(preview["preview"]) <= 5

    def test_preview_json(self, sample_json_data):
        """测试 JSON 预览"""
        preview = DataImporter.preview_file(sample_json_data, max_rows=5)

        assert preview["format"] == "JSON"
        assert "columns" in preview
        assert "preview" in preview

    def test_preview_numpy(self, sample_numpy_data):
        """测试 NumPy 预览"""
        preview = DataImporter.preview_file(sample_numpy_data, max_rows=5)

        assert preview["format"] == "NumPy"
        assert "columns" in preview
        assert "preview" in preview

    def test_preview_unsupported(self):
        """测试不支持格式的预览"""
        with pytest.raises(ValueError, match="不支持的文件格式"):
            DataImporter.preview_file("test.xyz")
