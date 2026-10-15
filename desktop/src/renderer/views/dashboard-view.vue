<script setup lang="ts">
/**
 * 仪表板（M5.3）：指标卡 + 三类快照 + 最新告警 + 跨进程边界吞吐。
 *
 * 需求来源：设计稿 `.design/uhf-pd-light/pages/dashboard.html` 是空壳
 * （28,958 字节里只有"首页"两个可见字，无 table/input/select），所以按旧版
 * `ui/pages/dashboard_page.py` 的口径推导：5 张指标卡（在线设备 / 局放总数 /
 * 报警总数 / 今日最大幅值 / AE Hit 次数）+ 三类快照（波形 / PRPD / FFT）
 * + 一条最新告警。吞吐表是 M4.6 的既有交付，保留。
 *
 * 幅值那张卡刻意不写 "mV"：全链路没有计数→mV 换算，真机是原始 int16 计数、
 * 模拟器是 mV，两者相差约百倍（AGENTS.md「单位口径本身是缺的」）。标一个
 * 未标定的单位等于让仪表说谎，所以只给数、把口径写进 title。
 */

import { computed } from "vue";
import { aeHits, alarms, frameCount, latestActiveAlarm, stats, streamState } from "@/renderer/lib/store";
import { TOPICS, type Topic } from "@/shared/protocol";
import SnapshotMini from "@/renderer/components/charts/snapshot-mini.vue";

