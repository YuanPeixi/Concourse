<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useFetchersStore } from '../stores/fetchers'
import { useEventsStore } from '../stores/events'
import { apiFetch } from '../api'
import ReminderSettings from '../components/ReminderSettings.vue'
import PlatformConfig from '../components/PlatformConfig.vue'
import type { FetcherInfo } from '../types'

const store = useFetchersStore()
const events = useEventsStore()

const PRESETS: { cron: string; label: string }[] = [
  { cron: '*/10 * * * *', label: '每 10 分钟' },
  { cron: '*/30 * * * *', label: '每 30 分钟' },
  { cron: '0 * * * *',    label: '每小时' },
  { cron: '0 */2 * * *',  label: '每 2 小时' },
  { cron: '0 */6 * * *',  label: '每 6 小时' },
  { cron: '0 8 * * *',    label: '每天 08:00' },
]
const CUSTOM = '__custom__'

// ── Per-fetcher edit state ───────────────────────────────────────────────────
interface Draft { base: string; preset: string; custom: string; saving: boolean; msg: string; msgErr: boolean }
const drafts = reactive<Record<string, Draft>>({})

// Re-sync from the server unless the user has an unsaved edit in progress.
function syncDraft(f: FetcherInfo, force = false) {
  const d = drafts[f.name]
  if (d && !force && (d.base === f.cron || draftCron(f.name) !== d.base)) return
  const isPreset = PRESETS.some(p => p.cron === f.cron)
  drafts[f.name] = {
    base: f.cron,
    preset: isPreset ? f.cron : CUSTOM,
    custom: isPreset ? '' : f.cron,
    saving: false, msg: d?.msg ?? '', msgErr: d?.msgErr ?? false,
  }
}

function draftCron(name: string): string {
  const d = drafts[name]
  return d.preset === CUSTOM ? d.custom.trim() : d.preset
}

function isDirty(f: FetcherInfo): boolean {
  return !!drafts[f.name] && draftCron(f.name) !== f.cron
}

function flash(name: string, msg: string, err = false) {
  drafts[name].msg = msg
  drafts[name].msgErr = err
  if (!err) setTimeout(() => { if (drafts[name]?.msg === msg) drafts[name].msg = '' }, 2500)
}

async function saveCron(f: FetcherInfo) {
  const cron = draftCron(f.name)
  if (!cron) { flash(f.name, '请输入 cron 表达式', true); return }
  drafts[f.name].saving = true
  try {
    await store.update(f.name, { cron: cron === f.cron_default ? null : cron })
    syncDraft(store.fetchers.find(x => x.name === f.name)!, true)
    flash(f.name, '已保存')
  } catch (e: any) {
    flash(f.name, e.message, true)
  } finally {
    drafts[f.name].saving = false
  }
}

async function resetCron(f: FetcherInfo) {
  try {
    await store.update(f.name, { cron: null })
    syncDraft(store.fetchers.find(x => x.name === f.name)!, true)
    flash(f.name, '已恢复默认')
  } catch (e: any) { flash(f.name, e.message, true) }
}

async function toggle(f: FetcherInfo) {
  try { await store.update(f.name, { enabled: !f.enabled }) }
  catch (e: any) { flash(f.name, e.message, true) }
}

async function login(f: FetcherInfo, action: string) {
  try {
    const response = await apiFetch(`/fetchers/${f.name}/login/${action}`, { method: 'POST' })
    if (!response.ok) throw new Error((await response.json()).detail)
    schedulePoll(0)
  } catch (e: any) { flash(f.name, e.message, true) }
}

async function run(f: FetcherInfo) {
  try { await store.runNow(f.name); schedulePoll(0) }
  catch (e: any) { flash(f.name, e.message, true) }
}

// ── Polling: fast while something runs, slow otherwise ──────────────────────
let pollTimer: ReturnType<typeof setTimeout> | null = null
let stopped = false

async function poll() {
  const wasRunning = new Set(store.fetchers.filter(f => f.running).map(f => f.name))
  await store.load()
  for (const f of store.fetchers) syncDraft(f)
  // A run just finished — refresh events so the dashboard reflects new data
  if (store.fetchers.some(f => wasRunning.has(f.name) && !f.running)) events.fetchEvents()
  schedulePoll(store.fetchers.some(f => f.running) ? 2000 : 15000)
}

