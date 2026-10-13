/**
 * 热图色表。抽成共享模块是因为 PRPD 主面板与仪表板的 PRPD 快照必须用同一张表——
 * 两处各留一份定义时，改了一处就会出现"同一个矩阵在两个页面上颜色不同"，
 * 而这种不一致在截图上是读不出来的。
 *
 * 表格是 256 项 RGBA 的 Uint8ClampedArray，可直接按 `cell * 4` 索引。
 */

/**
 * jet 颜色表：与旧版 pyqtgraph 用的 matplotlib jet 同一条分段线性定义。
 *
 * 注意 jet(0) 是饱和蓝，而 360×256 的热图里通常 99% 以上的格子为零——
 * 直接把零计数画成 jet(0) 会让"无事件"与"低计数"同色，真实聚集区反而读不出来。
 * 所以调用方对 v===0 必须写 alpha=0（见 paintPrpdCells）。
 */
export const JET: Uint8ClampedArray = (() => {
  const lut = new Uint8ClampedArray(256 * 4);
  const f = (t: number, offset: number) => Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - offset)));
  for (let i = 0; i < 256; i += 1) {
    const t = i / 255;
    lut[i * 4] = Math.round(f(t, 3.5) * 255);
    lut[i * 4 + 1] = Math.round(f(t, 2) * 255);
    lut[i * 4 + 2] = Math.round(f(t, 0.5) * 255);
    lut[i * 4 + 3] = 255;
  }
  return lut;
})();

/**
 * 把 (phase, amplitude) 行主序的量化格子铺进一张 phaseBins×ampBins 的 ImageData。
 *
 * 两个朝向约定都是踩过坑定下来的，别按"看起来更自然"改：
 *  - 索引是 `cells[phase * ampBins + amplitude]`。旧版点击读数按未转置的
 *    `[amp_idx, phase_idx]` 索引、只在显示时 `.T`，读到的是错格子。
 *  - 画布 y=0 在顶部而幅值轴向上增大，所以行要上下翻转（ampBins-1-y）。
 */
export function paintPrpdCells(
  target: ImageData,
  cells: Uint8Array | Float32Array,
  shape: number[],
): void {
  const [phaseBins = 0, ampBins = 0] = shape;
  const px = target.data;
  for (let y = 0; y < ampBins; y += 1) {
    const ampIdx = ampBins - 1 - y;
    for (let x = 0; x < phaseBins; x += 1) {
      const v = cells[x * ampBins + ampIdx] ?? 0;
      const dst = (y * phaseBins + x) * 4;
      if (v === 0) {
        px[dst + 3] = 0;
        continue;
      }
      const src = Math.min(255, Math.round(v)) * 4;
      px[dst] = JET[src] ?? 0;
      px[dst + 1] = JET[src + 1] ?? 0;
      px[dst + 2] = JET[src + 2] ?? 0;
      px[dst + 3] = 255;
    }
  }
}
