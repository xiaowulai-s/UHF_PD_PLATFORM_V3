/**
 * 界面偏好存储。
 *
 * 这些项（语言/时区/日期格式/显示密度/通知频率/系统名称）旧版走 QSettings，
 * 不在 SQLite 里，也没有对应的 sidecar RPC —— 它们只影响前端呈现，所以留在
 * localStorage，不假装是后端配置。
 *
 * 报警阈值不在此列：那属于内核状态，必须走 config.get_thresholds /
 * config.set_amplitude_threshold，否则前端显示的值和实际判定会分家。
 */

import { reactive, watch } from "vue";

const KEY = "uhf-pd.prefs.v1";

export interface Prefs {
  systemName: string;
  language: "zh-CN" | "en";
  timezone: string;
  dateFormat: string;
  density: "realtime" | "comfortable";
  notifyFrequency: "realtime" | "5m" | "15m" | "hourly" | "daily";
  /**
   * 主题。
   *
   * 落地方式是 `<html data-theme="dark">`：`styles/tokens.css` 里
   * `[data-theme="dark"]` 覆盖同一批底层令牌，所以组件不需要知道主题存在。
   * `chart-theme.ts` 的 MutationObserver 也监听这个属性，canvas 与 WebGL 的取色
   * 缓存靠它失效——**但 WebGL 的材质不是自动的**，`prps-chart` / `prpd-3d` 各自
   * 订阅了 `chartTheme()` 重建材质，否则切主题后参照框会停在旧颜色上。
   */
  theme: "light" | "dark";
  /**
   * AE 传感器阵列（位置，米）。
   *
   * 放在这里而不是后端：全仓库没有传感器阵列的 RPC，旧版
   * `sensor_array_config_dialog.py:364` 也只返回一个内存 dict（重启即丢），
   * 而它只影响前端的定位精度图，不参与任何判定——与其它 prefs 同性质。
   * 消费方必须先用 `sanitizeSensors()` 收敛：localStorage 里的值不可信。
   */
  aeSensors: { id: string; x: number; y: number }[];
}

export const DEFAULT_PREFS: Prefs = {
  systemName: "局部放电监测系统",
  language: "zh-CN",
  timezone: "Asia/Shanghai",
  dateFormat: "YYYY-MM-DD",
  density: "realtime",
  notifyFrequency: "5m",
  theme: "light",
  aeSensors: [],
};

function load(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT_PREFS };
    const parsed = JSON.parse(raw) as Partial<Prefs>;
    // 只接受已知键，避免旧版本残留或手改存储塞进垃圾字段
    const out = { ...DEFAULT_PREFS };
    for (const key of Object.keys(DEFAULT_PREFS) as (keyof Prefs)[]) {
      const value = parsed[key];
      if (value !== undefined) out[key] = value as never;
    }
    return out;
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

export const prefs = reactive<Prefs>(load());

/**
 * 窗口底色。
 *
 * 主进程读不到 CSS 变量，所以只能由渲染进程把当前主题的底色推过去（`window:background`）。
 * 这两个值必须与 `styles/tokens.css` 的 `--bg-base-default` 保持一致——改令牌时要一起改，
 * 否则窗口缩放时新暴露的区域会闪出另一种颜色。
 */
const WINDOW_BACKGROUND: Record<Prefs["theme"], string> = { light: "#FFFFFF", dark: "#0E0E10" };

/**
 * 把主题落到 `<html data-theme>`，并把窗口底色同步给主进程。
 *
 * 只在值真的变了时才写属性：`chart-theme.ts` 的 MutationObserver 监听 data-theme，
 * 每次赋值都会让所有图表重建取色缓存；启动时若写一个和现状相同的值，就会白刷一次。
 */
export function applyTheme(theme: string): void {
  const next: Prefs["theme"] = theme === "dark" ? "dark" : "light";
  if (document.documentElement.dataset.theme !== next) {
    document.documentElement.dataset.theme = next;
  }
  // 纯浏览器预览时没有这个桥，静默跳过
  void window.appTheme?.setWindowBackground(WINDOW_BACKGROUND[next]);
}

applyTheme(prefs.theme);
watch(
  () => prefs.theme,
  (next) => applyTheme(next),
);

let wired = false;
export function persistPrefs(): void {
  if (wired) return;
  wired = true;
  watch(
    () => ({ ...prefs }),
    (next) => {
      try {
        localStorage.setItem(KEY, JSON.stringify(next));
      } catch {
        /* 存储被禁用（隐私模式）时静默降级为会话内有效 */
      }
    },
    { deep: true },
  );
}

const ZONE_OFFSET: Record<string, string> = {
  "Asia/Shanghai": "UTC+8",
  "Asia/Tokyo": "UTC+9",
  UTC: "UTC+0",
  "America/New_York": "UTC-5",
};

export const TIMEZONES = Object.entries(ZONE_OFFSET).map(([value, label]) => ({ value, label }));

export const DATE_FORMATS = ["YYYY-MM-DD", "YYYY年MM月DD日", "DD/MM/YYYY", "MM/DD/YYYY"];

/** 按偏好里的日期格式渲染；时间固定 24 小时制 */
export function formatStamp(epochMs: number): string {
  const d = new Date(epochMs);
  const pad = (n: number) => String(n).padStart(2, "0");
  const y = d.getFullYear();
  const m = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  switch (prefs.dateFormat) {
    case "DD/MM/YYYY":
      return `${day}/${m}/${y} ${time}`;
    case "MM/DD/YYYY":
      return `${m}/${day}/${y} ${time}`;
    case "YYYY年MM月DD日":
      return `${y}年${m}月${day}日 ${time}`;
    default:
      return `${y}-${m}-${day} ${time}`;
  }
}
