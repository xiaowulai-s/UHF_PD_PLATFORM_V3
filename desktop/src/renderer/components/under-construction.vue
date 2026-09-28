<script setup lang="ts">
defineProps<{ page: string; milestone: string }>();
</script>

<template>
  <div class="flex h-full flex-col items-center justify-center gap-2 text-app-muted">
    <p class="text-sm">{{ page }} 页面待实现</p>
    <p class="text-[12px]">按计划属于 <span class="font-mono text-app-fg">{{ milestone }}</span></p>
  </div>
</template>