function schedulePoll(ms: number) {
  if (stopped) return
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = setTimeout(poll, ms)
}

const now = ref(Date.now())
let clock: ReturnType<typeof setInterval>
onMounted(() => { poll(); clock = setInterval(() => { now.value = Date.now() }, 10_000) })
onUnmounted(() => { stopped = true; if (pollTimer) clearTimeout(pollTimer); clearInterval(clock) })

// ── Formatting ───────────────────────────────────────────────────────────────
function parseMs(iso: string | null): number {
  if (!iso) return NaN
  return Date.parse(iso.endsWith('Z') || /[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z')
}

function rel(iso: string | null): string {
  const ms = parseMs(iso)
  if (isNaN(ms)) return '—'
  const diff = ms - now.value
  const a = Math.abs(diff)
  let s: string
  if (a < 60_000) s = '不到 1 分钟'
  else if (a < 3_600_000) s = `${Math.floor(a / 60_000)} 分钟`
  else if (a < 86_400_000) s = `${Math.floor(a / 3_600_000)} 小时`
  else s = `${Math.floor(a / 86_400_000)} 天`
  return diff >= 0 ? `${s}后` : `${s}前`
}

function abs(iso: string | null): string {
  const ms = parseMs(iso)
  if (isNaN(ms)) return ''
  return new Date(ms).toLocaleString('zh-CN', { hour12: false })
}

function duration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

const TRIGGER_LABEL: Record<string, string> = { schedule: '定时', manual: '手动', cli: 'CLI' }

function statusOf(f: FetcherInfo): { cls: string; text: string } {
  if (f.state === 'waiting_verification') return { cls: 'running', text: '等待人工验证' }
  if (!f.configured) return { cls: 'never', text: '未配置' }
  if (f.running) return { cls: 'running', text: '运行中' }
  if (f.last_status === 'error') return { cls: 'error', text: '上次失败' }
  if (f.last_status === 'ok') return { cls: 'ok', text: '正常' }
  return { cls: 'never', text: '从未运行' }
}
</script>

<template>
  <div class="fetchers-view">
    <div class="fv-header">
      <h2 class="fv-title">抓取器</h2>
      <span class="fv-sub">启停、频率与运行状态 · 修改即时生效，无需重启</span>
      <button class="btn refresh-btn" :disabled="store.loading" @click="schedulePoll(0)">刷新</button>
    </div>

    <ReminderSettings />

    <div v-if="store.error" class="status-bar error">{{ store.error }}</div>
    <div v-else-if="!store.fetchers.length && store.loading" class="status-bar">正在加载…</div>

    <div v-for="f in store.fetchers" :key="f.name" class="fcard" :class="{ disabled: !f.enabled }">
      <!-- header -->
      <div class="fcard-head">
        <span class="fname" :style="{ color: f.description.color }">{{ f.description.title }}</span>
        <span class="fid">{{ f.name }}</span>
        <span class="badge" :class="statusOf(f).cls">
          <span v-if="f.running" class="spinner"></span>{{ statusOf(f).text }}
        </span>
        <span class="head-actions">
          <label class="switch" :title="f.enabled ? '点击停用定时抓取' : '点击启用定时抓取'">
            <input type="checkbox" :checked="f.enabled" @change="toggle(f)" />
            <span class="track"><span class="thumb"></span></span>
            <span class="switch-label">{{ f.enabled ? '已启用' : '已停用' }}</span>
          </label>
          <button class="btn run-btn" :disabled="f.running" @click="run(f)">
            {{ f.running ? '运行中…' : '立即运行' }}
          </button>
        </span>
      </div>

      <!-- facts -->
      <div class="facts">
        <div class="fact">
          <span class="k">频率</span>
          <span class="v freq" v-if="drafts[f.name]">
            <select v-model="drafts[f.name].preset">
              <option v-for="p in PRESETS" :key="p.cron" :value="p.cron">{{ p.label }}</option>
              <option :value="CUSTOM">自定义 cron…</option>
            </select>
            <input
              v-if="drafts[f.name].preset === CUSTOM"
              v-model="drafts[f.name].custom"
              class="cron-input"
              placeholder="分 时 日 月 周"
              @keyup.enter="saveCron(f)"
            />
            <button v-if="isDirty(f)" class="btn save-btn" :disabled="drafts[f.name].saving" @click="saveCron(f)">保存</button>
            <button v-if="f.cron_overridden && !isDirty(f)" class="btn link-btn" @click="resetCron(f)"
              :title="`config.yaml 默认：${f.cron_default}`">恢复默认</button>
            <span class="cron-raw">{{ f.cron }}</span>
            <span v-if="drafts[f.name].msg" class="msg" :class="{ err: drafts[f.name].msgErr }">{{ drafts[f.name].msg }}</span>
          </span>
        </div>

        <div class="fact">
          <span class="k">下次运行</span>
          <span class="v" :title="abs(f.next_run_at)">
            <template v-if="!f.enabled">— <span class="dim">（已停用）</span></template>
            <template v-else-if="f.next_run_at">{{ rel(f.next_run_at) }} <span class="dim">{{ abs(f.next_run_at) }}</span></template>
            <template v-else>—</template>
          </span>
        </div>

        <div class="fact">
          <span class="k">上次运行</span>
          <span class="v" :title="abs(f.last_started_at)">
            <template v-if="f.last_started_at">
              {{ rel(f.last_started_at) }}
              <span class="dim">· {{ TRIGGER_LABEL[f.last_trigger ?? ''] ?? f.last_trigger }}</span>
              <span v-if="f.last_finished_at && !f.running" class="dim">· 耗时 {{ duration(f.last_duration_ms) }}</span>
            </template>
            <template v-else>—</template>
          </span>
        </div>

        <div class="fact">
          <span class="k">上次结果</span>
          <span class="v">
            <template v-if="f.last_status === 'ok'">抓到 {{ f.last_count }} 条，其中 {{ f.last_changed }} 条新增/变更</template>
            <template v-else-if="f.last_status === 'error'"><span class="err-text">失败</span></template>
            <template v-else>—</template>
          </span>
        </div>

        <div class="fact">
          <span class="k">上次成功</span>
          <span class="v" :title="abs(f.last_success_at)">
            {{ f.last_success_at ? rel(f.last_success_at) : '从未成功' }}
          </span>
        </div>
      </div>

      <pre v-if="f.last_status === 'error' && f.last_error" class="error-box">{{ f.last_error }}</pre>

      <div v-if="f.description.login" class="login-actions">
        <template v-if="f.state === 'waiting_verification'">
          <span>请在打开的浏览器中登录并完成验证（3 分钟内）。</span>
          <button class="btn" @click="login(f, 'complete')">已完成登录</button>
          <button class="btn" @click="login(f, 'cancel')">取消</button>
        </template>
        <button v-else class="btn" :disabled="f.running" @click="login(f, 'start')">打开辅助登录</button>
      </div>
      <PlatformConfig :platform="f" @saved="poll" />
    </div>

    <div v-if="!store.fetchers.length && !store.loading && !store.error" class="empty">没有可用的抓取器</div>

    <div class="hint">保存配置后可单独启用定时抓取。关闭窗口后后台继续运行；完全退出可停止后台任务。</div>
  </div>
</template>

<style scoped>
.login-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 12px 16px; font-size: 12px; }
.fetchers-view { max-width: 900px; margin: 0 auto; padding: 24px 20px 60px; }

.fv-header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 16px; }
.fv-title  { font-size: 18px; font-weight: 600; letter-spacing: -.3px; }
.fv-sub    { font-size: 12px; color: var(--muted); }
.refresh-btn { margin-left: auto; }

