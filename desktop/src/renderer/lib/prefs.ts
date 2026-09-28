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
}

export const DEFAULT_PREFS: Prefs = {
  systemName: "局部放电监测系统",
  language: "zh-CN",
  timezone: "Asia/Shanghai",
  dateFormat: "YYYY-MM-DD",
  density: "realtime",
  notifyFrequency: "5m",
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
