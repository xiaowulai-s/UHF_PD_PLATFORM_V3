<script setup lang="ts">
/**
 * PRPS 三维散点面板（M4.5）。
 *
 * 数据是 (cycle × phase) 的幅值矩阵，512×360=184320 格里通常 99% 以上为零，
 * 所以只把非零格送进 GPU 当点画，并预分配缓冲、用 setDrawRange 截断，
 * 避免每帧重建 geometry（那会让 GC 成为卡顿源）。
 *
 * 轨道控制是手写的：three 的 OrbitControls 在 examples/ 子路径下，多引一个入口
 * 就要多算一份打包体积与一条解析路径，而这里只需要"拖拽转 + 滚轮缩"。
 */

import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import * as THREE from "three";
import { chartTheme } from "@/renderer/lib/chart-theme";
import { useFrameDraw } from "@/renderer/lib/use-frame-draw";
import { decodeCells, type Frame } from "@/shared/protocol";

/** 上限按非零格数量截断；超出说明数据密度异常，宁可丢尾巴也不要把 GPU 撑爆 */
const MAX_POINTS = 40000;

const host = ref<HTMLDivElement | null>(null);
const stats = shallowRef({ points: 0, fps: 0, cycles: 0 });
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

/** jet 色带，与 PRPD 面板同一条定义，两图配色才可比 */
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

  // 参考框：x=相位 0~360°，y=幅值，z=周期
  // LineSegments 是**按顶点对**画线的，不是折线；早先直接塞一圈闭合顶点
  // 会被配对成 (0,1)(2,3)… 并在末点与下一组之间连出乱线，所以这里显式列 12 条棱。
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
  // THREE.Color 不认 8 位十六进制的 border 令牌（会 warn 后静默留白），
  // 所以取拆出来的实色 + alpha 两个值。
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
 * 主题切换时重建参照框材质。
 *
 * `chart-theme.ts` 的取色缓存失效是自动的（MutationObserver 监听 <html> 属性），
 * 但 **WebGL 材质不会自己重取**：`buildScene()` 只在挂载时读一次颜色，
 * 不重设就会把参照框停在旧主题的颜色上——浅色背景上的白框几乎看不出来，
 * 而这正是当初 `borderSolid/borderAlpha` 要解决的问题。
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
  const [rows = 512, cols = 360] = decoded.shape;
  // y 用量化格 v/255 就够了：PRPS 只看相对高低，qmax 只在需要标 mV 时才用得上
  let n = 0;
  for (let c = 0; c < rows; c += 1) {
    const z = (c / Math.max(1, rows - 1)) * 2 - 1;
    for (let p = 0; p < cols; p += 1) {
      const v = cells[c * cols + p] ?? 0;
      if (v === 0) continue;
      if (n >= MAX_POINTS) break;
      posArr[n * 3] = (p / Math.max(1, cols - 1)) * 2 - 1;
      posArr[n * 3 + 1] = (v / 255) * 1.2 - 0.6;
      posArr[n * 3 + 2] = z;
      colArr[n * 3] = JET[v * 3] ?? 0;
      colArr[n * 3 + 1] = JET[v * 3 + 1] ?? 0;
      colArr[n * 3 + 2] = JET[v * 3 + 2] ?? 0;
      n += 1;
    }
    if (n >= MAX_POINTS) break;
  }

  const geom = points.geometry;
  geom.setDrawRange(0, n);
  (geom.attributes.position as THREE.BufferAttribute).needsUpdate = true;
  (geom.attributes.color as THREE.BufferAttribute).needsUpdate = true;
  dirty = true;
  stats.value = { points: n, fps: stats.value.fps, cycles: Number(frame.m.total_cycles ?? rows) };
}

useFrameDraw("prps", onFrame);

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
    <div class="flex items-center justify-between gap-2 text-[11px]">
      <div class="flex items-center gap-2">
        <button
          class="rounded border px-2 py-0.5"
          :class="spinning ? 'border-app-primary text-app-primary' : 'border-app-border text-app-muted'"
          @click="spinning = !spinning"
        >
          自转
        </button>
        <span class="tabular text-app-muted">
          {{ stats.points }} 点 / {{ stats.cycles }} 周期 · 拖拽旋转 · 滚轮缩放
        </span>
      </div>
      <!-- 按需渲染：只有新数据或视角变化才画，所以这个数低是设计如此，不是掉帧。
           要看真实上限就开"自转"，那时每帧都画。 -->
      <span class="tabular" :class="spinning && stats.fps < 50 ? 'text-app-warning' : ''">
        渲染 {{ stats.fps }} 次/秒{{ spinning ? "" : "（按需，开自转看上限）" }}
      </span>
    </div>
    <div ref="host" class="relative min-h-0 flex-1 touch-none" @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp" @pointercancel="onPointerUp" @wheel="onWheel" />
  </div>
</template>
