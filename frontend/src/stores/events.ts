import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { ScheduleEvent, StagedEvent } from '../types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export const useEventsStore = defineStore('events', () => {
  const events = ref<ScheduleEvent[]>([])
  const staged = ref<StagedEvent[]>([])
  const trash = ref<ScheduleEvent[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const activePlatform = ref<string>('all')
  const hideDone = ref(false)
  // 移入回收站前是否弹确认框；偏好持久化到 localStorage
  const confirmDelete = ref(localStorage.getItem('confirmDelete') !== 'false')
  watch(confirmDelete, v => localStorage.setItem('confirmDelete', String(v)))
  const platforms = computed(() => {
    const ps = new Set(events.value.map(e => e.platform))
    return ['all', ...Array.from(ps).sort()]
  })

  const filteredEvents = computed(() => {
    let base = activePlatform.value === 'all'
      ? events.value
      : events.value.filter(e => e.platform === activePlatform.value)
    if (hideDone.value) base = base.filter(e => urgencyOf(e) !== 'done')
    return base
  })

  const now = ref(new Date())

  // Backend returns offsets like "+00:00"; appending "Z" to those yields
  // invalid ISO strings, so only append when no zone is present.
  function parseMs(iso: string | null): number {
    if (!iso) return NaN
    return Date.parse(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z')
  }

  function urgencyOf(e: ScheduleEvent): 'urgent' | 'warn' | 'normal' | 'done' {
    if (['submitted', 'done', 'closed'].includes(e.status)) return 'done'
    if (!e.end_at) return 'normal'
    const ms = parseMs(e.end_at) - now.value.getTime()
    if (ms < 0) return 'done'
    if (ms <= 48 * 3600 * 1000) return 'urgent'
    if (ms <= 7 * 24 * 3600 * 1000) return 'warn'
    return 'normal'
  }

  const urgentEvents = computed(() =>
    filteredEvents.value.filter(e => urgencyOf(e) === 'urgent')
  )
  const warnEvents = computed(() =>
    filteredEvents.value.filter(e => urgencyOf(e) === 'warn')
  )
  const normalEvents = computed(() =>
    filteredEvents.value.filter(e => urgencyOf(e) === 'normal')
  )
  const doneEvents = computed(() =>
    filteredEvents.value.filter(e => urgencyOf(e) === 'done')
  )

  async function fetchEvents() {
    loading.value = true
    error.value = null
    try {
      const [evRes, stRes] = await Promise.all([
        fetch(`${BASE}/events?limit=200`),
        fetch(`${BASE}/staged`),
      ])
      if (!evRes.ok) throw new Error(`/events ${evRes.status}`)
      if (!stRes.ok) throw new Error(`/staged ${stRes.status}`)
      events.value = await evRes.json()
      staged.value = await stRes.json()
    } catch (e: any) {
      error.value = e.message ?? 'Fetch failed'
    } finally {
      loading.value = false
    }
    // Trash is best-effort — a failure here doesn't break the main view.
    fetchTrash().catch(() => {})
  }

  async function acceptStaged(id: number) {
    const res = await fetch(`${BASE}/staged/${id}/accept`, { method: 'POST' })
    if (!res.ok) throw new Error(`Accept failed: ${res.status}`)
    const newEvent: ScheduleEvent = await res.json()
    events.value.push(newEvent)
    staged.value = staged.value.filter(s => s.id !== id)
  }

  async function deleteEvent(id: number) {
    const res = await fetch(`${BASE}/events/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
    events.value = events.value.filter(e => e.id !== id)
  }

  async function markDone(id: number) {
    const res = await fetch(`${BASE}/events/${id}/done`, { method: 'POST' })
    if (!res.ok) throw new Error(`Mark done failed: ${res.status}`)
    const updated: ScheduleEvent = await res.json()
    const idx = events.value.findIndex(e => e.id === id)
    if (idx !== -1) events.value[idx] = updated
  }

  async function restoreEvent(id: number) {
    const res = await fetch(`${BASE}/events/${id}/restore`, { method: 'POST' })
    if (!res.ok) throw new Error(`Restore failed: ${res.status}`)
    const restored: ScheduleEvent = await res.json()
    trash.value = trash.value.filter(e => e.id !== id)
    events.value.push(restored)
  }

  async function permanentDelete(id: number) {
    const res = await fetch(`${BASE}/events/${id}/permanent`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`Permanent delete failed: ${res.status}`)
    trash.value = trash.value.filter(e => e.id !== id)
  }

  async function fetchTrash() {
    const res = await fetch(`${BASE}/trash`)
    if (!res.ok) throw new Error(`Fetch trash failed: ${res.status}`)
    trash.value = await res.json()
  }

  async function dismissStaged(id: number) {
    const res = await fetch(`${BASE}/staged/${id}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`Dismiss failed: ${res.status}`)
    staged.value = staged.value.filter(s => s.id !== id)
  }

  // tick every minute so urgency classes stay fresh
  let tickTimer: ReturnType<typeof setInterval> | null = null
  function startTick() {
    if (tickTimer) return
    tickTimer = setInterval(() => { now.value = new Date() }, 60_000)
  }
  function stopTick() {
    if (tickTimer) { clearInterval(tickTimer); tickTimer = null }
  }

  // poll backend every 5 min
  let pollTimer: ReturnType<typeof setInterval> | null = null
  function startPoll() {
    if (pollTimer) return
    fetchEvents()
    pollTimer = setInterval(fetchEvents, 5 * 60 * 1000)
  }
  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }

  return {
    events, staged, trash, loading, error,
    activePlatform, platforms, hideDone, confirmDelete,
    filteredEvents, urgentEvents, warnEvents, normalEvents, doneEvents,
    urgencyOf, now,
    fetchEvents, acceptStaged, dismissStaged,
    deleteEvent, markDone, restoreEvent, permanentDelete, fetchTrash,
    startTick, stopTick, startPoll, stopPoll,
  }
})
