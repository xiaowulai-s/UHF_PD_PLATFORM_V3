<script setup lang="ts">
/**
 * uPlot 实例的生命周期封装：测量容器、建图、跟随尺寸、卸载销毁。
 *
 * 只解决"每张图都要重做一遍的事"：uPlot 要显式 CSS 像素尺寸（width/height 在
 * Options 里是必填），而容器尺寸要等挂载后才知道；DPR 由 uPlot 自己按
 * devicePixelRatio 放大 canvas，我们只报 CSS 像素。
 *
 * options 变化不在此处理——主题切换时由使用方 :key 强制重建，比在组件里做
 * diff/重建状态机小得多，也更不容易留下旧配色。
 */

import { onBeforeUnmount, onMounted, ref } from "vue";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

const props = defineProps<{ options: Omit<uPlot.Options, "width" | "height"> }>();
const emit = defineEmits<{ ready: [plot: uPlot] }>();

const host = ref<HTMLElement | null>(null);
let plot: uPlot | null = null;
let observer: ResizeObserver | null = null;

onMounted(() => {
  const el = host.value;
  if (!el) return;
  plot = new uPlot(
    { ...props.options, width: el.clientWidth, height: el.clientHeight },
    // 空图：两个长度为 0 的列，等使用方 setData。传 [] 会让 uPlot 找不到 x 轴列
    [[], []],
    el,
  );
  emit("ready", plot);

  observer = new ResizeObserver((entries) => {
    const box = entries[0]?.contentRect;
    // 折叠/隐藏时 width 为 0，此时 setSize 会让 uPlot 算出 NaN 刻度
    if (!plot || !box || box.width < 1 || box.height < 1) return;
    plot.setSize({ width: Math.round(box.width), height: Math.round(box.height) });
  });
  observer.observe(el);
});

onBeforeUnmount(() => {
  observer?.disconnect();
  observer = null;
  plot?.destroy();
  plot = null;
});

defineExpose({ plot: () => plot });
</script>

<template>
  <div ref="host" class="h-full w-full" />
</template>
