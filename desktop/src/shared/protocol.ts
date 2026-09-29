/**
 * 跨进程协议契约的前端实现，与 Python 侧 core/protocol/frames.py 手工镜像。
 * 改一边必须改另一边。
 *
 * 线格式：一个 WebSocket 二进制帧 = 一条 msgpack 映射。数值负载走 msgpack 原生
 * bin 类型，@msgpack/msgpack 默认就解成 Uint8Array，无需配 ExtensionCodec。
 */

export const PROTOCOL_VERSION = 1;

export type Qos = "lossy" | "reliable";

export const TOPICS = {
  waveform: "lossy",
  fft: "lossy",
  prpd: "lossy",
  prps: "lossy",
  ae_envelope: "lossy",
  ae_hit: "lossy",
  stats: "lossy",
  log: "lossy",
  alarm: "reliable",
  alarm_cleared: "reliable",
  device_status: "reliable",
  acquisition: "reliable",
  source: "reliable",
} as const satisfies Record<string, Qos>;

export type Topic = keyof typeof TOPICS;

export type DTypeCode = "f4" | "f8" | "i4" | "i2" | "i1" | "u1";

export interface ArrayMeta {
  shape: number[];
  dtype: DTypeCode;
  endian: "le";
  /** 负载经过的压缩算法；目前内核只用 zlib */
  codec?: "zlib";
  /** 线性量化上限，配合 dtype=u1；解码后可反插值回原量纲 */
  qmax?: number;
}

export interface Frame {
  v: number;
  k: "tele" | "event";
  t: Topic;
  d: string;
  c: number;
  q: number;
  ts: number;
  m: Record<string, unknown>;
  p?: Uint8Array;
}

export interface Hello {
  v: number;
  k: "hello";
  mode: "stream" | "ctrl";
  topics: Record<Topic, Qos>;
  rpc: string[];
}

export type RpcResponse<T = unknown> =
  | { v: number; k: "result"; i: string; ok: true; d: T }
  | { v: number; k: "result"; i: string; ok: false; e: string };

export interface WaveformMeta extends ArrayMeta {
  sensor_type: string;
  sample_rate: number;
}

export interface FftPeak {
  frequency: number;
  magnitude: number;
  is_harmonic: boolean;
  harmonic_order: number;
}

export interface FftBand {
  name: string;
  /**
   * 协议内频率统一 Hz（与负载 x 列、FftPeak.frequency 同量纲），显示层自己换算 MHz。
   * Python 侧 BandStats 原本 f_min/f_max 是 MHz 而 peak_frequency 是 Hz，
   * 边界字段带 _hz 后缀就是为了让单位不可猜。
   */
  f_min_hz: number;
  f_max_hz: number;
  energy: number;
  peak_magnitude: number;
  peak_frequency_hz: number;
  avg_magnitude: number;
}

/** 负载是 [频率, 幅值] 交错的 (N,2) 行主序数组 */
export interface FftMeta extends ArrayMeta {
  sensor_type: string;
  peaks: FftPeak[];
  noise_floor: number;
  snr_db: number;
  bands: FftBand[];
}

export interface PrpdEvent {
  phase: number;
  amplitude: number;
  polarity: number;
  energy: number;
  cycle: number;
}

export interface PrpdMeta extends ArrayMeta {
  /** 本窗口观测到的最大事件幅值；不是矩阵的分箱轴上限 */
  max_amplitude: number;
  min_amplitude: number;
  /** 矩阵行的真实分箱轴范围。铺矩阵必须用它：观测值通常远小于它，
   *  拿观测值当轴上限会把事件带纵向压到贴底（实测电晕被压了 4 倍）。 */
  amp_axis_min?: number;
  amp_axis_max?: number;
  /** 相位轴上限（通常 360） */
  phase_axis_max?: number;
  total_events: number;
  events: PrpdEvent[];
  /** 内核按哪种模式算出这张矩阵（heatmap/density/scatter）。密度与热力的差别发生在
   *  服务端 compute 里，前端必须以此为准，不能拿自己按钮的状态当事实。 */
  mode?: string;
  /** 仅在服务端截断时出现，值为截断前的原始条数 */
  events_truncated?: number;
}

export interface LogLine {
  seq: number;
  ts: number;
  level: string;
  logger: string;
  module: string;
  line: number;
  message: string;
}

/**
 * 服务端推流记账，与 Python 侧 SidecarServer.diagnostics() 手工镜像。
 *
 * 三个"没发出去"的计数必须分开看，否则"入帧速率低于管线速率"无法归因：
 * coalesced 是合并窗口内被后帧顶掉，throttled 是被显式速率掐掉，dropped 是背压整批丢弃。
 */
export interface TransportDiagnostics {
  sent_frames: number;
  dropped_frames: number;
  coalesced_frames: number;
  throttled_frames: number;
  sent_by_topic: Partial<Record<Topic, number>>;
  /** topic -> 目标 Hz；缺键表示不限流，跟随管线节奏 */
  topic_rates: Partial<Record<Topic, number>>;
  flush_interval_ms: number;
  clients: number;
  seq: Partial<Record<Topic, number>>;
  uptime_s: number;
}

export interface StatsSnapshot {
  frames_received?: number;
  waveforms_processed?: number;
  pd_events_detected?: number;
  fft_computed?: number;
  errors?: number;
  observed_max_amplitude?: number;
  channels_observed?: number;
  alarms?: Record<string, number>;
  source_count?: number;
  online_count?: number;
  active_source?: string | null;
  sources?: Record<string, { id?: string; name?: string; type?: string; status?: string; generated?: number; fps?: number }>;
  storage?: Record<string, number>;
  transport?: TransportDiagnostics;
}

