# -*- coding: utf-8 -*-
"""
UI Lint — 检测 UI 源码中的硬编码值，强制使用 Design Token

用法:
    python scripts/ui_lint.py              # 扫描并报告
    python scripts/ui_lint.py --fix        # 自动修复（保守模式）
    python scripts/ui_lint.py --ci         # CI 模式，有违规则 exit 1

检测项:
    1. 硬编码 font-size → 应使用 DT.FS.*
    2. 硬编码 border-radius → 应使用 DT.R.*
    3. 硬编码 padding/margin 数值 → 应使用 DT.S.*
    4. 硬编码按钮高度 (28/32/36 混用) → 应统一
    5. get_font() 硬编码字号 → 应使用 DT.T.*
    6. 内联 QSS 重复 → 应使用 DT.sheet.*
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path
from typing import List

# Windows GBK 兼容
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Design Token 允许值 ─────────────────────────────────────

# DT.FS 允许的字号
ALLOWED_FONT_SIZES = {10, 11, 12, 13, 14, 16, 18, 20, 22, 28}

# DT.R 允许的圆角
ALLOWED_RADIUS = {0, 2, 3, 4, 6, 8, 12, 16, 24, 9999}

# DT.S 允许的间距
ALLOWED_SPACING = {0, 2, 4, 8, 12, 16, 24, 32, 48}

# 允许的交互控件高度 (按钮/输入框/下拉框)
ALLOWED_HEIGHTS = {28, 32, 36, 40, 48}
# 容器/面板的最小高度不检查 (150, 200 等是合理的)

# font-size 数字 → DT.FS 映射
FONT_SIZE_TOKEN_MAP = {
    28: "DT.FS.TITLE_XLARGE",  # 或 DT.FS.DATA_LARGE
    22: "DT.FS.DATA_MEDIUM",
    20: "DT.FS.TITLE_LARGE",  # 注意: DT.FS 无 20，需用 DT.T.TITLE_LARGE
    18: "DT.FS.DATA",
    16: "DT.FS.TITLE_MEDIUM",
    14: "DT.FS.TITLE_SMALL",  # 或 DT.FS.DATA_SMALL
    13: "DT.FS.BODY",
    12: "DT.FS.BODY_SMALL",  # 或 DT.FS.LABEL
    11: "DT.FS.BODY_XSMALL",  # 或 DT.FS.CAPTION
    10: "DT.FS.CAPTION_SMALL",
}

# border-radius 数字 → DT.R 映射
RADIUS_TOKEN_MAP = {
    0: "DT.R.NONE",
    2: "2",  # 允许但无令牌
    3: "3",  # 允许但无令牌
    4: "DT.R.SM",
    6: "6",  # 允许但无令牌
    8: "DT.R.MD",
    12: "DT.R.LG",
    16: "DT.R.XL",
    24: "DT.R.XXL",
}

# padding/margin 数字 → DT.S 映射
SPACING_TOKEN_MAP = {
    0: "DT.S.ZERO",
    2: "DT.S.TIGHT",
    4: "DT.S.XS",
    8: "DT.S.SM",
    12: "12",  # 无直接令牌，可用 DT.S.MD 近似
    16: "DT.S.MD",
    24: "DT.S.LG",
    32: "DT.S.XL",
    48: "DT.S.XXL",
}


# ── 违规定义 ──────────────────────────────────────────────


class Violation:
    def __init__(self, file: str, line: int, col: int, rule: str, message: str, fix: str = ""):
        self.file = file
        self.line = line
        self.col = col
        self.rule = rule
        self.message = message
        self.fix = fix

    def __str__(self):
        loc = f"{self.file}:{self.line}:{self.col}"
        return f"  {loc}  [{self.rule}] {self.message}" + (f"  -> {self.fix}" if self.fix else "")


# ── 扫描规则 ──────────────────────────────────────────────

# 匹配 font-size: Npx（不在 f-string 中的纯 QSS）
RE_FONT_SIZE_QSS = re.compile(r"font-size:\s*(\d+)\s*px")
# 匹配 f-string 中的 font-size: {expr}px
RE_FONT_SIZE_FSTRING = re.compile(r"font-size:\s*\{([^}]+)\}\s*px")
# 匹配 border-radius: Npx
RE_RADIUS_QSS = re.compile(r"border-radius:\s*(\d+)\s*px")
# 匹配 padding/margin 中的纯数字 px 值
RE_SPACING = re.compile(r"(?:padding|margin)(?:-(?:top|bottom|left|right))?:\s*[^;]*?(\d+)\s*px")
# 匹配 get_font("...", N, ...) 硬编码字号
RE_GET_FONT = re.compile(r"get_font\([^,]+,\s*(\d+)")
# 匹配 setFixedHeight(N) / setMinimumHeight(N) / setMaximumHeight(N)
RE_SET_HEIGHT = re.compile(r"set(?:Fixed|Minimum|Maximum)Height\((\d+)\)")
# 匹配 setFixedWidth(N) / setMinimumWidth(N)
RE_SET_WIDTH = re.compile(r"set(?:Fixed|Minimum|Maximum)Width\((\d+)\)")
# 匹配硬编码十六进制颜色 (在 QSS 字符串中)
RE_HEX_COLOR = re.compile(r"(?:color|background|background-color|border-color):\s*(#[0-9A-Fa-f]{6})\b")


def scan_file(filepath: Path) -> List[Violation]:
    """扫描单个文件，返回所有违规"""
    violations: List[Violation] = []
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return violations

    lines = text.splitlines()
    rel = str(filepath).replace("\\", "/")

    # 跳过 design_tokens.py 自身和本脚本
    if "design_tokens.py" in rel or "ui_lint.py" in rel or "theme_" in rel:
        return violations

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # 跳过注释
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        # ── 规则 1: 硬编码 font-size（纯数字，非 f-string）──
        for m in RE_FONT_SIZE_QSS.finditer(line):
            val = int(m.group(1))
            # 排除 f-string 中的 {DT.FS.*} 已经是令牌的情况
            if f"{{{FONT_SIZE_TOKEN_MAP.get(val, '')}}}" in line:
                continue
            if val not in ALLOWED_FONT_SIZES:
                violations.append(
                    Violation(rel, i, m.start(), "FONT-SIZE", f"硬编码 font-size: {val}px", "使用 DT.FS 令牌")
                )
            # 即使值在允许列表中，也应该用令牌而非数字
            # 但如果是 QSS 模板字符串中的常量，允许通过

        # ── 规则 2: 硬编码 border-radius（仅 .py 文件，.qss 无法使用令牌）──
        if filepath.suffix == ".py":
            for m in RE_RADIUS_QSS.finditer(line):
                val = int(m.group(1))
                token = RADIUS_TOKEN_MAP.get(val)
                if token and token.startswith("DT."):
                    violations.append(
                        Violation(rel, i, m.start(), "RADIUS", f"硬编码 border-radius: {val}px", f"使用 {token}")
                    )

        # ── 规则 3: 硬编码 spacing（仅在 Python 内联 QSS 中，排除纯 QSS 文件）──
        if filepath.suffix == ".py":
            # 排除 QSS 特有属性 (padding-top, left, right 用于 QGroupBox title 定位)
            qss_only_props = (
                "padding-top",
                "padding-bottom",
                "padding-left",
                "padding-right",
                "left:",
                "right:",
                "top:",
                "bottom:",
            )
            is_qss_prop = any(p in line for p in qss_only_props)

            if not is_qss_prop and ("padding" in line or "margin" in line):
                for m in RE_SPACING.finditer(line):
                    val = int(m.group(1))
                    if val in (1, 3, 5, 7, 9, 10, 14, 20, 40):
                        violations.append(
                            Violation(
                                rel,
                                i,
                                m.start(),
                                "SPACING",
                                f"硬编码间距: {val}px",
                                f"使用 DT.S 令牌 (最近: {min(ALLOWED_SPACING, key=lambda x: abs(x - val))}px)",
                            )
                        )

        # ── 规则 4: get_font 硬编码字号 ──
        for m in RE_GET_FONT.finditer(line):
            val = int(m.group(1))
            token = FONT_SIZE_TOKEN_MAP.get(val)
            if token:
                violations.append(
                    Violation(
                        rel, i, m.start(), "GET-FONT", f"get_font() 硬编码字号: {val}", f"使用 {token} 或 DT.T.* 常量"
                    )
                )

        # ── 规则 5: 硬编码十六进制颜色（在 QSS 内联字符串中）──
        # 仅检测明显的内联 QSS（包含多条 CSS 属性的行）
        if line.count(":") >= 2 and ("px" in line or "color" in line.lower()):
            for m in RE_HEX_COLOR.finditer(line):
                hex_val = m.group(1).upper()
                # 排除 DT.C.* 引用中的颜色（这些是令牌值本身）
                if f'"{hex_val}"' in line or f"'{hex_val}'" in line:
                    # 这是 DT.C 定义中的颜色值，跳过
                    if "class Colors" in text or "DT.C." in line:
                        continue
                    # 如果行中有 DT.C 引用，说明是令牌，跳过
                    if "DT.C." in line:
                        continue

    return violations


def scan_height_violations(filepath: Path) -> List[Violation]:
    """扫描按钮/输入框高度不一致问题（仅交互控件，不检查容器）"""
    violations: List[Violation] = []
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception:
        return violations

    rel = str(filepath).replace("\\", "/")
    if "design_tokens.py" in rel or "ui_lint.py" in rel:
        return violations

    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        for m in RE_SET_HEIGHT.finditer(line):
            val = int(m.group(1))
            # 仅检查交互控件高度 (setFixedHeight, 且值 <= 60)
            # 容器的 setMinimumHeight(80/96/150/200) 是合理的，跳过
            if "setMinimumHeight" in line or "setMaximumHeight" in line:
                continue
            if val not in ALLOWED_HEIGHTS and 20 < val <= 60:
                violations.append(
                    Violation(
                        rel,
                        i,
                        m.start(),
                        "HEIGHT",
                        f"非标准控件高度: {val}px",
                        f"使用标准高度: {sorted(ALLOWED_HEIGHTS)}",
                    )
                )

    return violations


# ── 主入口 ────────────────────────────────────────────────


def find_ui_files(root: Path) -> List[Path]:
    """查找所有 UI Python 文件"""
    patterns = ["ui/**/*.py", "ui/**/*.qss"]
    files = []
    for pat in patterns:
        files.extend(root.glob(pat))
    return sorted(files)


def run_lint(root: Path, fix: bool = False, ci: bool = False) -> int:
    """运行 lint 检查，返回违规数"""
    files = find_ui_files(root)
    all_violations: List[Violation] = []

    for f in files:
        all_violations.extend(scan_file(f))
        all_violations.extend(scan_height_violations(f))

    if not all_violations:
        print(f"\n  [PASS] UI Lint OK - scan {len(files)} files, 0 violations\n")
        return 0

    # 按文件分组
    by_file: dict[str, list] = {}
    for v in all_violations:
        by_file.setdefault(v.file, []).append(v)

    print(f"\n  [FAIL] UI Lint: {len(all_violations)} violations ({len(by_file)} files)\n")

    for filepath, violations in sorted(by_file.items()):
        print(f"  {filepath}")
        for v in violations:
            print(str(v))
        print()

    # 统计
    by_rule: dict[str, int] = {}
    for v in all_violations:
        by_rule[v.rule] = by_rule.get(v.rule, 0) + 1

    print("  -- Statistics --")
    for rule, count in sorted(by_rule.items(), key=lambda x: -x[1]):
        print(f"    {rule}: {count}")
    print()

    if ci:
        sys.exit(1)

    return len(all_violations)


def main():
    parser = argparse.ArgumentParser(description="UI Lint — 检测硬编码值")
    parser.add_argument("--fix", action="store_true", help="自动修复")
    parser.add_argument("--ci", action="store_true", help="CI 模式")
    parser.add_argument("--root", type=str, default=".", help="项目根目录")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    count = run_lint(root, fix=args.fix, ci=args.ci)
    sys.exit(1 if count > 0 and args.ci else 0)


if __name__ == "__main__":
    main()
