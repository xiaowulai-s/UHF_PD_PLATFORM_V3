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

/** 负载是 [频率, 幅值] 交错的 (N,2) 行主序数组 */
export interface FftMeta extends ArrayMeta {
  sensor_type: string;
  peaks: FftPeak[];
  noise_floor: number;
  snr_db: number;
}

export interface PrpdEvent {
  phase: number;
  amplitude: number;
  polarity: number;
  energy: number;
  cycle: number;
}

export interface PrpdMeta extends ArrayMeta {
  max_amplitude: number;
  min_amplitude: number;
  total_events: number;
  events: PrpdEvent[];
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
  transport?: { sent_frames: number; dropped_frames: number; clients: number; uptime_s: number };
}

const ELEMENT_SIZE: Record<DTypeCode, number> = { f4: 4, f8: 8, i4: 4, i2: 2, i1: 1, u1: 1 };

/**
 * 把帧负载还原成类型化数组。
 *
 * 返回的视图可能.byteOffset 不为 0（msgpack 把整包 buffer 直接切片交给我们），
 * 所以调用方要用返回对象的 byteOffset/length 而不是假定从 0 开始。
 */
export function toFloat32(frame: Frame): Float32Array {
  if (!frame.p) return new Float32Array(0);
  const meta = frame.m as unknown as ArrayMeta;
  if (meta.dtype !== "f4") {
    throw new Error(`topic ${frame.t} 期望 dtype=f4，实际 ${meta.dtype}`);
  }
  if (meta.endian && meta.endian !== "le") {
    throw new Error(`不支持的字节序: ${meta.endian}`);
  }
  const bytes = frame.p;
  return new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 4);
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
