<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useEventsStore } from '../stores/events'
import type { ScheduleEvent } from '../types'
import { PLATFORM_COLORS, displayName, platformColor } from '../platforms'
import { isRunning, isTimed, keyMs, parseMs, urgencyOf, type Urgency } from '../eventTime'

const store = useEventsStore()

// ── Fixed-pixel layout constants ─────────────────────────────────────────────
const PX_PER_DAY  = 120   // pixels per day — change this to zoom
const LABEL_W     = 120   // px for platform label column
const PAST_H      = 24    // hours of history shown left of "now"
const FUTURE_D    = 30    // days of future shown right of "now"
const TOTAL_MS    = (PAST_H + FUTURE_D * 24) * 3_600_000
const TOTAL_PX    = computed(() => (PAST_H / 24 + FUTURE_D) * PX_PER_DAY)

const now = ref(Date.now())
let timer: ReturnType<typeof setInterval>
onMounted(() => { if (!store.events.length) store.fetchEvents(); timer = setInterval(() => { now.value = Date.now() }, 30_000) })
onUnmounted(() => clearInterval(timer))

const windowStart = computed(() => now.value - PAST_H * 3_600_000)
const windowEnd   = computed(() => now.value + FUTURE_D * 24 * 3_600_000)

// ── Pixel position helpers ────────────────────────────────────────────────────
function toPx(ms: number): number {
  return ((ms - windowStart.value) / TOTAL_MS) * TOTAL_PX.value
}
const nowPx = computed(() => toPx(now.value))

// Node sits at the key time: start for contests/exams, deadline otherwise
function eventPx(e: ScheduleEvent): number {
  const ms = keyMs(e)
  return isNaN(ms) ? 0 : toPx(ms)
}

// ── Urgency ───────────────────────────────────────────────────────────────────
function urgency(e: ScheduleEvent): Urgency {
  return urgencyOf(e, now.value)
}

// ── Countdown ────────────────────────────────────────────────────────────────
function countdown(e: ScheduleEvent): string {
  if (isRunning(e, now.value)) return '进行中'
  const key = keyMs(e)
  if (isNaN(key)) return '—'
  const diff = key - now.value
  if (diff <= 0) {
    const o = -diff
    if (o < 3_600_000) return `已过 ${Math.floor(o / 60_000)} 分`
    if (o < 86_400_000) return `已过 ${Math.floor(o / 3_600_000)} 小时`
    return `已过 ${Math.floor(o / 86_400_000)} 天`
  }
  if (diff < 3_600_000) return `还剩 ${Math.floor(diff / 60_000)} 分钟`
  if (diff < 86_400_000) return `还剩 ${Math.floor(diff / 3_600_000)} 小时`
  const d2 = Math.floor(diff / 86_400_000)
  const h  = Math.floor((diff % 86_400_000) / 3_600_000)
  return h > 0 ? `还剩 ${d2} 天 ${h} 小时` : `还剩 ${d2} 天`
}

// ── Axis ticks (pixel-based) ──────────────────────────────────────────────────
// Use LOCAL midnight, not UTC midnight — tick labels are rendered in local time
// (toLocaleDateString) so the anchor must also be local midnight to keep them aligned.
interface Tick { label: string; px: number; major: boolean }
const ticks = computed<Tick[]>(() => {
  const result: Tick[] = []
  const d = new Date(windowStart.value)
  d.setHours(0, 0, 0, 0)           // local midnight, not UTC
  d.setDate(d.getDate() + 1)
  while (d.getTime() <= windowEnd.value) {
    result.push({
      label: d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric', weekday: 'short' }),
      px: toPx(d.getTime()),
      major: true,
    })
    const noon = new Date(d); noon.setHours(12)
    if (noon.getTime() <= windowEnd.value)
      result.push({ label: '12:00', px: toPx(noon.getTime()), major: false })
    d.setDate(d.getDate() + 1)
  }
  return result
})

// ── Grouped lanes ─────────────────────────────────────────────────────────────
const visibleEvents = computed(() =>
  store.events.filter(e => {
    const ms = keyMs(e)
    return !isNaN(ms) && ms >= windowStart.value && ms <= windowEnd.value
  })
)

