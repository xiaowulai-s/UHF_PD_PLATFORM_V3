<script setup lang="ts">
/**
 * PRPD 三维视图：相位 × 幅值 × 强度（计数）。
 *
 * 与 PRPS 3D 不是一回事，别混：
 *  - PRPS 3D（`prps-chart.vue`）的轴是 **周期 × 相位 × 幅值**，数据是 cycle×phase 的幅值矩阵；
 *  - 本视图用 PRPD 的 **相位 × 幅值** 计数矩阵，高度编码"该相位-幅值格里的放电次数"。
 *
 * 旧 `ui/widgets/prpd_3d_renderer.py` 虽然叫 PRPD 3D，但轴是 相位/周期/幅值
 * （色条=幅值、周期 %50），docstring 也自称 3D PRPS —— 所以那个不是本视图，
 * 新端 `prps-chart.vue` 已经覆盖了它。本视图是**旧版从来没有过**的东西。
 *
 * **高度取对数**：计数矩阵跨几个数量级（峰值几百，绝大多数非零格是 1~2），
 * 线性高度会把 99% 的点压成贴地的一张毯子，结构完全看不出来。所以 y 用
 * `log1p(count)/log1p(qmax)`，并在标题里写明"对数"。这不等于 PRPD 纵轴那种
 * "自动量程掩盖问题"——那里编码的是**测量量程**，这里编码的是显示强度。
 *
 * 轨道控制与渲染节奏照抄 `prps-chart.vue`：手写球坐标、按需渲染（只有新数据或
 * 视角变化才画），预分配缓冲 + `setDrawRange`，不每帧重建 geometry。
 */

import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import * as THREE from "three";
import { chartTheme } from "@/renderer/lib/chart-theme";
import { useFrameDraw } from "@/renderer/lib/use-frame-draw";
import { decodeCells, type Frame } from "@/shared/protocol";

/** 上限按非零格数量截断；PRPD 矩阵是 360×256=92160 格，超出说明密度异常 */
const MAX_POINTS = 40000;

const host = ref<HTMLDivElement | null>(null);
const stats = shallowRef({ points: 0, peak: 0, bins: "", fps: 0 });
const spinning = ref(false);

let renderer: THREE.WebGLRenderer | null = null;
let scene: THREE.Scene | null = null;
let camera: THREE.PerspectiveCamera | null = null;
let points: THREE.Points | null = null;
let boxLines: THREE.LineSegments | null = null;
let posArr: Float32Array | null = null;
let colArr: Float32Array | null = null;
let raf = 0;
let dirty = false;
let frames = 0;
let resizeObserver: ResizeObserver | null = null;

/** 球坐标轨道：theta 方位角、phi 天顶角、radius 视距 */
const orbit = { theta: 0.9, phi: 1.15, radius: 4.2 };
let dragging = false;
let lastX = 0;
let lastY = 0;

/** jet 色带，与 PRPD 2D 面板同一条定义，两图配色才可比 */
const JET = (() => {
  const lut = new Float32Array(256 * 3);
  const f = (t: number, o: number) => Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - o)));
  for (let i = 0; i < 256; i += 1) {
    const t = i / 255;
    lut[i * 3] = f(t, 3.5);
    lut[i * 3 + 1] = f(t, 2);
    lut[i * 3 + 2] = f(t, 0.5);
  }
  return lut;
})();

