<script setup lang="ts">
import type { ScheduleEvent } from '../types'
import { useEventsStore } from '../stores/events'

const props = defineProps<{
  event: ScheduleEvent
  urgency: 'urgent' | 'warn' | 'normal' | 'done'
}>()

const store = useEventsStore()

const POLICY_LABEL: Record<string, string> = {
  none: '不可补',
  once: '仅一次',
  late: '可补交',
  unlimited: '无限次',
}

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const ms = Date.parse(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z')
  return new Date(ms).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

async function markDone() {
  try { await store.markDone(props.event.id) }
  catch (e: any) { console.error(e.message) }
}

async function del() {
  if (store.confirmDelete && !confirm(`移入回收站？\n${props.event.title}`)) return
  try { await store.deleteEvent(props.event.id) }
  catch (e: any) { console.error(e.message) }
}
</script>

<template>
  <div class="event-row" :class="`row-${urgency}`">
    <span class="col-course">{{ event.course || '—' }}</span>
    <span class="col-title">{{ event.title }}</span>
    <span class="col-deadline">{{ fmt(event.end_at) }}</span>
    <span class="col-policy">
      <span v-if="event.submission_policy && event.submission_policy !== 'none'"
        class="badge" :class="`policy-${event.submission_policy}`">
        {{ POLICY_LABEL[event.submission_policy] ?? event.submission_policy }}
      </span>
    </span>
    <span class="col-actions">
      <button
        v-if="urgency !== 'done'"
        class="row-btn done-btn"
        title="标记完成"
        @click="markDone"
      >完成</button>
      <button class="row-btn del-btn" title="移入回收站" @click="del">删除</button>
    </span>
  </div>
</template>

<style scoped>
.event-row {
  display: grid;
  grid-template-columns: 96px 1fr 120px 76px 96px;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  transition: background .1s;
  cursor: default;
  position: relative;
}
.event-row:last-child { border-bottom: none; }
.event-row:hover { background: rgba(0,103,192,.04); }

/* urgency left strip */
.row-urgent { border-left: 3px solid var(--urgent); padding-left: 13px; }
.row-warn   { border-left: 3px solid #F59E0B;      padding-left: 13px; }

.col-course { font-family: var(--mono); font-size: 10px; color: var(--muted); }
.col-title    { font-size: 13px; }
.col-deadline { font-family: var(--mono); font-size: 11px; color: var(--muted); text-align: right; }
.col-policy   { text-align: right; }
.col-actions  { display: flex; gap: 6px; justify-content: flex-end; }

.row-urgent .col-deadline { color: var(--urgent); font-weight: 600; }
.row-urgent .col-title    { font-weight: 500; }
.row-warn   .col-deadline { color: var(--warn); }
.row-done   .col-title    { color: var(--done); text-decoration: line-through; }
.row-done   .col-deadline { color: var(--done); }

.badge {
  display: inline-block; padding: 2px 7px; border-radius: 12px;
  font-size: 10px; font-family: var(--mono);
  border: 1px solid var(--border-strong); color: var(--muted);
}
.policy-once { background: #FDE7E9; border-color: #F1BCBF; color: var(--urgent); }
.policy-late { background: #FFF4CE; border-color: #F0D06A; color: var(--warn); }

.row-btn {
  padding: 3px 10px; border-radius: 4px; font-size: 11px;
  border: 1px solid var(--border-strong); background: transparent;
  color: var(--muted); cursor: pointer; transition: background .1s, color .1s;
}
.done-btn:hover { background: #EAF4E9; color: #107C10; border-color: #107C10; }
.del-btn:hover  { background: #FDE7E9; color: var(--urgent); border-color: var(--urgent); }
</style>