interface Lane { platform: string; events: ScheduleEvent[] }
const lanes = computed<Lane[]>(() => {
  const map = new Map<string, ScheduleEvent[]>()
  for (const e of visibleEvents.value) {
    if (!map.has(e.platform)) map.set(e.platform, [])
    map.get(e.platform)!.push(e)
  }
  for (const arr of map.values())
    arr.sort((a, b) => keyMs(a) - keyMs(b))
  return Array.from(map.entries()).map(([platform, events]) => ({ platform, events }))
})

// Assign sub-rows within a lane to avoid overlap (conflict = nodes closer than 130px)
function rowAssignments(events: ScheduleEvent[]): number[] {
  const rows: number[] = []
  const pxs = events.map(e => eventPx(e))
  for (let i = 0; i < events.length; i++) {
    let assigned = 0
    for (let row = 0; row <= 3; row++) {
      const conflict = rows.slice(0, i).some((r, j) => r === row && Math.abs(pxs[j] - pxs[i]) < 130)
      if (!conflict) { assigned = row; break }
    }
    rows.push(assigned)
  }
  return rows
}

// Lane height based on max row used
function laneHeight(events: ScheduleEvent[]): number {
  const rows = rowAssignments(events)
  return 52 + Math.max(...rows) * 48
}

// ── Detail panel ──────────────────────────────────────────────────────────────
const selected = ref<ScheduleEvent | null>(null)
function select(e: ScheduleEvent) { selected.value = selected.value?.id === e.id ? null : e }
function fmtFull(iso: string | null): string {
  if (!iso) return '—'
  const ms = parseMs(iso)
  return new Date(ms).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}
</script>

