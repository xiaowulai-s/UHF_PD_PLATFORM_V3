# -*- coding: utf-8 -*-
"""PRPD 实验数据导入器 — CSV/Excel/TXT 自动识别"""
# TODO: 大文件全量加载有 OOM 风险

from __future__ import annotations
import csv
from pathlib import Path
from typing import Tuple
import numpy as np


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

        if not rows:
            raise ValueError("文件为空或不包含有效数据行")

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


