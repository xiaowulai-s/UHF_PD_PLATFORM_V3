/**
 * 连接 Python sidecar 的 WebSocket 客户端。
 *
 * 两条连接：/stream 收有损遥测（最新帧合并，掉帧属预期，用 seq 跳变自行发现），
 * /ctrl 收无损事件并承载 RPC。渲染进程直连 sidecar，不经过主进程中转——20FPS 的
 * Float32Array 多拷一次是白扔的开销。
 *
 * sidecar 重启后 token 会变，所以端点必须可在运行期替换并重连。
 */

import { decode, encode } from "@msgpack/msgpack";
import { endpointUrl, type Frame, type Hello, type Topic } from "@/shared/protocol";

export interface Endpoint {
  port: number;
  token: string;
}

export type ConnState = "connecting" | "open" | "closed";

type Handler<T> = (payload: T) => void;

interface Pending {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

const RPC_TIMEOUT_MS = 10_000;

abstract class Channel {
  protected ws: WebSocket | null = null;
  protected hello: Hello | null = null;
  private readonly frameHandlers = new Set<Handler<Frame>>();
  private readonly stateHandlers = new Set<Handler<ConnState>>();
  private readonly closeHandlers = new Set<() => void>();
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;

  constructor(
    protected readonly mode: "stream" | "ctrl",
    private topics?: Topic[],
  ) {}

  abstract onMessage(raw: Uint8Array): void;

  get endpoint(): Endpoint | null {
    return this._endpoint ?? null;
  }

  protected _endpoint: Endpoint | null = null;

  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  get advertisedTopics(): Topic[] {
    return this.hello ? (Object.keys(this.hello.topics) as Topic[]) : [];
  }

  connect(endpoint: Endpoint): void {
    this._endpoint = endpoint;
    this.open();
  }

