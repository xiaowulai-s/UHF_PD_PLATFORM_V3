/**
 * 把设计令牌读成图表库能用的具体色值与字体。
 *
 * uPlot 的轴/网格/曲线颜色只接受具体字符串（它直接往 canvas 上填），不吃 CSS 变量，
 * 所以令牌必须从 :root 上取下来。取一次就缓存：getComputedStyle 会强制样式重算，
 * 每帧每个颜色都调的话是实打实的布局开销。
 *
 * 暗色主题（M3.5）上线后令牌会变，因此观察 <html> 上的属性变化来失效缓存——
 * 主题切换走 data-theme 属性，不需要图表自己重新挂载。
 */

import { ref } from "vue";

const TOKEN_VARS = {
  fg: "--color-app-fg",
  muted: "--color-app-muted",
  border: "--color-app-border",
  surface: "--color-app-surface",
  primary: "--color-app-primary",
  success: "--color-app-success",
  warning: "--color-app-warning",
  error: "--color-app-error",
  info: "--color-app-info",
  fontSans: "--font-family-default",
  fontMono: "--font-family-mono",
} as const;

type TokenKey = keyof typeof TOKEN_VARS;

export interface ChartTheme extends Record<TokenKey, string> {
  /** 轴标签用正文族，数值刻度用等宽族，避免刷新时数字宽度跳动 */
  axisFont: string;
  /** 数值刻度用等宽族 */
  tickFont: string;
  /** 网格线色，见 build() 上方关于令牌取值形态的说明 */
  grid: string;
  /** border 去掉 alpha 后的实色，供只认 #rgb/#rrggbb 的消费者（如 THREE.Color）使用 */
  borderSolid: string;
  /** 与 borderSolid 配套的 alpha（0~1）；WebGL 侧要透明就得自己开 transparent */
  borderAlpha: number;
}

let cached: ChartTheme | null = null;
const version = ref(0);

/**
 * 网格色直接沿用 border 令牌。
 *
 * 这里原本想把 surface 与 border 混成一个更淡的实色，但 border 令牌带 alpha——
 * 实测 getComputedStyle 给出的是 **8 位十六进制 `#7373731f`**（末两位 1f = 31/255 ≈ 0.12），
 * 而不是 rgba() 写法。十六进制解析会得到 NaN，拼出 `rgb(NaN, NaN, NaN)`，而 canvas
 * 对非法颜色的处理是**静默保留上一次的 fillStyle**。结果是网格线用最后一次设置的
 * 颜色画出来，从截图上完全看不出问题，只会在某些配色下"恰好正常"。
 * canvas 的 strokeStyle 本来就吃带 alpha 的颜色，不需要混色。
 *
 * 但 WebGL 侧不吃：`THREE.Color` 的正则只接受 3/6 位十六进制，喂 8 位会 warn
 * "Invalid hex color" 然后**静默留在默认白色**——PRPS 参照框因此一直是白色，
 * 与设计的淡灰完全不同，而截图上白框配浅背景几乎看不出来。所以额外导出
 * borderSolid + borderAlpha，让只认实色的消费者显式拿颜色与透明度两个值。
 */
function build(): ChartTheme {
  const cs = getComputedStyle(document.documentElement);
  const raw = (k: TokenKey) => cs.getPropertyValue(TOKEN_VARS[k]).trim();
  const t = {} as ChartTheme;
  for (const k of Object.keys(TOKEN_VARS) as TokenKey[]) t[k] = raw(k);
  t.axisFont = `11px ${t.fontSans}`;
  t.tickFont = `10px ${t.fontMono}`;
  t.grid = t.border;
  const m = /^#([0-9a-f]{6})([0-9a-f]{2})$/i.exec(t.border);
  const [, rgb = "", alphaHex = ""] = m ?? [];
  t.borderSolid = rgb ? `#${rgb}` : t.border;
  t.borderAlpha = rgb ? Number.parseInt(alphaHex, 16) / 255 : 1;
  return t;
}

export function chartTheme(): ChartTheme {
  // version 进依赖追踪：主题变化时读它的组件会重渲染，图表在各自的 watch 里重设颜色
  void version.value;
  if (!cached) cached = build();
  return cached;
}

/** 主题切换或令牌改动后强制下次重读。 */
export function invalidateChartTheme(): void {
  cached = null;
  version.value += 1;
}

if (typeof MutationObserver !== "undefined") {
  new MutationObserver(invalidateChartTheme).observe(document.documentElement, {
    attributeFilter: ["class", "style", "data-theme"],
  });
}