<template>
  <div class="timeline-view">
    <div class="tl-header">
      <h2 class="tl-title">时间轴</h2>
      <span class="tl-range">过去 {{ PAST_H }} 小时 · 未来 {{ FUTURE_D }} 天</span>
    </div>

    <!-- legend -->
    <div class="legend">
      <span v-for="(color, key) in PLATFORM_COLORS" :key="key" class="legend-item">
        <span class="legend-dot" :style="{ background: color }"></span>{{ displayName(key) }}
      </span>
      <span class="legend-item"><span class="legend-badge urgent-dot"></span>48h 内截止</span>
      <span class="legend-item"><span class="legend-badge warn-dot"></span>7天内截止</span>
    </div>

    <!-- scrollable timeline -->
    <div class="tl-scroll-wrap">
      <div class="tl-wrap" :style="{ width: (LABEL_W + TOTAL_PX) + 'px' }">

        <!-- axis row -->
        <div class="axis-row">
          <div class="label-cell"></div>
          <div class="track-cell" :style="{ width: TOTAL_PX + 'px' }">
            <div class="axis-track">
              <div v-for="(t, i) in ticks" :key="i"
                class="tick" :class="{ major: t.major }"
                :style="{ left: t.px + 'px' }">
                <span v-if="t.major" class="tick-label">{{ t.label }}</span>
              </div>
              <div class="now-marker" :style="{ left: nowPx + 'px' }">
                <span class="now-label">现在</span>
              </div>
            </div>
          </div>
        </div>

        <!-- swimlanes -->
        <div class="lanes">
          <!-- now-line overlay -->
          <div class="now-overlay" :style="{ left: (LABEL_W + nowPx) + 'px' }"></div>

          <div v-for="lane in lanes" :key="lane.platform" class="lane-row"
            :style="{ height: laneHeight(lane.events) + 'px' }">
            <div class="label-cell" :style="{ color: platformColor(lane.platform) }">
              {{ displayName(lane.platform) }}
            </div>
            <div class="track-cell" :style="{ width: TOTAL_PX + 'px', position: 'relative' }">
              <!-- bar fills: horizontal line from now to deadline, vertically centred on the node -->
              <template v-for="(e, idx) in lane.events" :key="'bar-' + e.id">
                <div class="bar-fill"
                  :style="{
                    background: platformColor(e.platform),
                    left: Math.max(0, nowPx) + 'px',
                    width: Math.max(0, eventPx(e) - Math.max(0, nowPx)) + 'px',
                    top: (38 + rowAssignments(lane.events)[idx] * 48) + 'px',
                  }">
                </div>
              </template>
              <!-- event nodes -->
              <template v-for="(e, idx) in lane.events" :key="'node-' + e.id">
                <button class="event-node"
                  :style="{
                    left: eventPx(e) + 'px',
                    top: (24 + rowAssignments(lane.events)[idx] * 48) + 'px',
                    borderColor: platformColor(e.platform),
                    opacity: urgency(e) === 'done' ? 0.45 : 1,
                  }"
                  @click="select(e)">
                  <!-- urgency dot badge -->
                  <span v-if="urgency(e) === 'urgent'" class="badge-dot urgent-badge"></span>
                  <span v-else-if="urgency(e) === 'warn'" class="badge-dot warn-badge"></span>
                  <span class="node-title">{{ e.title }}</span>
                  <span class="node-cd">{{ countdown(e) }}</span>
                </button>
              </template>
            </div>
          </div>

          <div v-if="!lanes.length && !store.loading" class="empty">窗口内无事项</div>
        </div>
      </div>
    </div>

    <!-- detail panel -->
    <transition name="slide">
      <div v-if="selected" class="detail-panel">
        <div class="detail-header">
          <span class="detail-platform" :style="{ color: platformColor(selected.platform) }">
            {{ displayName(selected.platform) }}
          </span>
          <button class="detail-close" @click="selected = null">✕</button>
        </div>
        <div class="detail-title">{{ selected.title }}</div>
        <div v-if="isTimed(selected)" class="detail-row"><span class="detail-k">开始</span><span class="detail-v">{{ fmtFull(selected.start_at) }} (CST)</span></div>
        <div class="detail-row"><span class="detail-k">{{ isTimed(selected) ? '结束' : '截止' }}</span><span class="detail-v">{{ fmtFull(selected.end_at) }} (CST)</span></div>
        <div class="detail-row"><span class="detail-k">倒计时</span><span class="detail-v" :class="`cd-${urgency(selected)}`">{{ countdown(selected) }}</span></div>
        <div class="detail-row"><span class="detail-k">状态</span><span class="detail-v">{{ selected.status }}</span></div>
        <div class="detail-row"><span class="detail-k">提交规则</span><span class="detail-v">{{ selected.submission_policy }}</span></div>
        <div v-if="selected.raw_payload?.proposed_by || selected.raw_payload?.publisher" class="detail-row">
          <span class="detail-k">来源</span>
          <span class="detail-v">{{ selected.raw_payload?.proposed_by ?? selected.raw_payload?.publisher }}</span>
        </div>
        <div v-if="selected.description" class="detail-desc">{{ selected.description.slice(0, 300) }}{{ selected.description.length > 300 ? '…' : '' }}</div>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.timeline-view { max-width: 100%; margin: 0 auto; padding: 24px 20px 60px; }

.tl-header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; }
.tl-title  { font-size: 18px; font-weight: 600; letter-spacing: -.3px; }
.tl-range  { font-size: 12px; color: var(--muted); }