.status-bar { padding: 8px 16px; font-size: 12px; color: var(--muted); }
.status-bar.error { color: var(--urgent); }

.fcard {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
  margin-bottom: 16px;
  overflow: hidden;
  transition: opacity .15s;
}
.fcard.disabled { border-style: dashed; }

.fcard-head {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
}
.fname { font-size: 14px; font-weight: 600; }
.fid   { font-family: var(--mono); font-size: 10px; color: var(--muted); }

.badge {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 10px; font-family: var(--mono);
  padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border);
  color: var(--muted); background: var(--bg);
}
.badge.ok      { color: #107C10; background: #EAF4E9; border-color: #B7DDB4; }
.badge.error   { color: var(--urgent); background: #FDE7E9; border-color: #F1BCBF; }
.badge.running { color: var(--accent); background: #E6F0FA; border-color: #B3D1EE; }

.spinner {
  width: 8px; height: 8px; border-radius: 50%;
  border: 1.5px solid currentColor; border-right-color: transparent;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg) } }

.head-actions { margin-left: auto; display: flex; align-items: center; gap: 12px; }

/* toggle switch */
.switch { display: flex; align-items: center; gap: 6px; cursor: pointer; user-select: none; }
.switch input { position: absolute; opacity: 0; pointer-events: none; }
.track {
  width: 28px; height: 16px; border-radius: 8px;
  background: var(--border-strong); position: relative; transition: background .15s;
}
.thumb {
  position: absolute; top: 2px; left: 2px;
  width: 12px; height: 12px; border-radius: 50%;
  background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.2); transition: left .15s;
}
.switch input:checked + .track { background: var(--accent); }
.switch input:checked + .track .thumb { left: 14px; }
.switch input:focus-visible + .track { outline: 2px solid var(--accent); outline-offset: 2px; }
.switch-label { font-size: 11px; color: var(--muted); width: 3.5em; }