function buildScene(): void {
  const el = host.value;
  if (!el) return;
  scene = new THREE.Scene();
  camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1);
  el.appendChild(renderer.domElement);
  renderer.domElement.style.cssText = "position:absolute;inset:0;width:100%;height:100%";

  // 参考框：x=相位 0~360°，y=强度（对数，0~峰值），z=幅值 0~量程上限。
  // LineSegments 按顶点对画线，所以显式列 12 条棱，不能塞一圈闭合顶点。
  const box = new THREE.BufferGeometry();
  const h = 0.6;
  const corners: [number, number, number][] = [
    [-1, -h, -1], [1, -h, -1], [1, -h, 1], [-1, -h, 1],
    [-1, h, -1], [1, h, -1], [1, h, 1], [-1, h, 1],
  ];
  const edges: [number, number][] = [
    [0, 1], [1, 2], [2, 3], [3, 0],
    [4, 5], [5, 6], [6, 7], [7, 4],
    [0, 4], [1, 5], [2, 6], [3, 7],
  ];
  const verts = new Float32Array(edges.length * 2 * 3);
  edges.forEach(([a, b], i) => {
    const va = corners[a]!;
    const vb = corners[b]!;
    verts.set([va[0], va[1], va[2], vb[0], vb[1], vb[2]], i * 6);
  });
  box.setAttribute("position", new THREE.BufferAttribute(verts, 3));
  const t = chartTheme();
  // THREE.Color 不认 8 位十六进制的 border 令牌（会 warn 后静默留白），取实色 + alpha
  const boxMaterial = new THREE.LineBasicMaterial({
    color: new THREE.Color(t.borderSolid),
    transparent: t.borderAlpha < 1,
    opacity: t.borderAlpha,
  });
  boxLines = new THREE.LineSegments(box, boxMaterial);
  scene.add(boxLines);

  posArr = new Float32Array(MAX_POINTS * 3);
  colArr = new Float32Array(MAX_POINTS * 3);
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(posArr, 3));
  geom.setAttribute("color", new THREE.BufferAttribute(colArr, 3));
  geom.setDrawRange(0, 0);
  points = new THREE.Points(
    geom,
    new THREE.PointsMaterial({ size: 0.03, vertexColors: true, sizeAttenuation: true }),
  );
  scene.add(points);
}

/**
 * 主题切换时重建参照框材质（WebGL 不会自己重取颜色，理由见 prps-chart 同名函数）。
 */
function applyTheme(): void {
  const material = boxLines?.material as THREE.LineBasicMaterial | undefined;
  if (material) {
    const t = chartTheme();
    material.color = new THREE.Color(t.borderSolid);
    material.transparent = t.borderAlpha < 1;
    material.opacity = t.borderAlpha;
    material.needsUpdate = true;
  }
  dirty = true;
}

watch(() => chartTheme(), applyTheme);

function applyCamera(): void {
  if (!camera) return;
  const { theta, phi, radius } = orbit;
  camera.position.set(
    radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  );
  camera.lookAt(0, 0, 0);
  dirty = true;
}

function renderLoop(): void {
  raf = requestAnimationFrame(renderLoop);
  if (spinning.value) {
    orbit.theta += 0.006;
    dirty = true;
  }
  if (!dirty || !renderer || !scene || !camera) return;
  renderer.render(scene, camera);
  dirty = false;
  frames += 1;
}

async function onFrame(frame: Frame): Promise<void> {
  if (!points || !posArr || !colArr) return;
  const decoded = await decodeCells(frame);
  const cells = decoded.cells;
  if (!(cells instanceof Uint8Array)) return;
  // PRPD 矩阵是 (相位, 幅值) 行主序：rows=相位箱、cols=幅值箱
  const [rows = 360, cols = 256] = decoded.shape;
  // 反量化上限：帧 meta 的 qmax 是矩阵峰值计数，用它把 0~255 还原成"次数"
  const qmax = Math.max(1, Number((frame.m as { qmax?: number }).qmax ?? 255));
  const logMax = Math.log1p(qmax);

  let n = 0;
  let peak = 0;
  for (let p = 0; p < rows && n < MAX_POINTS; p += 1) {
    const x = (p / Math.max(1, rows - 1)) * 2 - 1;
    for (let a = 0; a < cols; a += 1) {
      const v = cells[p * cols + a] ?? 0;
      if (v === 0) continue;
      if (n >= MAX_POINTS) break;
      if (v > peak) peak = v;
      const count = (v / 255) * qmax;
      posArr[n * 3] = x;
      posArr[n * 3 + 1] = (Math.log1p(count) / logMax) * 1.2 - 0.6;
      posArr[n * 3 + 2] = (a / Math.max(1, cols - 1)) * 2 - 1;
      colArr[n * 3] = JET[v * 3] ?? 0;
      colArr[n * 3 + 1] = JET[v * 3 + 1] ?? 0;
      colArr[n * 3 + 2] = JET[v * 3 + 2] ?? 0;
      n += 1;
    }
  }

  const geom = points.geometry;
  geom.setDrawRange(0, n);
  (geom.attributes.position as THREE.BufferAttribute).needsUpdate = true;
  (geom.attributes.color as THREE.BufferAttribute).needsUpdate = true;
  dirty = true;
  stats.value = { points: n, peak: Math.round((peak / 255) * qmax), bins: `${rows}×${cols}`, fps: stats.value.fps };
}

