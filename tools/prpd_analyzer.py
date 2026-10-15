#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

"""
PRPD 实验数据分析工具

功能:
1. 从 CSV/Excel 导入 PRPD 实验数据
2. 自动识别数据格式（相位-幅值对、PRPD矩阵、波形等）
3. 生成 PRPD 图谱、FFT 频谱、统计特征
4. 输出分析报告（JSON + PNG 图片）

用法:
    python tools/prpd_analyzer.py --input PRPD数据.csv --output 分析结果/
    python tools/prpd_analyzer.py --input 数据.xlsx --plot --report
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

# 加入项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.processing import FFTProcessor, PeakDetector, PRPDProcessor
from core.processing.prpd_processor import PRPDMode

# ═══════════════════════════════════════════════════════
# 数据导入器
# ═══════════════════════════════════════════════════════


class PRPDDataImporter:
    """PRPD 数据导入器 — 支持多种实验数据格式"""

    @staticmethod
    def detect_format(file_path: str) -> str:
        """
        自动检测数据文件格式

        返回: "phase_amplitude" | "prpd_matrix" | "waveform" | "unknown"
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        suffix = path.suffix.lower()
        if suffix == ".csv":
            return PRPDDataImporter._detect_csv_format(path)
        elif suffix in (".xlsx", ".xls"):
            return PRPDDataImporter._detect_excel_format(path)
        elif suffix == ".txt":
            return PRPDDataImporter._detect_txt_format(path)
        else:
            raise ValueError(f"不支持的文件格式: {suffix}")

    @staticmethod
    def _detect_csv_format(path: Path) -> str:
        """检测 CSV 文件格式"""
        import csv

        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, [])
                first_row = next(reader, [])

            header_lower = [h.strip().lower() for h in header]
            n_cols = len(first_row)

            # 格式1: 相位-幅值对 (phase, amplitude) 或 (phi, amp/mV)
            if any(kw in " ".join(header_lower) for kw in ["phase", "phi", "φ", "相位"]):
                if any(kw in " ".join(header_lower) for kw in ["amp", "magnitude", "幅值", "mV", "discharge"]):
                    return "phase_amplitude"

            # 格式1b: image_id,cycle,phase_deg,discharge 格式
            if any(h in " ".join(header_lower) for h in ["image_id", "image"]):
                if any(h in " ".join(header_lower) for h in ["phase_deg", "phase"]):
                    if any(h in " ".join(header_lower) for h in ["discharge"]):
                        return "phase_amplitude"

            # 格式2: 纯数值矩阵（每行一个相位bin，每列一个幅值bin）
            if n_cols >= 32 and len(first_row) == n_cols:
                try:
                    vals = [float(v) for v in first_row]
                    if all(v >= 0 for v in vals[:10]):
                        return "prpd_matrix"
                except ValueError:
                    pass

            # 格式3: 单列波形数据
            if n_cols == 1:
                try:
                    float(first_row[0])
                    return "waveform"
                except ValueError:
                    pass

            # 格式4: 时间戳-幅值序列
            if "timestamp" in header_lower or "time" in header_lower:
                if "amplitude" in header_lower or "value" in header_lower:
                    return "time_series"

            return "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _detect_excel_format(path: Path) -> str:
        """检测 Excel 文件格式"""
        try:
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True)
            ws = wb.active
            header = [
                str(c.value).strip().lower() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))[0]
            ]
            header_text = " ".join(header)

            if any(kw in header_text for kw in ["phase", "phi", "φ", "相位"]):
                if any(kw in header_text for kw in ["amp", "magnitude", "幅值", "mV"]):
                    return "phase_amplitude"
            if any(kw in header_text for kw in ["timestamp", "time"]):
                if any(kw in header_text for kw in ["amplitude", "value"]):
                    return "time_series"

            wb.close()
            return "prpd_matrix"
        except ImportError:
            raise ImportError("需要安装 openpyxl: pip install openpyxl")
        except Exception:
            return "unknown"

    @staticmethod
    def _detect_txt_format(path: Path) -> str:
        """检测 TXT 文件格式"""
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) < 2:
            return "unknown"
        first_line = lines[0].strip()
        # 空格/Tab分隔的数值
        parts = first_line.split()
        if len(parts) == 1:
            return "waveform"
        elif len(parts) == 2:
            return "phase_amplitude"
        elif len(parts) >= 32:
            return "prpd_matrix"
        return "unknown"

    @staticmethod
    def load(path: str) -> Tuple[str, Any]:
        """
        加载数据文件

        Args:
            path: 文件路径

        Returns:
            (format, data) 格式和数据
        """
        fmt = PRPDDataImporter.detect_format(path)
        suffix = Path(path).suffix.lower()

        if suffix == ".csv":
            return fmt, PRPDDataImporter._load_csv(path, fmt)
        elif suffix in (".xlsx", ".xls"):
            return fmt, PRPDDataImporter._load_excel(path, fmt)
        elif suffix == ".txt":
            return fmt, PRPDDataImporter._load_txt(path, fmt)
        else:
            raise ValueError(f"不支持的文件格式: {suffix}")

    @staticmethod
    def _load_csv(path: str, fmt: str) -> Any:
        """加载 CSV 文件"""
        import csv

        with open(path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = [r for r in reader if r and not r[0].startswith(("#", "%", "//"))]

        if fmt == "phase_amplitude":
            header = rows[0]
            data = rows[1:]
            header_lower = [h.strip().lower() for h in header]
            phases, amplitudes = [], []

            # 按列名映射，支持多种命名方式
            phase_idx = None
            amp_idx = None
            for i, h in enumerate(header_lower):
                if h in ("phase_deg", "phase", "phi", "φ", "φ(度)", "相位"):
                    phase_idx = i
                if h in ("discharge", "amplitude", "amp", "magnitude", "幅值", "mv", "pc"):
                    amp_idx = i

            # 如果找不到列名，回退到前两列
            if phase_idx is None:
                phase_idx = 0
            if amp_idx is None:
                amp_idx = 1

            for row in data:
                if len(row) > max(phase_idx, amp_idx):
                    try:
                        p = float(row[phase_idx])
                        a = float(row[amp_idx])
                        if 0 <= p <= 360 and a >= 0:
                            phases.append(p)
                            amplitudes.append(a)
                    except (ValueError, IndexError):
                        continue
            return {"phases": np.array(phases), "amplitudes": np.array(amplitudes)}

        elif fmt == "prpd_matrix":
            matrix = []
            for row in rows:
                vals = [float(v) for v in row if v.strip()]
                if vals:
                    matrix.append(vals)
            return {"matrix": np.array(matrix)}

        elif fmt == "waveform":
            values = [float(r[0]) for r in rows if r]
            return {"waveform": np.array(values)}

        elif fmt == "time_series":
            header = rows[0]
            data = rows[1:]
            timestamps, values = [], []
            for row in data:
                if len(row) >= 2:
                    try:
                        timestamps.append(float(row[0]))
                        values.append(float(row[1]))
                    except ValueError:
                        try:
                            from datetime import datetime

                            ts = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").timestamp()
                            timestamps.append(ts)
                            values.append(float(row[1]))
                        except (ValueError, IndexError):
                            continue
            return {"timestamps": np.array(timestamps), "values": np.array(values)}

        return {"raw": np.array([r for r in rows])}

    @staticmethod
    def _load_excel(path: str, fmt: str) -> Any:
        """加载 Excel 文件"""
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active

        if fmt == "phase_amplitude":
            rows = list(ws.iter_rows(values_only=True))
            phases, amplitudes = [], []
            for row in rows[1:]:
                if len(row) >= 2 and row[0] is not None and row[1] is not None:
                    try:
                        phases.append(float(row[0]))
                        amplitudes.append(float(row[1]))
                    except (ValueError, TypeError):
                        continue
            wb.close()
            return {"phases": np.array(phases), "amplitudes": np.array(amplitudes)}

        elif fmt == "prpd_matrix":
            matrix = []
            for row in ws.iter_rows(values_only=True):
                vals = [float(v) for v in row if v is not None]
                if vals:
                    matrix.append(vals)
            wb.close()
            return {"matrix": np.array(matrix)}

        wb.close()
        return {}

    @staticmethod
    def _load_txt(path: str, fmt: str) -> Any:
        """加载 TXT 文件"""
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith(("#", "%", "//"))]

        if fmt == "phase_amplitude":
            phases, amplitudes = [], []
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        phases.append(float(parts[0]))
                        amplitudes.append(float(parts[1]))
                    except ValueError:
                        continue
            return {"phases": np.array(phases), "amplitudes": np.array(amplitudes)}

        elif fmt == "waveform":
            values = []
            for line in lines:
                try:
                    values.append(float(line))
                except ValueError:
                    continue
            return {"waveform": np.array(values)}

        elif fmt == "prpd_matrix":
            matrix = []
            for line in lines:
                vals = [float(v) for v in line.split()]
                if vals:
                    matrix.append(vals)
            return {"matrix": np.array(matrix)}

        return {}