.facts { padding: 8px 16px 10px; }
.fact { display: flex; align-items: center; gap: 12px; min-height: 28px; font-size: 12px; }
.k { width: 64px; flex-shrink: 0; color: var(--muted); }
.v { font-family: var(--mono); font-size: 11px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.dim { color: var(--muted); }
.err-text { color: var(--urgent); font-weight: 600; }

.freq select, .cron-input {
  font-size: 11px; font-family: var(--mono);
  padding: 2px 6px; border-radius: 4px;
  border: 1px solid var(--border-strong); background: var(--bg); color: var(--text);
}
.cron-input { width: 130px; }
.freq select:focus, .cron-input:focus { outline: none; border-color: var(--accent); }
.cron-raw { color: var(--muted); font-size: 10px; }
.msg { font-size: 11px; color: #107C10; }
.msg.err { color: var(--urgent); }

.error-box {
  margin: 0 16px 12px; padding: 8px 10px;
  background: #FDE7E9; border: 1px solid #F1BCBF; border-radius: 4px;
  color: #8A0B16; font-family: var(--mono); font-size: 11px;
  white-space: pre-wrap; word-break: break-all; max-height: 140px; overflow: auto;
}

.config { margin: 0 16px 12px; font-size: 11px; color: var(--muted); }
.config summary { cursor: pointer; }
.config pre {
  margin-top: 6px; padding: 8px 10px; background: var(--bg);
  border: 1px solid var(--border); border-radius: 4px;
  font-family: var(--mono); font-size: 11px; color: var(--text);
}

.btn {
  padding: 3px 10px; border-radius: 4px; font-size: 11px;
  border: 1px solid var(--border-strong); background: transparent;
  color: var(--muted); cursor: pointer; transition: background .1s, color .1s;
}
.btn:disabled { opacity: .55; cursor: default; }
.run-btn:hover:not(:disabled)  { background: var(--accent); color: #fff; border-color: var(--accent); }
.save-btn { color: var(--accent); border-color: var(--accent); }
.save-btn:hover:not(:disabled) { background: var(--accent); color: #fff; }
.link-btn { border: none; padding: 0 4px; color: var(--accent); }
.link-btn:hover { text-decoration: underline; }

.empty { padding: 24px; text-align: center; color: var(--muted); font-size: 13px; }
.hint { margin-top: 8px; font-size: 11px; color: var(--muted); line-height: 1.7; }
.hint code { font-family: var(--mono); font-size: 10px; background: var(--bg); padding: 0 3px; border-radius: 3px; }

@media (prefers-reduced-motion: reduce) { .spinner { animation: none; } }
</style>