interface Card {
  label: string;
  value: string;
  suffix: string;
  tone: string;
  hint: string;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function fmt(v: number | null): string {
  if (v === null) return "—";
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

const CARDS = computed<Card[]>(() => {
  const s = stats.value;
  const alarmTotal = num(s?.alarms?.total_alarms);
  return [
    {
      label: "在线数据源",
      value: fmt(num(s?.online_count)),
      suffix: "路",
      tone: "text-app-success",
      hint: "状态属于 ONLINE_STATES 的数据源计数，随 stats 帧每 2 秒更新",
    },
    {
      label: "局放总数",
      value: fmt(num(s?.pd_events_detected)),
      suffix: "次",
      tone: "text-app-primary",
      hint: "内核累计检出的事件数（自内核启动起，不是本页打开后）",
    },
    {
      label: "报警总数",
      value: fmt(alarmTotal),
      suffix: "条",
      tone: "text-app-error",
      hint: "PDAlarmService.get_stats() 的 total_alarms；另可看当前活跃 active_count",
    },
    {
      label: "最大幅值",
      value: fmt(num(s?.observed_max_amplitude)),
      suffix: "",
      tone: "text-app-warning",
      hint: "单位未标定：真机是原始 int16 计数、模拟器是 mV，量纲相差约百倍，需现场标定后才能写 mV",
    },
    {
      label: "AE 命中",
      value: fmt(frameCount.ae_hit ?? null),
      suffix: "条",
      tone: "text-app-info",
      hint: `客户端实际收到的 ae_hit 帧数（当前缓存 ${aeHits.value.length} 条）；ae_hit 是有损 topic，这个数会低于内核真实命中数`,
    },
  ];
});

/** 最新一条告警：实时帧按到达顺序前插，所以第 0 条就是最新 */
const LEVEL_TONE: Record<string, string> = {
  critical: "text-app-error",
  high: "text-app-error",
  warning: "text-app-warning",
  medium: "text-app-warning",
  info: "text-app-muted",
  low: "text-app-muted",
};

const latestAlarm = computed(() => {
  // 取最新一条**尚未消除**的：内核发了 alarm_cleared 的报警条件已消失，
  // 挂在"最新告警"卡上会让人以为还在报。全部消除时显示空态。
  const raw = latestActiveAlarm();
  if (!raw) return null;
  const ts = num(raw.timestamp) ?? 0;
  const channel = raw.channel_id === null || raw.channel_id === undefined ? "" : ` / 通道 ${raw.channel_id}`;
  return {
    level: String(raw.level ?? "info"),
    device: `${String(raw.device_id ?? "未知设备")}${channel}`,
    type: String(raw.alarm_type ?? ""),
    amplitude: num(raw.amplitude),
    threshold: num(raw.threshold),
    time: ts > 0 ? new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : "—",
  };
});

const transport = computed(() => stats.value?.transport ?? null);

/**
 * 内核进程（sidecar）自身的资源占用。
 *
 * 旧 `PerformanceMonitorWidget` 显示的就是这一类量，ui/ 归档后没有接回前端——
 * 仪表板的吞吐表测的是"帧发出去多少"，与"进程吃了多少 CPU/内存"是两回事，
 * 前者不能替代后者（长稳排障要看的正是后者）。
 */
const proc = computed(() => stats.value?.process ?? null);
const procMachinePercent = computed(() => {
  const p = proc.value;
  if (!p || !p.cpu_count) return null;
  return p.cpu_percent / p.cpu_count;
});

const uptime = computed(() => {
  const s = transport.value?.uptime_s;
  return s === undefined ? "—" : `${Math.floor(s / 60)} 分 ${Math.round(s % 60)} 秒`;
});

/** 逐 topic 收帧计数：这是验证"边界是否真的在流动"最直接的观测量 */
const received = computed(() =>
  (Object.keys(TOPICS) as Topic[]).map((topic) => ({
    topic,
    count: frameCount[topic] ?? 0,
    sent: transport.value?.sent_by_topic[topic] ?? 0,
    rate: transport.value?.topic_rates[topic],
  })),
);
</script>

<template>
  <div class="flex flex-col gap-4">
    <section class="grid grid-cols-5 gap-3">
      <div
        v-for="card in CARDS"
        :key="card.label"
        class="rounded-lg border border-app-border bg-app-surface p-3"
        :title="card.hint"
      >
        <div class="text-[11px] text-app-muted">{{ card.label }}</div>
        <div class="tabular mt-1 text-2xl font-semibold" :class="card.tone">
          {{ card.value }}<span class="ml-1 text-xs font-normal text-app-muted">{{ card.suffix }}</span>
        </div>
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="flex items-center gap-2 border-b border-app-border px-3 py-1.5">
        <span class="text-[13px] font-medium">最新告警</span>
        <span class="text-[11px] text-app-muted">共收到 {{ alarms.length }} 条（上限 500）</span>
      </div>
      <div v-if="latestAlarm" class="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2">
        <span class="rounded px-1.5 py-0.5 text-[11px]" :class="LEVEL_TONE[latestAlarm.level] ?? ''">
          {{ latestAlarm.level }}
        </span>
        <span class="text-[13px] font-medium">{{ latestAlarm.device }}</span>
        <span class="text-[12px] text-app-muted">{{ latestAlarm.type }}</span>
        <span v-if="latestAlarm.amplitude !== null" class="tabular text-[12px]">
          {{ latestAlarm.amplitude.toFixed(1) }}
          <span v-if="latestAlarm.threshold !== null" class="text-app-muted">
            / 阈值 {{ latestAlarm.threshold.toFixed(1) }}
          </span>
        </span>
        <span class="tabular ml-auto text-[11px] text-app-muted">{{ latestAlarm.time }}</span>
      </div>
      <div v-else class="px-3 py-2 text-[12px] text-app-muted">暂无报警信息</div>
    </section>

    <section class="grid grid-cols-5 gap-3">
      <SnapshotMini class="col-span-3" topic="waveform" title="波形快照" :height="150" />
      <div class="col-span-2 flex flex-col gap-3">
        <SnapshotMini topic="prpd" title="PRPD 快照" :height="150" />
        <SnapshotMini topic="fft" title="FFT 快照" :height="150" />
      </div>
    </section>

    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
        <span class="text-[13px] font-medium">跨进程边界吞吐</span>
        <span class="text-[11px] text-app-muted"> 连接 {{ streamState }} · 内核运行 {{ uptime }} </span>
      </div>
      <table class="w-full text-[12px]">
        <thead class="text-app-muted">
          <tr class="border-b border-app-border">
            <th class="px-3 py-1.5 text-left font-normal">topic</th>
            <th class="px-3 py-1.5 text-right font-normal">已收帧</th>
            <th class="px-3 py-1.5 text-right font-normal">服务端发出</th>
            <th class="px-3 py-1.5 text-right font-normal">限流</th>
            <th class="px-3 py-1.5 text-right font-normal">投递等级</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in received" :key="row.topic" class="border-b border-app-border/50">
            <td class="px-3 py-1.5 font-mono">{{ row.topic }}</td>
            <td class="tabular px-3 py-1.5 text-right">{{ row.count }}</td>
            <td class="tabular px-3 py-1.5 text-right">{{ row.sent }}</td>
            <td class="tabular px-3 py-1.5 text-right text-app-muted">
              {{ row.rate === undefined ? "跟随管线" : `${row.rate} Hz` }}
            </td>
            <td class="px-3 py-1.5 text-right text-app-muted">{{ TOPICS[row.topic] }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="transport" class="border-t border-app-border px-3 py-2 text-[11px] text-app-muted">
        服务端发出 {{ transport.sent_frames }} 帧 · 合并顶掉 {{ transport.coalesced_frames }} · 限流掐掉
        {{ transport.throttled_frames }} · 因滞后丢弃 {{ transport.dropped_frames }} · 合并窗口
        {{ transport.flush_interval_ms }}ms · 当前客户端 {{ transport.clients }}
      </div>
      <div v-if="transport" class="px-3 pb-2 text-[11px] text-app-muted">
        服务端各列随 stats 帧每 2 秒更新，所以"已收帧"短暂高于"服务端发出"是正常的（计数时刻不同），
        而 log 的接入补历史不走发送计数。真正要看的是三个"没发出去"的计数是否持续增长。
      </div>
      <div v-else class="border-t border-app-border px-3 py-2 text-[11px] text-app-muted">
        尚未收到 stats 帧
      </div>
    </section>
    <section class="rounded-lg border border-app-border bg-app-surface">
      <div class="flex items-center justify-between border-b border-app-border px-3 py-2">
        <span class="text-[13px] font-medium">内核进程</span>
        <span class="text-[11px] text-app-muted">
          {{ proc ? `pid ${proc.pid} · 采样间隔 ${proc.interval_s}s` : "尚未收到 stats 帧" }}
        </span>
      </div>
      <div v-if="proc" class="grid grid-cols-3 gap-3 px-3 py-3">
        <div>
          <div class="text-[11px] text-app-muted">CPU 占用</div>
          <div class="tabular mt-1 text-xl font-semibold text-app-primary">
            {{ proc.cpu_percent.toFixed(1)
            }}<span class="ml-1 text-xs font-normal text-app-muted">% 单核基准</span>
          </div>
        </div>
        <div>
          <div class="text-[11px] text-app-muted">常驻内存 RSS</div>
          <div class="tabular mt-1 text-xl font-semibold text-app-info">
            {{ proc.rss_mb === null ? "—" : proc.rss_mb.toFixed(1)
            }}<span class="ml-1 text-xs font-normal text-app-muted">MB</span>
          </div>
        </div>
        <div>
          <div class="text-[11px] text-app-muted">累计 CPU 时间</div>
          <div class="tabular mt-1 text-xl font-semibold text-app-muted">
            {{ proc.cpu_seconds.toFixed(0) }}<span class="ml-1 text-xs font-normal text-app-muted">秒</span>
          </div>
        </div>
      </div>
      <div v-if="proc" class="border-t border-app-border px-3 py-2 text-[11px] text-app-muted">
        只统计内核进程（sidecar）自身，不含 Electron 渲染进程。CPU 以单核为 100%，本机
        {{ proc.cpu_count }} 核，折合整机约 {{ procMachinePercent === null ? "—" : procMachinePercent.toFixed(1) }}%。
        长稳口径：单通道稳态 &lt; 300 MB、8 通道 &lt; 600 MB；RSS 取不到时显示 —，不显示 0。
      </div>
      <div v-else class="px-3 py-2 text-[12px] text-app-muted">尚未收到 stats 帧</div>
    </section>
  </div>
</template>