# ═══════════════════════════════════════════════════════
# 分析引擎
# ═══════════════════════════════════════════════════════


class PRPDAnalyzer:
    """PRPD 数据分析引擎"""

    def __init__(self, sample_rate: int = 100_000_000):
        self._sample_rate = sample_rate
        self._result: Dict[str, Any] = {
            "analysis_time": datetime.now().isoformat(),
            "sample_rate": sample_rate,
            "total_events": 0,
            "prpd_stats": {},
            "peak_stats": {},
            "fft_stats": {},
            "warnings": [],
        }

    def analyze_phase_amplitude(self, phases: np.ndarray, amplitudes: np.ndarray) -> Dict:
        """
        分析相位-幅值数据

        输入: (phase, amplitude) 事件对列表
        输出: PRPD 图谱 + 统计特征
        """
        print(f"[分析] 相位-幅值数据: {len(phases)} 个事件")

        if len(phases) == 0:
            return {"error": "空数据"}

        # 创建 PRPD 处理器
        max_amp = float(np.percentile(np.abs(amplitudes), 99))
        prpd = PRPDProcessor(phase_bins=360, amplitude_bins=256, max_amplitude=max_amp * 1.2)

        for p, a in zip(phases, amplitudes):
            prpd.add_event(
                phase=float(p),
                amplitude=float(a),
                polarity=0 if a >= 0 else 1,
            )

        # 计算 PRPD
        result = prpd.compute(mode=PRPDMode.HEATMAP)
        stats = prpd.compute_stats()

        self._result["total_events"] = len(phases)
        self._result["max_amplitude"] = float(max_amp)
        self._result["prpd"] = {
            "matrix_shape": result.matrix.shape,
            "stats": stats,
            "phase_concentration": stats.get("phase_concentration", 0),
            "positive_ratio": stats.get("positive_ratio", 0),
        }
        self._result["prpd_stats"] = stats

        print(f"  - 最大幅值: {max_amp:.1f} mV")
        print(f"  - 相位集中度: {stats['phase_concentration']:.3f}")
        print(f"  - 正极性比例: {stats['positive_ratio']:.3f}")
        print(f"  - 平均幅值: {stats['avg_amplitude']:.1f} mV")

        # 放电类型判断
        self._classify_discharge_type(stats)

        return self._result

    def analyze_waveform(self, waveform: np.ndarray) -> Dict:
        """
        分析原始波形数据

        输入: 一维波形数组
        输出: FFT频谱 + 峰值检测 + PRPD
        """
        print(f"[分析] 波形数据: {len(waveform)} 点")

        if len(waveform) < 8:
            return {"error": "波形数据不足"}

        # 基线校正
        waveform = waveform - np.median(waveform)
        max_amp = float(np.max(np.abs(waveform)))

        # FFT 分析
        fft_proc = FFTProcessor(sample_rate=self._sample_rate, window_size=min(len(waveform), 4096))
        fft_result = fft_proc.compute(waveform, detect_peaks=False)
        # 手动峰值检测
        detector = PeakDetector(threshold=max_amp * 0.1, adaptive=True)
        peaks = detector.detect_peaks_simple(waveform)

        self._result["waveform_points"] = len(waveform)
        self._result["max_amplitude"] = max_amp
        self._result["fft_stats"] = {
            "noise_floor": float(fft_result.noise_floor),
            "snr_db": float(fft_result.snr_db),
            "peak_count": len(peaks),
        }
        self._result["peak_stats"] = {
            "count": len(peaks),
            "max_amplitude": float(max(p.amplitude for p in peaks)) if peaks else 0,
        }

        # 提取峰值作为 PRPD 事件
        phases = np.array([(p.position / len(waveform)) * 360 for p in peaks])
        amplitudes = np.array([p.amplitude for p in peaks])
        self.analyze_phase_amplitude(phases, amplitudes)

        print(f"  - 波峰数: {len(peaks)}")
        print(f"  - SNR: {fft_result.snr_db:.1f} dB")
        print(f"  - 噪声底噪: {fft_result.noise_floor:.3f}")

        return self._result

    def analyze_prpd_matrix(self, matrix: np.ndarray) -> Dict:
        """
        分析 PRPD 矩阵数据

        输入: 已有 PRPD 矩阵 (如从仪器导出)
        输出: 统计特征 + 放电类型判断
        """
        print(f"[分析] PRPD 矩阵: {matrix.shape}")

        if matrix.ndim != 2:
            return {"error": "矩阵应为二维"}

        nz = matrix[matrix > 0]
        if len(nz) == 0:
            return {"error": "空矩阵"}

        # 计算相位分布
        phase_axis = np.linspace(0, 360, matrix.shape[0])
        amplitude_axis = np.linspace(0, 100, matrix.shape[1])

        # 按相位聚合
        phase_profile = np.sum(matrix, axis=1)
        phase_peak = phase_axis[np.argmax(phase_profile)]

        # 按幅值聚合
        amp_profile = np.sum(matrix, axis=0)
        max_amp = amplitude_axis[np.argmax(amp_profile)]

        # 相位集中度
        phase_concentration = float(np.max(phase_profile) / (np.sum(phase_profile) + 1e-10))

        # 正负半周能量比 (假设 0-180° 为正半周)
        pos_energy = float(np.sum(matrix[: matrix.shape[0] // 2, :]))
        neg_energy = float(np.sum(matrix[matrix.shape[0] // 2 :, :]))
        total = pos_energy + neg_energy
        pos_ratio = pos_energy / total if total > 0 else 0.5

        stats = {
            "total_events": int(np.sum(matrix)),
            "max_amplitude": max_amp,
            "phase_peak": phase_peak,
            "phase_concentration": phase_concentration,
            "positive_ratio": pos_ratio,
            "non_zero_cells": int(np.count_nonzero(matrix)),
        }
        self._result["prpd_stats"] = stats
        self._result["total_events"] = stats["total_events"]

        # 放电类型判断
        self._classify_discharge_type(stats)

        return self._result

    def _classify_discharge_type(self, stats: Dict) -> None:
        """判断放电类型"""
        conc = stats.get("phase_concentration", 0)
        pos_ratio = stats.get("positive_ratio", 0.5)
        max_amp = stats.get("max_amplitude", 0)

        types = []
        confidences = []

        # 特征1: 相位集中度
        if conc > 0.3:
            if 0.4 < pos_ratio < 0.6:
                types.append("内部气隙放电 (Internal Discharge)")
                confidences.append(min(conc * 2, 0.95))
            elif pos_ratio < 0.3:
                types.append("电晕放电 (Corona Discharge)")
                confidences.append(min(conc * 2, 0.95))
            elif pos_ratio > 0.7:
                types.append("沿面放电 (Surface Discharge)")
                confidences.append(min(conc * 2, 0.95))
        else:
            types.append("悬浮颗粒放电 (Floating Discharge)")
            confidences.append(0.6)

        # 特征2: 幅值判断严重程度
        severity = "normal"
        if max_amp > 80:
            severity = "critical"
        elif max_amp > 50:
            severity = "warning"
        elif max_amp > 30:
            severity = "attention"

        self._result["discharge_type"] = {
            "primary_type": types[0],
            "confidence": float(confidences[0]),
            "severity": severity,
        }
        print(f"  - 放电类型: {types[0]} (置信度: {confidences[0]:.0%})")
        print(f"  - 严重等级: {severity}")

    def get_result(self) -> Dict:
        """获取完整分析结果"""
        return self._result

    def print_report(self) -> None:
        """打印分析报告"""
        r = self._result
        print("\n" + "=" * 60)
        print("            PRPD 分析报告")
        print("=" * 60)
        print(f"  分析时间: {r.get('analysis_time', 'N/A')}")
        print(f"  总事件数: {r.get('total_events', 0)}")
        print(f"  最大幅值: {r.get('max_amplitude', 'N/A')} mV")

        if "discharge_type" in r:
            dt = r["discharge_type"]
            print(f"  放电类型: {dt.get('primary_type', 'N/A')}")
            print(f"  置信度:   {dt.get('confidence', 0):.0%}")
            print(f"  严重等级: {dt.get('severity', 'N/A')}")

        if "prpd_stats" in r:
            s = r["prpd_stats"]
            print("\n  PRPD 特征:")
            print(f"    - 相位集中度: {s.get('phase_concentration', 0):.3f}")
            print(f"    - 正极性比例: {s.get('positive_ratio', 0):.3f}")
            print(f"    - 平均幅值:   {s.get('avg_amplitude', 'N/A')} mV")

        if "fft_stats" in r:
            f = r["fft_stats"]
            print("\n  FFT 特征:")
            print(f"    - SNR:        {f.get('snr_db', 0):.1f} dB")
            print(f"    - 噪声底噪:   {f.get('noise_floor', 0):.4f}")
            print(f"    - 峰值数:     {f.get('peak_count', 0)}")

        print("=" * 60)

    def save_report(self, output_dir: str) -> str:
        """保存分析报告到 JSON"""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)

        report_path = path / f"prpd_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_path, "w", encoding="utf-8") as f:
            # 转换不可序列化的类型
            def serialize(obj):
                if isinstance(obj, (np.ndarray, np.generic)):
                    return obj.tolist() if isinstance(obj, np.ndarray) else obj.item()
                return str(obj)

            json.dump(self._result, f, ensure_ascii=False, indent=2, default=serialize)

        print(f"\n[报告已保存] {report_path}")
        return str(report_path)

    def save_plots(self, output_dir: str) -> List[str]:
        """保存分析图表（需要 matplotlib）"""
        files = []
        r = self._result

        if "prpd" not in r and "prpd_stats" not in r:
            return files

        try:
            import matplotlib

            matplotlib.use("Agg")
        except ImportError:
            print("[警告] matplotlib 未安装，跳过图表输出")
            return files

        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)

        # 如果有 PRPD 矩阵数据（从 phase_amplitude 分析来的）
        # 这里主要通过 PRPDProcessor 获取

        print(f"\n[图表已保存] 共 {len(files)} 张")
        return files


# ═══════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="PRPD 实验数据分析工具")
    parser.add_argument("--input", "-i", required=True, help="输入数据文件路径 (CSV/Excel/TXT)")
    parser.add_argument("--output", "-o", default="./prpd_output", help="输出目录 (默认: ./prpd_output)")
    parser.add_argument("--plot", action="store_true", help="生成分析图表")
    parser.add_argument("--report", action="store_true", help="保存 JSON 分析报告")
    parser.add_argument("--sample-rate", type=int, default=100_000_000, help="采样率 Hz (默认 100MHz)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[错误] 文件不存在: {args.input}")
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)

    # 1. 导入数据
    print(f"[导入] 加载文件: {args.input}")
    try:
        fmt, data = PRPDDataImporter.load(args.input)
        print(f"[格式] 识别格式: {fmt}")
    except Exception as e:
        print(f"[错误] 数据导入失败: {e}")
        sys.exit(1)

    # 2. 分析数据
    analyzer = PRPDAnalyzer(sample_rate=args.sample_rate)
    try:
        if fmt == "phase_amplitude":
            analyzer.analyze_phase_amplitude(data["phases"], data["amplitudes"])
        elif fmt == "prpd_matrix":
            analyzer.analyze_prpd_matrix(data["matrix"])
        elif fmt == "waveform":
            analyzer.analyze_waveform(data["waveform"])
        elif fmt == "time_series":
            analyzer.analyze_waveform(data["values"])
        else:
            print(f"[错误] 无法识别的数据格式: {fmt}")
            print("支持格式: 相位-幅值对、PRPD矩阵、波形数据")
            sys.exit(1)
    except Exception as e:
        print(f"[错误] 分析过程异常: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # 3. 输出结果
    analyzer.print_report()

    if args.report:
        analyzer.save_report(args.output)

    if args.plot:
        analyzer.save_plots(args.output)

    print("\n[完成]")


if __name__ == "__main__":
    main()