.legend { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.legend-item { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--muted); font-family: var(--mono); }
.legend-dot  { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.legend-badge { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.urgent-dot { background: #C50F1F; }
.warn-dot   { background: #F59E0B; }

/* ── Scroll container ── */
.tl-scroll-wrap {
  overflow-x: auto;
  overflow-y: visible;
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
  background: var(--surface);
  backdrop-filter: blur(20px) saturate(1.3);
  -webkit-backdrop-filter: blur(20px) saturate(1.3);
}

/* Inner fixed-width container */
.tl-wrap { display: flex; flex-direction: column; min-width: 100%; }

/* ── Two-column row layout ── */
.axis-row,
.lane-row {
  display: flex;
  align-items: stretch;
}

.label-cell {
  width: 120px;
  flex-shrink: 0;
  padding: 0 8px 0 14px;
  display: flex;
  align-items: center;
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 500;
  color: var(--muted);
}

.track-cell {
  position: relative;
  flex-shrink: 0;
}

/* ── Axis ── */
.axis-track {
  position: relative;
  height: 36px;
  border-bottom: 1px solid var(--border-strong);
}

.tick {
  position: absolute;
  top: 0; bottom: 0;
  width: 1px;
  background: rgba(0,0,0,.08);
}
.tick.major {
  width: 2px;
  background: rgba(0,0,0,.18);
}
.tick-label {
  position: absolute;
  top: 8px; left: 5px;
  font-size: 11px;
  font-family: var(--mono);
  color: var(--text);
  white-space: nowrap;
  font-weight: 500;
}
.now-marker {
  position: absolute;
  top: 0; bottom: -4px;
  width: 2px; background: var(--accent);
  z-index: 10;
  animation: nowPulse 2s ease-in-out infinite;
}
.now-label {
  position: absolute;
  top: 7px; left: 5px;
  font-size: 11px; font-weight: 600;
  color: var(--accent);
  white-space: nowrap; font-family: var(--mono);
}
@keyframes nowPulse { 0%,100%{opacity:1} 50%{opacity:.45} }

/* ── Lanes ── */
.lanes { position: relative; }
.now-overlay {
  position: absolute;
  top: 0; bottom: 0;
  width: 1px;
  background: rgba(0,103,192,.25);
  pointer-events: none;
  z-index: 5;
}

.lane-row { border-bottom: 1px solid var(--border); }
.lane-row:last-child { border-bottom: none; }

/* bar */
.bar-fill {
  position: absolute;
  height: 3px;
  opacity: .3;
  border-radius: 2px;
  pointer-events: none;
  transform: translateY(0);
}

/* node */
.event-node {
  position: absolute;
  transform: translateX(-50%);
  display: flex; flex-direction: column; align-items: center;
  background: var(--surface-solid);
  border: 2px solid #999;
  border-radius: 6px;
  padding: 3px 8px 3px 8px;
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  z-index: 10;
  min-width: 90px; max-width: 180px;
  white-space: nowrap;
  transition: box-shadow .12s, transform .12s;
}
.event-node:hover { box-shadow: var(--shadow-md); transform: translateX(-50%) translateY(-3px); }

/* urgency badge dot */
.badge-dot {
  position: absolute;
  top: -4px; right: -4px;
  width: 8px; height: 8px;
  border-radius: 50%;
  border: 1.5px solid #fff;
}
.urgent-badge { background: #C50F1F; }
.warn-badge   { background: #F59E0B; }

.node-title { font-size: 10px; font-weight: 500; color: var(--text); overflow: hidden; text-overflow: ellipsis; max-width: 164px; }
.node-cd    { font-size: 9px; font-family: var(--mono); color: var(--muted); margin-top: 1px; }

.empty { padding: 24px; text-align: center; color: var(--muted); font-size: 13px; }

/* ── Detail panel ── */
.detail-panel {
  margin-top: 16px; background: var(--surface-solid);
  border: 1px solid var(--border); border-radius: 8px;
  box-shadow: var(--shadow-md); padding: 16px 20px;
  max-width: 560px;
}
.detail-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.detail-platform { font-family: var(--mono); font-size: 11px; font-weight: 600; }
.detail-close { border: none; background: none; color: var(--muted); font-size: 14px; cursor: pointer; }
.detail-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; line-height: 1.35; }
.detail-row { display: flex; gap: 12px; font-size: 12px; margin-bottom: 6px; }
.detail-k { width: 60px; color: var(--muted); flex-shrink: 0; }
.detail-v { font-family: var(--mono); }
.cd-urgent { color: #C50F1F; font-weight: 600; }
.cd-warn   { color: #835B00; }
.detail-desc { margin-top: 12px; font-size: 12px; color: var(--muted); line-height: 1.6; border-top: 1px solid var(--border); padding-top: 10px; }

.slide-enter-active, .slide-leave-active { transition: opacity .2s, transform .2s; }
.slide-enter-from, .slide-leave-to       { opacity: 0; transform: translateY(-8px); }
@media (prefers-reduced-motion: reduce) { .now-marker { animation: none; } }
</style>
