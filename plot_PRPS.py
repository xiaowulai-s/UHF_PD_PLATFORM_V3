from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def main() -> None:
    # 字体设置（保证中文可显示）
    plt.rcParams["font.sans-serif"] = ["SimHei", "SimSun", "Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False

    # 默认读取的 CSV 文件
    csv_path = Path(__file__).resolve().parent / "PD_data.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"文件不存在: {csv_path}")

    # 读取 CSV（跳过表头）
    data = np.loadtxt(csv_path, delimiter=",", skiprows=1, dtype=np.float64, ndmin=2)
    if data.ndim != 2 or data.shape[1] != 4:
        raise ValueError(f"CSV data shape must be (N,4), got {data.shape} in {csv_path}")

    # 第2列是工频周期，第3列是相位，第4列是放电量（取绝对值）
    raw_cycle = data[:, 1]
    cycle_local = ((raw_cycle - 1) % 50) + 1
    phase = data[:, 2]
    discharge = np.abs(data[:, 3])

    # 创建画布并绘制 PRPS 三维散点
    fig = plt.figure(figsize=(9, 7), facecolor="white")
    ax = fig.add_subplot(111, projection="3d")

    scatter = ax.scatter(
        phase,
        cycle_local,
        discharge,
        s=18,
        c=discharge,
        cmap="jet",
        marker="o",
        edgecolors="none",
    )
    colorbar = fig.colorbar(scatter, ax=ax, shrink=0.74, pad=0.03)
    colorbar.set_label("Discharge (a.u.)")

    # 坐标轴和视角设置
    ax.set_xlabel("相位 (°)")
    ax.set_ylabel("工频周期 (n)")
    ax.set_zlabel("放电量")
    ax.set_xlim(0, 360)
    ax.set_ylim(0, 50)
    ax.set_yticks([0, 10, 20, 30, 40, 50])
    ax.set_zlim(0, max(1000.0, float(np.max(discharge) * 1.05)))
    ax.set_xticks([0, 60, 120, 180, 240, 300, 360])
    ax.view_init(elev=36, azim=-122)
    ax.set_box_aspect((1.45, 1.15, 1.20))
    ax.set_title("PRPS Pattern")

    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
