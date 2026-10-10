"""分析报告导出的 RPC —— 内核生成字节，前端交给主进程的原生对话框落盘。

为什么不把路径交给渲染进程：报告是二进制（xlsx/docx），若让 renderer 传"写到哪"，
就等于给 file:// 页面开一个任意路径写入口。所以内核只在临时目录里生成、读成 bytes
回传，临时目录随 with 块删掉；真正落盘由主进程的保存对话框决定（见 file:save-binary）。

`ReportExporter` 只有"写文件"的接口，这里不改动它（它的用例已经覆盖了按路径写的行为），
而是用临时目录接住——多一次磁盘往返换的是不新增第二套生成逻辑。

Word 报告的 PRPD 截图走 `chart_png`（base64）。解码失败就报错，不静默出一张没图的报告：
用户勾了"带图谱"却拿到空图，比导入失败更难发现。
"""

from __future__ import annotations

import base64
import binascii
import importlib.util
import json
import logging
import tempfile
from datetime import datetime
from typing import Any, Callable

from core.data.report_exporter import ReportExporter

logger = logging.getLogger(__name__)

#: 扩展名按格式白名单来，不接受前端传来的文件名后缀——那才是真正会写错文件的地方
FORMATS: dict[str, str] = {"json": "json", "excel": "xlsx", "word": "docx"}

#: 单次导出的输入上限。ctrl 报文本身还有 MAX_MESSAGE_BYTES(16MB) 兜着，这里给的是
#: "报告数据"这一层的意思：超过它说明传进来的是原始事件数组而不是聚合结果。
MAX_DATA_BYTES = 8 * 1024 * 1024
MAX_CHART_BYTES = 8 * 1024 * 1024

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _importable(modname: str) -> bool:
    """只判断模块能否被导入，不真正导入（导入 openpyxl 有几十毫秒的成本）。"""
    return importlib.util.find_spec(modname) is not None


class ReportRpc:
    """无状态：每次调用现生成一份报告字节，不留会话。"""

    def available_formats(self, _params: dict | None = None) -> dict:
        """报告内核当前真能生成哪些格式。

        存在理由：`openpyxl` 在 `pyproject.toml` 的依赖里，而 `python-docx` **不在**，
        所以冻结产物里没有它——目标机又不装 Python，"导出 Word"按钮点下去只会拿到一句
        ImportError，用户完全没法预判。这里让内核自报，前端据此禁用并写明原因，
        比给一个必失败的按钮诚实。
        """
        missing: dict[str, str] = {}
        available: list[str] = ["json"]
        if _importable("openpyxl"):
            available.append("excel")
        else:
            missing["excel"] = "内核未打包 openpyxl"
        if _importable("docx"):
            available.append("word")
        else:
            missing["word"] = "内核未打包 python-docx（它不在 pyproject 依赖里）"
        return {"available": sorted(available, key=FORMATS.__getitem__), "missing": missing}

    def export_report(self, params: dict) -> dict:
        """按 `params["format"]` 生成报告，返回 `{filename, format, bytes, size}`。"""
        fmt = str(params.get("format") or "").strip().lower()
        ext = FORMATS.get(fmt)
        if ext is None:
            raise ValueError(f"未知报告格式: {fmt!r}，支持: {', '.join(sorted(FORMATS))}")

        data = params.get("data")
        if not isinstance(data, dict) or not data:
            raise ValueError("data 必须是非空对象（分析结果聚合，不是原始事件数组）")
        self._check_data_size(data)

        with tempfile.TemporaryDirectory(prefix="uhf-report-") as workdir:
            # 载荷一律先校验，再按格式决定用不用：如果 json/excel 静默丢掉一张图，
            # 用户以为报告里有图谱，实际没有——这跟原先"缺 import os 却全绿"是同一类假。
            chart_path = self._write_chart(workdir, params.get("chart_png"), supports_chart=fmt == "word")
            path = f"{workdir}/report.{ext}"
            if fmt == "json":
                ReportExporter.export_json(data, path)
            elif fmt == "excel":
                ReportExporter.export_excel(data, path)
            else:
                ReportExporter.export_word(data, path, chart_path=chart_path)
            with open(path, "rb") as fh:
                blob = fh.read()

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        logger.info("报告已生成 format=%s size=%.1fKB 图表=%s", fmt, len(blob) / 1024, bool(chart_path))
        return {
            "format": fmt,
            # 只是默认名：用户可以改，路径由主进程的对话框决定
            "filename": f"prpd-report-{stamp}.{ext}",
            "bytes": blob,
            "size": len(blob),
            "chart_embedded": bool(chart_path),
        }

    @staticmethod
    def _check_data_size(data: dict) -> None:
        size = len(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"))
        if size > MAX_DATA_BYTES:
            raise ValueError(
                f"报告数据 {size / 1048576:.1f}MB 超过上限 " f"{MAX_DATA_BYTES // 1048576}MB（应传聚合结果）"
            )

    @staticmethod
    def _write_chart(workdir: str, chart_png: Any, *, supports_chart: bool) -> str | None:
        """把 base64 PNG 落到临时目录，返回路径；没给就返回 None。

        载荷一律先校验（坏 base64、非 PNG、超限都必须报错），`supports_chart` 只决定
        落不落盘：前端可以不分格式都把 canvas 快照带上，非 Word 的格式忽略它，而
        `chart_embedded` 据实返回 false——不能让用户以为报告里有图谱，实际没有。
        """
        if chart_png in (None, ""):
            return None
        if isinstance(chart_png, (bytes, bytearray)):
            raw = bytes(chart_png)
        else:
            try:
                raw = base64.b64decode(str(chart_png), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(f"chart_png 不是合法 base64: {exc}") from exc
        if not raw.startswith(_PNG_MAGIC):
            raise ValueError("chart_png 解出来不是 PNG（前端应传 canvas.toDataURL 的 dataURL 载荷）")
        if len(raw) > MAX_CHART_BYTES:
            raise ValueError(f"图谱截图 {len(raw) / 1048576:.1f}MB 超过上限 {MAX_CHART_BYTES // 1048576}MB")
        if not supports_chart:
            return None
        path = f"{workdir}/prpd.png"
        with open(path, "wb") as fh:
            fh.write(raw)
        return path

    def handlers(self) -> list[tuple[str, Callable[[dict], Any]]]:
        return [("report.formats", self.available_formats), ("report.export", self.export_report)]


def register_report_rpc(server: Any) -> list[str]:
    """把报告导出挂上 sidecar。生成是 CPU+磁盘活，放工作线程别堵 Qt 事件循环。"""
    rpc = ReportRpc()
    names = []
    for name, handler in rpc.handlers():
        server.register_rpc(name, handler, background=True)
        names.append(name)
    return names
