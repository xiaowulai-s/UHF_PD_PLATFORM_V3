<script setup lang="ts">
import PrpdChart from "@/renderer/components/charts/prpd-chart.vue";
import PrpsChart from "@/renderer/components/charts/prps-chart.vue";
import FftChart from "@/renderer/components/charts/fft-chart.vue";
import WaveformChart from "@/renderer/components/charts/waveform-chart.vue";
</script>

<template>
  <div class="grid h-full min-h-0 grid-cols-2 grid-rows-2 gap-2">
    <div class="flex min-h-0 flex-col rounded-lg border border-app-border bg-app-surface p-2">
      <WaveformChart />
    </div>
    <div class="flex min-h-0 flex-col rounded-lg border border-app-border bg-app-surface p-2">
      <FftChart />
    </div>
    <div class="flex min-h-0 flex-col rounded-lg border border-app-border bg-app-surface p-2">
      <PrpdChart />
    </div>
    <div class="flex min-h-0 flex-col rounded-lg border border-app-border bg-app-surface p-2">
      <PrpsChart />
    </div>
  </div>
</template>
