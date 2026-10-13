/**
 * AE 传感器阵列与定位精度（GDOP）计算。
 *
 * ## 为什么不用旧版的算法
 *
 * 旧 `ui/widgets/localization_heatmap_widget.py:compute_accuracy_heatmap` 的产物标着
 * 单位"米"，但它是**编造的**：公式 `gdop = sqrt(Σ 1/d_i²)` 既不是 GDOP 也没有物理含义，
 * 而到米的映射是硬编码的 `0.01 + 0.99 * (1 - gdop/max)`——源码注释自己写着
 * "假设最佳位置误差 0.01m，最差位置误差 1m"。把它照搬过来就是交付一个会说谎的仪表。
 *
 * 这里换成**真正的 2D TDOA GDOP**（几何精度因子）：
 *
 *     A 的第 i 行 = ∇(d_i - d_0) = u_i - u_0      （u 是指向传感器的单位向量）
 *     F = Aᵀ A （2×2）
 *     GDOP = sqrt(trace(F⁻¹))
 *
 * GDOP 是**无量纲**的：它只描述"传感器几何把测距误差放大了几倍"，与具体测距精度无关。
 * 所以这里既不乘也不加任何"米"，色带与读数都只给 GDOP 本身。
 *
 * ## 它为什么只依赖几何
 *
 * 真实声源定位需要**多传感器到达时差**，而协议里的 `ae_hit` 帧不带 sensor_id 与到达时刻，
 * 内核侧 `AELocalizer.locate()` 在生产路径上也没有调用点。所以这一页只能给"这套阵列摆法
 * 能到什么精度"，给不了"这次放电在哪"——后者要先把多通道到达时刻接进内核。
 */

export interface AeSensor {
  /** 传感器编号，显示用（如 S1） */
  id: string;
  /** 位置，米 */
  x: number;
  y: number;
}

/** 默认阵列：1m 见方的四角。间距是现场布置项，这里只给一个能画出图的初值。 */
export const DEFAULT_SENSORS: AeSensor[] = [
  { id: "S1", x: -0.5, y: -0.5 },
  { id: "S2", x: 0.5, y: -0.5 },
  { id: "S3", x: 0.5, y: 0.5 },
  { id: "S4", x: -0.5, y: 0.5 },
];

/**
 * 把任意来源（localStorage 里的旧值、手改的存储）收敛成合法阵列。
 *
 * 不信任输入：NaN 坐标会让整张图变成 NaN，缺 id 会让 key 重复。坏项直接丢掉而不是
 * 补 0——补 0 会把一个坏坐标伪装成一个合法的原点传感器。
 */
export function sanitizeSensors(raw: unknown): AeSensor[] {
  if (!Array.isArray(raw)) return DEFAULT_SENSORS.map((s) => ({ ...s }));
  const out: AeSensor[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null) continue;
    const { id, x, y } = item as Partial<AeSensor>;
    if (typeof x !== "number" || !Number.isFinite(x)) continue;
    if (typeof y !== "number" || !Number.isFinite(y)) continue;
    out.push({ id: typeof id === "string" && id.trim() ? id.trim() : `S${out.length + 1}`, x, y });
  }
  return out.length ? out : DEFAULT_SENSORS.map((s) => ({ ...s }));
}

export interface GdopGrid {
  n: number;
  bounds: { xMin: number; xMax: number; yMin: number; yMax: number };
  /** 行主序 n×n；几何退化处为 NaN（不是 0——0 会被读成"精度极好"） */
  values: Float32Array;
  /** 有限值里的最小/最大值，供色带定标 */
  min: number;
  max: number;
  /** 最小 GDOP 的格点，即这套阵列的最佳定位区 */
  best: { x: number; y: number; gdop: number } | null;
}

/**
 * 计算 GDOP 网格。
 *
 * 少于 3 个传感器返回 null：2D TDOA 以 1 号阵元为参考，N 个阵元只有 N-1 个方程，
 * 解 2 个未知数至少要 3 个阵元。这不是"精度差"，是**无解**，所以给 null 而不是一张全 0 的图。
 */
export function computeGdop(sensors: AeSensor[], n = 48, pad = 0.5): GdopGrid | null {
  const pts = sensors.filter((s) => Number.isFinite(s.x) && Number.isFinite(s.y));
  if (pts.length < 3) return null;

  const xs = pts.map((s) => s.x);
  const ys = pts.map((s) => s.y);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 1e-6);
  const margin = span * pad;
  const bounds = {
    xMin: Math.min(...xs) - margin,
    xMax: Math.max(...xs) + margin,
    yMin: Math.min(...ys) - margin,
    yMax: Math.max(...ys) + margin,
  };

  const values = new Float32Array(n * n);
  let min = Infinity;
  let max = -Infinity;
  let best: GdopGrid["best"] = null;

  for (let iy = 0; iy < n; iy += 1) {
    const py = bounds.yMin + ((bounds.yMax - bounds.yMin) * iy) / (n - 1);
    for (let ix = 0; ix < n; ix += 1) {
      const px = bounds.xMin + ((bounds.xMax - bounds.xMin) * ix) / (n - 1);
      const g = gdopAt(pts, px, py);
      values[iy * n + ix] = g;
      if (!Number.isFinite(g)) continue;
      if (g < min) min = g;
      if (g > max) max = g;
      if (!best || g < best.gdop) best = { x: px, y: py, gdop: g };
    }
  }
  if (!Number.isFinite(min)) return null;
  return { n, bounds, values, min, max, best };
}

/** 单点 GDOP。退化处返回 NaN，让调用方显式处理，而不是拿到一个假的有限值。 */
export function gdopAt(sensors: AeSensor[], px: number, py: number): number {
  const ux: number[] = [];
  const uy: number[] = [];
  for (const s of sensors) {
    const dx = s.x - px;
    const dy = s.y - py;
    const d = Math.hypot(dx, dy);
    // 格点正好落在阵元上时方向未定义
    if (d < 1e-9) return Number.NaN;
    ux.push(dx / d);
    uy.push(dy / d);
  }
  const rx = ux[0] ?? 0;
  const ry = uy[0] ?? 0;
  let a11 = 0;
  let a12 = 0;
  let a22 = 0;
  for (let i = 1; i < ux.length; i += 1) {
    const dx = (ux[i] ?? 0) - rx;
    const dy = (uy[i] ?? 0) - ry;
    a11 += dx * dx;
    a12 += dx * dy;
    a22 += dy * dy;
  }
  const det = a11 * a22 - a12 * a12;
  // 退化（所有阵元与格点近共线）时 F 不可逆，GDOP → ∞。给 NaN 而不是一个大数：
  // 大数会被色带当成"最差点"从而拉平整张图的对比度。
  if (!(det > 1e-12)) return Number.NaN;
  // F = AᵀA 为 2×2，trace(F⁻¹) = (a11 + a22) / det
  return Math.sqrt((a11 + a22) / det);
}