  private open(): void {
    if (this.disposed || !this._endpoint) return;
    this.setState("connecting");
    const ws = new WebSocket(endpointUrl(this._endpoint.port, this.mode, this._endpoint.token));
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.onopen = () => {
      this.attempt = 0;
      if (this.topics?.length) {
        ws.send(encode({ k: "sub", topics: this.topics }));
      }
      this.setState("open");
    };
    ws.onmessage = (ev) => this.onMessage(new Uint8Array(ev.data as ArrayBuffer));
    ws.onclose = () => {
      // 必须置空：`updateEndpoint()` 用 `if (this.ws)` 区分"有活动 socket"与"没有"，
      // 不置空的话已关闭的 socket 会被当成活动的，热切换走进 restart() 那条死路。
      if (this.ws === ws) this.ws = null;
      this.setState("closed");
      for (const fn of this.closeHandlers) fn();
      this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  /**
   * 指数退避重连。sidecar 重启可能要几秒，前端必须自己等回来，
   * 否则现场会出现"界面卡在最后一次数据上"这种最难排查的假死。
   */
  private scheduleReconnect(): void {
    if (this.disposed || !this._endpoint) return;
    this.attempt += 1;
    const delay = Math.min(500 * 2 ** (this.attempt - 1), 8000);
    this.reconnectTimer = setTimeout(() => this.open(), delay);
  }

  /** sidecar 重启后凭证变化时热切换端点，不断加重连退避计数。 */
  updateEndpoint(endpoint: Endpoint): void {
    this._endpoint = endpoint;
    this.attempt = 0;
    // 没有活动 socket 时不能只靠 restart()：它只做 ws?.close()，重连是靠 onclose
    // 回调链触发的；socket 从未建立的话那条链根本不会启动，端点被静默丢弃、
    // 界面永远停在"未连接"。首帧端点晚于页面加载到达时就是这个情况。
    if (this.ws) this.restart();
    else if (!this.disposed) this.open();
  }

  private restart(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const ws = this.ws;
    this.ws = null;
    // 对**已经关闭**的 socket 再调 close() 是空操作，onclose 也不会再触发一次。
    // 于是"清掉重连定时器 + 关一个已关的 socket"= 什么都没做，连接永久停在 closed，
    // 界面再也不恢复。端点热切换时正好会踩到：主进程推新凭证的那一刻，旧 socket
    // 可能已经因为内核退出而关掉了。实测 M7.4：旧 socket 还开着时 4.0~4.3s 恢复，
    // 已经关掉时 30s 都不恢复。
    if (ws && ws.readyState !== WebSocket.CLOSED) ws.close();
    else if (!this.disposed) this.open();
  }

  onFrame(fn: Handler<Frame>): () => void {
    this.frameHandlers.add(fn);
    return () => this.frameHandlers.delete(fn);
  }

  onStateChange(fn: Handler<ConnState>): () => void {
    this.stateHandlers.add(fn);
    return () => this.stateHandlers.delete(fn);
  }

  onClose(fn: () => void): () => void {
    this.closeHandlers.add(fn);
    return () => this.closeHandlers.delete(fn);
  }

  protected emitFrame(frame: Frame): void {
    for (const fn of this.frameHandlers) fn(frame);
  }

  protected setState(state: ConnState): void {
    for (const fn of this.stateHandlers) fn(state);
  }

  dispose(): void {
    this.disposed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
  }
}

function toFrame(body: Record<string, unknown>): Frame | null {
  const kind = body.k;
  if (kind !== "tele" && kind !== "event") return null;
  return body as unknown as Frame;
}

class StreamChannel extends Channel {
  onMessage(raw: Uint8Array): void {
    const body = decode(raw) as Record<string, unknown>;
    if (body.k === "hello") {
      this.hello = body as unknown as Hello;
      return;
    }
    const frame = toFrame(body);
    if (frame) this.emitFrame(frame);
  }
}

class CtrlChannel extends Channel {
  private seq = 0;
  private readonly pending = new Map<string, Pending>();

  onMessage(raw: Uint8Array): void {
    const body = decode(raw) as Record<string, unknown>;
    if (body.k === "hello") {
      this.hello = body as unknown as Hello;
      return;
    }
    if (body.k !== "result") {
      const frame = toFrame(body);
      if (frame) this.emitFrame(frame);
      return;
    }
    const id = String(body.i ?? "");
    const entry = this.pending.get(id);
    if (!entry) return;
    this.pending.delete(id);
    clearTimeout(entry.timer);
    if (body.ok) entry.resolve(body.d);
    else entry.reject(new Error(String(body.e ?? "RPC 失败")));
  }

  call<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    if (!this.isOpen) return Promise.reject(new Error(`sidecar 未连接，无法调用 ${method}`));
    const id = `r${++this.seq}`;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} 超时 (${RPC_TIMEOUT_MS}ms)`));
      }, RPC_TIMEOUT_MS);
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
      this.ws?.send(encode({ k: "call", i: id, m: method, p: params }));
    });
  }

  override onClose(fn: () => void): () => void {
    return super.onClose(() => {
      // 连接断掉时挂起的调用永远不会回来，必须就地失败
      for (const [id, entry] of this.pending) {
        clearTimeout(entry.timer);
        entry.reject(new Error(`连接中断，${id} 调用取消`));
        this.pending.delete(id);
      }
      fn();
    });
  }
}

export class SidecarClient {
  readonly stream: StreamChannel;
  readonly ctrl: CtrlChannel;

  constructor(topics?: Topic[]) {
    this.stream = new StreamChannel("stream", topics);
    this.ctrl = new CtrlChannel("ctrl");
  }

  connect(endpoint: Endpoint): void {
    this.stream.connect(endpoint);
    this.ctrl.connect(endpoint);
  }

  /** sidecar 重启后 token 变化时由主进程推给渲染层，再调这里热切换。 */
  retarget(endpoint: Endpoint): void {
    this.stream.updateEndpoint(endpoint);
    this.ctrl.updateEndpoint(endpoint);
  }

  dispose(): void {
    this.stream.dispose();
    this.ctrl.dispose();
  }
}

export type { Channel };
