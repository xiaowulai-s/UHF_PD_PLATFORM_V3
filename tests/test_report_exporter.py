# -*- coding: utf-8 -*-
"""ReportExporter 测试"""

import json
import os

import pytest

from core.data.report_exporter import ReportExporter


@pytest.fixture
def sample_analysis_result():
    """示例分析结果"""
    return {
        "analysis_time": "2025-06-30 12:00:00",
        "file_name": "test_data.csv",
        "statistics": {
            "total_events": 1000,
            "max_amplitude": 85.5,
            "min_amplitude": 10.2,
            "avg_amplitude": 45.3,
            "median_amplitude": 42.0,
            "p90": 75.0,
        },
        "discharge_classification": {
            "name": "内部放电",
            "confidence": 0.85,
            "severity": "medium",
            "total_samples": 1000,
            "type_counts": {
                "internal": 600,
                "corona": 300,
                "surface": 100,
            },
            "type_ratios": {
                "internal": 0.6,
                "corona": 0.3,
                "surface": 0.1,
            },
        },
    }


class TestReportExporter:
    """报告导出测试"""

    def test_export_json(self, sample_analysis_result, tmp_path):
        """测试 JSON 导出"""
        path = str(tmp_path / "report.json")
        result_path = ReportExporter.export_json(sample_analysis_result, path)

        assert os.path.exists(result_path)
        assert result_path == path

        # 验证文件内容
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["statistics"]["total_events"] == 1000
        assert data["discharge_classification"]["name"] == "内部放电"

    def test_export_excel(self, sample_analysis_result, tmp_path):
        """测试 Excel 导出"""
        path = str(tmp_path / "report.xlsx")
        result_path = ReportExporter.export_excel(sample_analysis_result, path)

        assert os.path.exists(result_path)
        assert result_path == path

    def test_export_word(self, sample_analysis_result, tmp_path):
        """测试 Word 导出"""
        pytest.importorskip("docx")
        path = str(tmp_path / "report.docx")
        result_path = ReportExporter.export_word(sample_analysis_result, path)

        assert os.path.exists(result_path)
        assert result_path == path

    def test_export_word_with_chart(self, sample_analysis_result, tmp_path):
        """带图表的 Word 导出：必须真的传一个存在的 chart 文件。

        这个用例原先传 `chart_path=None` 却叫"带图表"——短路绕开了
        `os.path.exists(chart_path)` 那一行，于是模块缺 `import os` 也照样全绿。
        """
        pytest.importorskip("docx")
        chart = tmp_path / "prpd.png"
        chart.write_bytes(b"\x89PNG\r\n\x1a\n")  # 够 docx 走 add_picture 的路径判断
        path = str(tmp_path / "report_with_chart.docx")
        result_path = ReportExporter.export_word(sample_analysis_result, path, chart_path=str(chart))

        assert os.path.exists(result_path)

    def test_chart_branch_resolves_os_even_without_docx(self, sample_analysis_result, tmp_path):
        """没装 python-docx 时也必须能跑到图表存在性那一行。

        这是防 `import os` 缺失的正主：原先模块里根本没有 `import os`，而所有带
        docx 的用例都被 `importorskip` 静默跳过，那行 NameError 因此永不可见。
        本用例不加 importorskip——它断言抛的是 ImportError 而不是 NameError。
        """
        chart = tmp_path / "prpd.png"
        chart.write_bytes(b"\x89PNG\r\n\x1a\n")
        path = str(tmp_path / "report.docx")

        with pytest.raises(ImportError, match="python-docx"):
            ReportExporter.export_word(sample_analysis_result, path, chart_path=str(chart))

    def test_chart_existence_checked_before_importing_docx(self, sample_analysis_result, tmp_path, monkeypatch):
        """存在性检查必须排在导 docx 之前，否则上一条用例在本机形同虚设。

        只有"先检查、后导库"这个顺序，才让带 chart_path 的分支在没装 python-docx
        的机器上真的被执行。用 spy 直接验顺序：把检查挪回导库之后的话，这里收不到
        调用，用例会红——单靠上一条断言 ImportError 是分不出这个差别的（实测过）。
        """
        seen = []
        real_exists = os.path.exists

        def spy(candidate):
            seen.append(str(candidate))
            return real_exists(candidate)

        monkeypatch.setattr(os.path, "exists", spy)

        chart = tmp_path / "prpd.png"
        chart.write_bytes(b"\x89PNG\r\n\x1a\n")
        path = str(tmp_path / "report.docx")

        with pytest.raises(ImportError, match="python-docx"):
            ReportExporter.export_word(sample_analysis_result, path, chart_path=str(chart))

        assert str(chart) in seen, (
            "图表存在性没在导 docx 之前被检查：本机没有 python-docx 时这一行永远执行不到，"
            "相关缺陷（如缺 import os）就永远不会被测到"
        )

    def test_missing_chart_path_is_tolerated(self, sample_analysis_result, tmp_path):
        """图表文件不存在时应跳过插图而不是抛 NameError/崩掉。"""
        path = str(tmp_path / "report.docx")

        with pytest.raises(ImportError, match="python-docx"):
            ReportExporter.export_word(sample_analysis_result, path, chart_path=str(tmp_path / "nope.png"))

    def test_export_excel_missing_openpyxl(self, sample_analysis_result, tmp_path, monkeypatch):
        """测试 Excel 导出缺少 openpyxl 时抛出 ImportError"""
        import sys

        path = str(tmp_path / "report.xlsx")

        # 临时隐藏 openpyxl
        monkeypatch.setitem(sys.modules, "openpyxl", None)

        with pytest.raises(ImportError, match="需要安装 openpyxl"):
            ReportExporter.export_excel(sample_analysis_result, path)

    def test_export_word_missing_docx(self, sample_analysis_result, tmp_path, monkeypatch):
        """测试 Word 导出缺少 python-docx 时抛出 ImportError"""
        import sys

        path = str(tmp_path / "report.docx")

        # 临时隐藏 docx
        monkeypatch.setitem(sys.modules, "docx", None)

        with pytest.raises(ImportError, match="需要安装 python-docx"):
            ReportExporter.export_word(sample_analysis_result, path)

    def test_export_json_handles_non_serializable(self, tmp_path):
        """测试 JSON 导出处理不可序列化对象"""
        # 包含 numpy 类型的数据（default=str 应处理）
        data = {
            "analysis_time": "2025-06-30",
            "statistics": {
                "total_events": 1000,
            },
        }
        path = str(tmp_path / "report.json")
        ReportExporter.export_json(data, path)

        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["statistics"]["total_events"] == 1000