useFrameDraw("prpd", onFrame);

function onPointerDown(ev: PointerEvent): void {
  dragging = true;
  lastX = ev.clientX;
  lastY = ev.clientY;
  (ev.target as Element).setPointerCapture?.(ev.pointerId);
}

function onPointerMove(ev: PointerEvent): void {
  if (!dragging) return;
  orbit.theta += (ev.clientX - lastX) * 0.008;
  orbit.phi = Math.min(Math.PI - 0.05, Math.max(0.05, orbit.phi + (ev.clientY - lastY) * 0.008));
  lastX = ev.clientX;
  lastY = ev.clientY;
  applyCamera();
}

function onPointerUp(): void {
  dragging = false;
}

function onWheel(ev: WheelEvent): void {
  ev.preventDefault();
  orbit.radius = Math.min(12, Math.max(1.6, orbit.radius * (ev.deltaY > 0 ? 1.1 : 0.9)));
  applyCamera();
}

let fpsTimer = 0;

onMounted(() => {
  buildScene();
  applyCamera();
  const el = host.value;
  if (!el || !renderer) return;
  resizeObserver = new ResizeObserver(() => {
    const w = el.clientWidth;
    const h = el.clientHeight;
    if (w < 1 || h < 1 || !renderer || !camera) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    dirty = true;
  });
  resizeObserver.observe(el);
  raf = requestAnimationFrame(renderLoop);
  fpsTimer = window.setInterval(() => {
    stats.value = { ...stats.value, fps: frames };
    frames = 0;
  }, 1000) as unknown as number;
});

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  window.clearInterval(fpsTimer);
  resizeObserver?.disconnect();
  points?.geometry.dispose();
  boxLines?.geometry.dispose();
  (boxLines?.material as THREE.Material | undefined)?.dispose();
  renderer?.dispose();
  renderer?.domElement.remove();
});
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-1">
    <div class="flex flex-wrap items-center gap-2 text-[11px]">
      <button
        class="rounded border px-2 py-0.5"
        :class="spinning ? 'border-app-primary text-app-primary' : 'border-app-border text-app-muted'"
        @click="spinning = !spinning"
      >
        自转
      </button>
      <span class="tabular text-app-muted">
        {{ stats.points }} 格 / 峰值 {{ stats.peak }} 次 / {{ stats.bins }} · 拖拽旋转 · 滚轮缩放
      </span>
      <span class="text-app-muted">
        轴：X 相位 0–360° · Z 幅值（0–量程上限）· Y 强度（<span class="text-app-fg">对数</span>，越高越多）
      </span>
      <!-- 按需渲染：只有新数据或视角变化才画，所以这个数低是设计如此，不是掉帧 -->
      <span class="tabular ml-auto" :class="spinning && stats.fps < 50 ? 'text-app-warning' : 'text-app-muted'">
        渲染 {{ stats.fps }} 次/秒{{ spinning ? "" : "（按需）" }}
      </span>
    </div>
    <div
      ref="host"
      data-role="prpd-3d"
      class="relative min-h-0 flex-1 touch-none"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
      @wheel="onWheel"
    />
  </div>
</template>
