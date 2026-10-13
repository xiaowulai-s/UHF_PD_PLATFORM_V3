/**
 * AE 命中的累计统计：分钟分桶 + 每桶运行聚合。
 *
 * 为什么不直接对 store 里的 `aeHits` 求值——那个列表被 `MAX_AE_HITS = 200` 截断，
 * 命中率一高就饱和："总 Hit 数"会停在一个假上限上，直方图也会随饱和变形。
 * AGENTS.md 里为此专门写过"一个会骗人的指标不如不放"。所以这里做**只增不减的累计
 * 聚合**，列表只用来显示明细行。
 *
 * 分桶按分钟：1440 个桶正好覆盖 24 小时，也就撑得起 1/4/8/24 小时四个时窗；
 * 每桶存 count 与各特征量的 sum/max，于是"均值"= sum/count、"最大"= max-of-max，
 * 换时窗只是换求和范围，不必保留原始点。
 *
 * 刻意不做成响应式：命中能到 40+/秒，逐条触发重算不划算。视图用 1 秒定时器读一次
 * （与仪表板快照同一套节奏）。
 */

import type { AeHit } from "@/shared/protocol";

/**
 * 参与"均值 / 最大"统计的特征量。
 *
 * 单位是**内核字段的物理含义**，不是设计稿上写的那些：`ae.html` 的表头写的是
 * dB / mJ / ms，而内核给的是 mV / µs / kHz / 原始能量。照抄设计稿的单位等于替
 * 未标定的量纲下结论（幅值单位至今没有现场标定，见 AGENTS.md）。
 */
export const AE_FIELDS: { key: keyof AeHit; label: string; unit: string; digits: number }[] = [
  { key: "amplitude", label: "幅值", unit: "mV", digits: 1 },
  { key: "asl_db", label: "ASL", unit: "dB", digits: 1 },
  { key: "rise_time_us", label: "上升时间", unit: "µs", digits: 1 },
  { key: "duration_us", label: "持续时间", unit: "µs", digits: 1 },
  { key: "counts", label: "振铃计数", unit: "", digits: 0 },
  { key: "avg_frequency_khz", label: "平均频率", unit: "kHz", digits: 1 },
  { key: "marse_energy", label: "MARSE 能量", unit: "", digits: 2 },
  { key: "energy", label: "能量", unit: "", digits: 2 },
];

/** 一小时的桶数上限，四个时窗（1/4/8/24h）里最大的是 24h */
const BUCKET_S = 60;
const MAX_BUCKETS = 24 * 60;

interface Bucket {
  /** 该分钟起始的 epoch 秒 */
  t: number;
  count: number;
  /** key -> 累计值，用于算均值 */
  sum: Record<string, number>;
  /** key -> 最大值 */
  max: Record<string, number>;
  /** 持续时长合计（秒） */
  durationS: number;
}

/** 按 `${device}:${channel}` 分组的桶序列。每路各留 24 小时，量级可忽略。 */
const buckets = new Map<string, Bucket[]>();

function keyOf(device: string, channel: number): string {
  return `${device}:${channel}`;
}

/** 供视图列出"库里出现过哪些通道"。 */
export function aeChannelKeys(): string[] {
  return [...buckets.keys()].sort();
}

export function recordAeHit(hit: AeHit, device: string, channel: number): void {
  if (!hit || typeof hit.amplitude !== "number") return;
  const key = keyOf(device, channel);
  let list = buckets.get(key);
  if (!list) {
    list = [];
    buckets.set(key, list);
  }
  const t = Math.floor(Date.now() / 1000 / BUCKET_S) * BUCKET_S;
  let bucket = list[list.length - 1];
  if (!bucket || bucket.t !== t) {
    bucket = { t, count: 0, sum: {}, max: {}, durationS: 0 };
    list.push(bucket);
    if (list.length > MAX_BUCKETS) list.splice(0, list.length - MAX_BUCKETS);
  }
  bucket.count += 1;
  for (const f of AE_FIELDS) {
    const v = hit[f.key];
    if (typeof v !== "number" || !Number.isFinite(v)) continue;
    bucket.sum[f.key] = (bucket.sum[f.key] ?? 0) + v;
    bucket.max[f.key] = Math.max(bucket.max[f.key] ?? -Infinity, v);
  }
  // 持续时长单独合计成秒：设计稿的"持续时长"卡是累计量，不是均值
  const dur = hit.duration_us;
  if (typeof dur === "number" && Number.isFinite(dur)) bucket.durationS += dur / 1e6;
}

