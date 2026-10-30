import type { ScheduleEvent } from './types'

export type Urgency = 'urgent' | 'warn' | 'normal' | 'done'

// raw_payload.kind values whose start time is what matters (contests, exams).
// Everything else (homework, trainings, manual) is a deadline: end_at.
const TIMED_KINDS = new Set(['contest', 'exam'])

const FINISHED_STATUSES = ['submitted', 'done', 'closed']

// Backend returns offsets like "+00:00"; appending "Z" to those yields
// invalid ISO strings, so only append when no zone is present.
export function parseMs(iso: string | null): number {
  if (!iso) return NaN
  return Date.parse(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z')
}

export function isTimed(e: ScheduleEvent): boolean {
  return !!e.start_at && TIMED_KINDS.has(e.raw_payload?.kind)
}

/** Timed event currently in its [start, end) window. */
export function isRunning(e: ScheduleEvent, nowMs: number): boolean {
  if (!isTimed(e)) return false
  const end = parseMs(e.end_at)
  return parseMs(e.start_at) <= nowMs && (isNaN(end) || nowMs < end)
}

/** The instant the user cares about: start for timed events, end (deadline) otherwise. */
export function keyMs(e: ScheduleEvent): number {
  return parseMs(isTimed(e) ? e.start_at : e.end_at)
}

export function urgencyOf(e: ScheduleEvent, nowMs: number): Urgency {
  if (FINISHED_STATUSES.includes(e.status)) return 'done'
  if (isRunning(e, nowMs)) return 'urgent'
  // A timed event past its start but not running has ended
  if (isTimed(e) && parseMs(e.start_at) <= nowMs) return 'done'
  const key = keyMs(e)
  if (isNaN(key)) return 'normal'
  const ms = key - nowMs
  if (ms < 0) return 'done'
  if (ms <= 48 * 3600 * 1000) return 'urgent'
  if (ms <= 7 * 24 * 3600 * 1000) return 'warn'
  return 'normal'
}