const ELEMENT_SIZE: Record<DTypeCode, number> = { f4: 4, f8: 8, i4: 4, i2: 2, i1: 1, u1: 1 };

/** 线性量化档位上限：u1 用 255，与 Python 侧 np.iinfo(np.uint8).max 一致。 */
const U1_MAX = 255;

/**
 * 还原 zlib 负载。
 *
 * 用浏览器原生 DecompressionStream 而不是引 fflate：实测 PRPS 单帧 inflate 约
 * 1.1ms、PRPD 约 0.6ms，且解压跑在 Chromium 的非主线程池上，不占 50ms 渲染预算。
 * "deflate" 对应带 zlib 头的格式，正是 Python zlib.compress 的产物。
 */
async function inflate(bytes: Uint8Array): Promise<Uint8Array> {
  const body = new Blob([bytes as BlobPart]).stream().pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(body).arrayBuffer());
}

export interface DecodedArray {
  shape: number[];
  dtype: DTypeCode;
  /** 非 null 表示 cells 是线性量化格子（0..255），真实值 = cell / 255 * quantizedMax */
  quantizedMax: number | null;
  /** 未量化 topic 是原 dtype 视图；量化 topic 是 Uint8Array，可直接索引调色板 */
  cells: Float32Array | Uint8Array;
}

/**
 * 把帧负载解成可直接绘制的数组。异步仅因为 zlib 主题要走 DecompressionStream。
 *
 * 量化 topic 刻意停留在 0..255 不展开：PRPD 展开成 float32 是 360KB、PRPS 是 720KB，
 * 而热图取色根本用不到连续值，白让渲染线程多做一遍分配。要真实量纲调 toValues()。
 */
export async function decodeCells(frame: Frame): Promise<DecodedArray> {
  const meta = frame.m as unknown as ArrayMeta;
  if (meta.endian && meta.endian !== "le") throw new Error(`不支持的字节序: ${meta.endian}`);
  const dtype = meta.dtype ?? "f4";
  const shape = meta.shape ?? [];
  const quantizedMax = typeof meta.qmax === "number" ? meta.qmax : null;

  if (!frame.p) return { shape, dtype, quantizedMax, cells: new Float32Array(0) };

  const bytes = meta.codec === "zlib" ? await inflate(frame.p) : frame.p;
  const cells = viewOf(bytes, dtype);
  if (quantizedMax !== null && dtype !== "u1") {
    throw new Error(`topic ${frame.t} 带 qmax 但 dtype=${dtype}，前端只支持 u1 线性量化`);
  }
  return { shape, dtype, quantizedMax, cells };
}

/** 需要真实量纲时用（游标读数、tooltip）；未量化 topic 返回同一段内存的视图。 */
export function toValues(d: DecodedArray): Float32Array {
  if (d.quantizedMax === null) {
    if (d.cells instanceof Float32Array) return d.cells;
    return Float32Array.from(d.cells as ArrayLike<number>);
  }
  const q = d.cells as Uint8Array;
  const out = new Float32Array(q.length);
  const scale = d.quantizedMax / U1_MAX;
  for (let i = 0; i < q.length; i += 1) out[i] = (q[i] ?? 0) * scale;
  return out;
}

function viewOf(bytes: Uint8Array, dtype: DTypeCode): Float32Array | Uint8Array {
  if (dtype === "u1") return bytes;
  if (dtype !== "f4") throw new Error(`前端暂不支持的 dtype: ${dtype}`);
  // msgpack 把 bin 字段按字节偏移直接从整包 buffer 上切下来，偏移不保证 4 字节对齐，
  // 而不对齐的 Float32Array 构造会抛 RangeError。对齐时零拷贝，否则只切这一段复制。
  const aligned = bytes.byteOffset % 4 === 0 ? bytes : bytes.slice();
  return new Float32Array(aligned.buffer, aligned.byteOffset, aligned.byteLength / 4);
}

/**
 * 未压缩 float32 主题的同步解码（波形/FFT/AE，单帧 16KB 以内）。
 *
 * 压缩主题必须走 decodeCells，这里直接抛错而不是静默解压失败：
 * 线格式上是看不出差别的，误用会解出一片噪声。
 */
export function toFloat32(frame: Frame): Float32Array {
  const meta = frame.m as unknown as ArrayMeta;
  if (meta.dtype !== "f4") {
    throw new Error(`topic ${frame.t} 期望 dtype=f4，实际 ${meta.dtype}`);
  }
  if (meta.codec) {
    throw new Error(`topic ${frame.t} 负载是 ${meta.codec} 压缩的，必须走 decodeCells`);
  }
  if (!frame.p) return new Float32Array(0);
  if (meta.endian && meta.endian !== "le") {
    throw new Error(`不支持的字节序: ${meta.endian}`);
  }
  return viewOf(frame.p, "f4") as Float32Array;
}

/** 按 meta.shape 还原为二维视图（行主序）；一维时返回单行数组的数组。 */
export function toRows(frame: Frame): Float32Array[] {
  const flat = toFloat32(frame);
  const shape = (frame.m as unknown as ArrayMeta).shape ?? [flat.length];
  if (shape.length < 2) return [flat];
  const [rows = 1, cols = 1] = shape;
  const out: Float32Array[] = [];
  for (let r = 0; r < rows; r += 1) {
    out.push(flat.subarray(r * cols, (r + 1) * cols));
  }
  return out;
}

export function elementSize(dtype: DTypeCode): number {
  return ELEMENT_SIZE[dtype];
}

export function endpointUrl(port: number, mode: "stream" | "ctrl", token: string): string {
  return `ws://127.0.0.1:${port}/${mode}?token=${encodeURIComponent(token)}`;
}