export interface AeAggregate {
  count: number;
  /** 各特征量的均值（仅统计到有效值的条目） */
  mean: Record<string, number>;
  /** 各特征量的最大值 */
  max: Record<string, number>;
  /** 各特征量的合计（"总能量"这类累计卡片用） */
  sum: Record<string, number>;
  /** 持续时长合计（秒） */
  durationS: number;
  /** 有命中的分钟桶数，用来判断样本够不够 */
  buckets: number;
}

/**
 * 汇总最近 `windowS` 秒。
 *
 * 空窗口返回全 0 而不是 null：调用方要区分"没有数据"和"统计失败"，前者由
 * `count === 0` 表达就够了，不需要再加一个空值分支。
 */
export function aeAggregate(windowS: number, keys: string[] = [...buckets.keys()]): AeAggregate {
  const since = Math.floor(Date.now() / 1000 / BUCKET_S) * BUCKET_S - windowS;
  const out: AeAggregate = { count: 0, mean: {}, max: {}, sum: {}, durationS: 0, buckets: 0 };
  const sum: Record<string, number> = {};
  for (const key of keys) {
    for (const b of buckets.get(key) ?? []) {
      if (b.t < since) continue;
      out.count += b.count;
      out.buckets += 1;
      out.durationS += b.durationS;
      for (const f of AE_FIELDS) {
        const s = b.sum[f.key];
        if (s !== undefined) sum[f.key] = (sum[f.key] ?? 0) + s;
        const m = b.max[f.key];
        if (m !== undefined) out.max[f.key] = Math.max(out.max[f.key] ?? -Infinity, m);
      }
    }
  }
  out.sum = sum;
  for (const f of AE_FIELDS) {
    const s = sum[f.key];
    // 均值口径说明：分母是"该字段有有效值的条数"而不是总命中数。内核的 hit 特征量
    // 允许缺失（取不到就是 None），拿总命中数当分母会把缺失当成 0 拉低均值。
    const n = countWithField(keys, since, f.key);
    if (s !== undefined && n > 0) out.mean[f.key] = s / n;
  }
  return out;
}

function countWithField(keys: string[], since: number, field: string): number {
  let n = 0;
  for (const key of keys) {
    for (const b of buckets.get(key) ?? []) {
      if (b.t < since) continue;
      if (b.sum[field] !== undefined) n += b.count;
    }
  }
  return n;
}

export interface AeHistogram {
  /** 每根柱子的起始时间（epoch 秒） */
  t: number[];
  /** 每根柱子的命中数 */
  count: number[];
}

/** 按 `stepS` 秒一桶出直方图，覆盖最近 `windowS` 秒。空桶补 0，不留断口。 */
export function aeHistogram(windowS: number, stepS: number, keys: string[] = [...buckets.keys()]): AeHistogram {
  const now = Math.floor(Date.now() / 1000 / stepS) * stepS;
  const bars = Math.max(1, Math.floor(windowS / stepS));
  const start = now - (bars - 1) * stepS;
  const counts = new Array<number>(bars).fill(0);
  for (const key of keys) {
    for (const b of buckets.get(key) ?? []) {
      if (b.t < start) continue;
      const idx = Math.min(bars - 1, Math.max(0, Math.round((b.t - start) / stepS)));
      counts[idx] = (counts[idx] ?? 0) + b.count;
    }
  }
  return { t: counts.map((_, i) => start + i * stepS), count: counts };
}

/** 仅供测试与调试：清空聚合。 */
export function resetAeStats(): void {
  buckets.clear();
}
