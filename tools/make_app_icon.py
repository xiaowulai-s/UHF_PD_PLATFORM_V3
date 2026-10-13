r"""生成应用图标（.ico / .png），不依赖任何外部图像工具链。

为什么用脚本而不是放一张现成的 PNG：
- Windows 的 .ico 必须内嵌 16/24/32/48/64/128/256 多尺寸，手 P 一张图很容易在
  16px（任务栏小图标、通知区域）下糊成一团；脚本能一次生成全套并保证同源。
- 配色取自 ui 设计令牌 `--color-primary`（#4B3FE3），改品牌色时重跑一次就同步，
  不会出现"图标还是旧色"的情况。

    python tools/make_app_icon.py            # 输出到 desktop/build-resources/

要换成设计师给的正式图标时，直接覆盖那两个文件即可，脚本只是保底。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "desktop" / "build-resources"

# 取自 desktop/src/renderer/styles/tokens.css 的品牌色与状态色
BRAND = (0x4B, 0x3F, 0xE3, 255)  # --color-primary
BRAND_DARK = (0x2E, 0x26, 0xA8, 255)
PULSE = (255, 255, 255, 255)
ICORE_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> Image.Image:
    """在 size x size 上画一枚"局部放电脉冲"标记：圆角方底 + 一条双极尖峰。

    先在 4 倍图上画再缩小，等价于超采样抗锯齿——直接按目标尺寸画线条会有锯齿。
    """
    ss = 4
    w = size * ss
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角方底：半径取 22% 是 Windows 11 应用图标的观感
    radius = int(w * 0.22)
    d.rounded_rectangle([0, 0, w - 1, w - 1], radius=radius, fill=BRAND)

    # 底部压一条深色带，避免纯色块在浅色任务栏上"糊成一片"
    band = int(w * 0.06)
    d.rounded_rectangle([0, w - band * 3, w - 1, w - 1], radius=radius, fill=BRAND_DARK)
    d.rectangle([0, w - band * 2, w - 1, w - 1], fill=BRAND_DARK)

    # 波形：基线 + 一个上冲一个下冲的双极脉冲（局放的典型形状）
    mid = w * 0.52
    x0, x1 = w * 0.12, w * 0.88
    spike_w = max(2, int(w * 0.055))
    amp = w * 0.30

    d.line([(x0, mid), (w * 0.38, mid)], fill=PULSE, width=spike_w)
    d.line([(w * 0.38, mid), (w * 0.50, mid - amp)], fill=PULSE, width=spike_w)
    d.line([(w * 0.50, mid - amp), (w * 0.60, mid + amp * 0.62)], fill=PULSE, width=spike_w)
    d.line([(w * 0.60, mid + amp * 0.62), (w * 0.70, mid)], fill=PULSE, width=spike_w)
    d.line([(w * 0.70, mid), (x1, mid)], fill=PULSE, width=spike_w)

    # 线帽画成圆头，缩小后不会显得断口发方
    r = spike_w // 2
    for cx, cy in ((x0, mid), (x1, mid), (w * 0.50, mid - amp)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=PULSE)

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    master = render(1024)
    png = OUT_DIR / "icon.png"
    master.save(png, "PNG")

    ico = OUT_DIR / "icon.ico"
    # append_images 把各尺寸一起塞进同一个 .ico；Windows 会按上下文挑合适的
    master.save(ico, "ICO", sizes=[(s, s) for s in ICORE_SIZES])

    print(f"已生成 {png.relative_to(REPO)} (1024x1024)")
    print(f"已生成 {ico.relative_to(REPO)} ({', '.join(str(s) for s in ICORE_SIZES)}px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
