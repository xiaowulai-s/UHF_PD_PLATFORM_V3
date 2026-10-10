"""实验数据导入的 RPC —— 把内核里"有能力但没人调用"的 `DataImporter` 接回前端。

背景：旧 PySide6 分析页（"PRPD 实验数据导入与分析"）归档后，`core/data/data_importer.py`
成了死代码：core/ 内部零调用者、无 RPC、前端零入口。前端"上传 CSV 做分析"靠本模块接回。

约束与口径：

- **只读**：导入只读文件、返回数组与统计，不落库、不进采集管线。
- **路径来源**：渲染进程是沙箱的、拿不到真实文件路径，由 preload 用
  `webUtils.getPathForFile(file)` 把用户在系统文件对话框里选中的文件换成绝对路径传过来。
  这里信任的是"用户亲手选的路径"，且只做读取；不接受目录、不接受 glob。
- **截断要如实回报**：ctrl 通道是可靠传输，几十万点整包塞过去会把前端拖死，超限截头，
  但 `truncated` 必须带回去——把 100 万点悄悄变成 5 万点是在说谎。
- **CSV 会丢行**：`DataImporter._load_csv` 的列映射带 `0 <= phase <= 360` 与 `a >= 0` 过滤，
  负幅值行会被丢掉。`source_rows` 与 `total_points` 的差就是被过滤的行数，原样上报。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from core.data.data_importer import DataImporter
from core.data.discharge_classifier import DischargeClassifier
from core.protocol.payloads import _plain

# ctrl 通道单响应的合理上限。5 万点对 PRPD 散点足够（内核实时 PRPD 帧也只带 2000 条），
# 真要分析更大的文件应该走分页，而不是把响应无限放大。
MAX_POINTS = 50_000


class DataRpc:
    """实验数据导入相关 RPC。无状态：文件读进来算完就返回，不持有任何会话。"""

    def data_import_file(self, params: dict) -> dict:
        path = str(params.get("path") or "").strip()
        if not path:
            raise ValueError("缺少 path")
        p = Path(path)
        if not p.is_file():
            raise ValueError(f"文件不存在: {path}")
        suffix = p.suffix.lower()
        if suffix not in DataImporter.SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持的文件格式: {suffix}，支持: {', '.join(DataImporter.SUPPORTED_EXTENSIONS)}")

        fmt, phases, amps, cycles, meta = DataImporter.load(str(p))
        total = int(len(phases))

        truncated = max(0, total - MAX_POINTS)
        if truncated:
            phases = phases[:MAX_POINTS]
            amps = amps[:MAX_POINTS]
            cycles = cycles[:MAX_POINTS]

        # CSV 里写 "nan" 之类会 float() 成功但不是有限值：msgpack 传不动、前端也没法画。
        # 丢弃并计数，而不是静默替换成 0——把坏点伪装成 0 幅值是在伪造数据。
        finite = np.isfinite(phases) & np.isfinite(amps)
        nonfinite = int((~finite).sum())
        if not finite.all():
            phases = phases[finite]
            amps = amps[finite]
            cycles = cycles[finite]

        validation = DataImporter.validate_data(phases, amps, cycles if len(cycles) else None)

        # 分类是尽力而为：失败也必须把数据还给前端，不能因为分类炸了整次导入作废
        classification: dict | None = None
        classification_error: str | None = None
        try:
            classification = DischargeClassifier.classify(phases, amps)
        except Exception as exc:  # noqa: BLE001 - 错误类型随实现变化，原样带回前端
            classification_error = f"{type(exc).__name__}: {exc}"

        # 逐周期分类 → 类型分布（设计稿"放电类型分布"的数据源）。
        # 只在有周期列时可用；per_sample_results 可能很大，对前端没用，裁掉。
        distribution: dict | None = None
        distribution_error: str | None = None
        try:
            if len(cycles) and int(np.max(cycles)) > 0:
                dist = DischargeClassifier.classify_by_cycles(phases, amps, cycles)
                cycle_ids = dist.get("cycle_ids") or []
                per_cycle = dist.get("per_sample_results") or []
                distribution = {
                    k: dist[k] for k in ("total_samples", "type_counts", "type_ratios", "severity") if k in dist
                }
                # 不把每周期的完整 scores/features 结果塞进 ctrl 响应；前端只需
                # cycle_id -> winning type，就能让旧版"类型筛选"筛选散点，响应保持很小。
                distribution["cycle_types"] = {
                    str(int(cycle_id)): str(result.get("type", "unknown"))
                    for cycle_id, result in zip(cycle_ids, per_cycle)
                }
        except Exception as exc:  # noqa: BLE001
            distribution_error = f"{type(exc).__name__}: {exc}"

        source_rows = meta.get("total_rows")
        filtered_rows = (int(source_rows) - total) if isinstance(source_rows, (int, float)) else None

        # 整包过一遍 _plain：validate/classify 的返回里混着 numpy 标量（比如
        # "symmetry": symmetry > 0.6 是 np.bool_），msgpack 不认，会在响应序列化时
        # 以"无法序列化: bool"整包失败——数据白算。
        return _plain(
            {
                "format": fmt,
                "file_name": p.name,
                "file_path": str(p),
                "file_size": p.stat().st_size,
                "total_points": total,
                "returned_points": int(len(phases)),
                "truncated": truncated,
                "nonfinite_dropped": nonfinite,
                "source_rows": int(source_rows) if isinstance(source_rows, (int, float)) else None,
                "filtered_rows": filtered_rows,
                "columns": meta.get("columns"),
                "phases": [float(x) for x in phases],
                "amplitudes": [float(x) for x in amps],
                "cycles": [int(x) for x in cycles],
                "validation": validation,
                "classification": classification,
                "classification_error": classification_error,
                "distribution": distribution,
                "distribution_error": distribution_error,
            }
        )


def register_data_rpc(server: Any) -> list[str]:
    rpc = DataRpc()
    out: list[str] = []
    for name, handler in (("data.import_file", rpc.data_import_file),):
        server.register_rpc(name, handler)
        out.append(name)
    return out
