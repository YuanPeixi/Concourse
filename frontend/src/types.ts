export interface ScheduleEvent {
  id: number
  platform: string
  source_id: string
  course: string
  title: string
  description: string
  location: string
  meeting_url: string | null
  start_at: string | null
  end_at: string | null
  tz_original: string | null
  all_day: boolean
  status: string
  submission_policy: string
  organizer_name: string | null
  organizer_email: string | null
  attendees: any[] | null
  recurrence_rrule: string | null
  content_hash: string
  raw_payload: Record<string, any> | null
  first_seen_at: string
  last_seen_at: string
  updated_at: string
  deleted_at: string | null
}

export interface StagedEvent {
  id: number
  platform: string
  source_id: string
  course: string
  title: string
  description: string
  start_at: string | null
  end_at: string | null
  tz_original: string | null
  all_day: boolean
  status: string
  submission_policy: string
  proposed_by: string
  created_at: string
  raw_payload: Record<string, any> | null
}

export interface AddEventPayload {
  message: string
}

export interface FetcherInfo {
  name: string
  enabled: boolean
  cron: string
  cron_default: string
  cron_overridden: boolean
  running: boolean
  next_run_at: string | null
  description: { name: string; title: string; color: string; login: boolean; cron: string;
    fields: { name: string; label: string; type: string; secret?: boolean; help?: string }[] }
  configured: boolean
  state: string
  error_kind: string | null
  credentials_saved: boolean
  config: Record<string, any>
  last_trigger: 'schedule' | 'manual' | 'cli' | null
  last_started_at: string | null
  last_finished_at: string | null
  last_success_at: string | null
  last_status: 'ok' | 'error' | null
  last_error: string
  last_count: number
  last_changed: number
  last_duration_ms: number
}
