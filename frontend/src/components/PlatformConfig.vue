<script setup lang="ts">
import { reactive, ref, nextTick, watch } from 'vue'
import type { FetcherInfo } from '../types'
import { apiFetch } from '../api'

const props = defineProps<{ platform: FetcherInfo }>()
const emit = defineEmits<{ saved: [] }>()
const values = reactive<Record<string, string | boolean>>({})
const busy = ref(false)
const message = ref('')
const failed = ref(false)
const summary = ref<HTMLElement>()
function reset() {
  for (const field of props.platform.description.fields) {
    const value = props.platform.config[field.name]
    values[field.name] = field.secret ? '' : field.type === 'ids'
      ? (value ?? []).join(', ') : value ?? false
  }
}
watch(() => props.platform.name, reset, { immediate: true })
async function submit(clear = false) {
  busy.value = true; message.value = ''; failed.value = false
  try {
    const config: Record<string, unknown> = {}
    const credentials: Record<string, string> = {}
    for (const field of props.platform.description.fields) {
      const value = values[field.name]
      if (field.secret) credentials[field.name] = String(value)
      else config[field.name] = field.type === 'ids'
        ? String(value).split(/[,，\s]+/).filter(Boolean) : value
    }
    const hasSecret = Object.values(credentials).some(Boolean)
    const response = await apiFetch(`/fetchers/${props.platform.name}/config`, {
      method: 'PUT', body: JSON.stringify({ config, clear_credentials: clear,
        ...(hasSecret && !clear ? { credentials } : {}) }),
    })
    const result = await response.json()
    if (!response.ok) throw new Error(result.detail ?? '保存失败')
    for (const field of props.platform.description.fields) if (field.secret) values[field.name] = ''
    message.value = clear ? '已清除凭据，平台已停用。' : '配置已保存。需要定时抓取时，请单独启用平台。'
    emit('saved')
  } catch (error) {
    failed.value = true
    message.value = error instanceof Error ? error.message : '保存失败，请重试'
    await nextTick(); summary.value?.focus()
  } finally { busy.value = false }
}
async function check() {
  busy.value = true; failed.value = false
  try {
    const response = await apiFetch(`/fetchers/${props.platform.name}/check`, { method: 'POST' })
    const result = await response.json()
    if (!response.ok) throw new Error(result.detail)
    message.value = `连接成功，读取到 ${result.count} 项。`
  } catch (error) { failed.value = true; message.value = error instanceof Error ? error.message : '连接失败' }
  finally { busy.value = false }
}
</script>

<template>
  <details class="platform-config">
    <summary>配置平台 · {{ platform.configured ? '已配置' : '尚未配置' }}</summary>
    <form @submit.prevent="submit()">
      <p v-if="message" ref="summary" tabindex="-1" :role="failed ? 'alert' : 'status'" :class="{ error: failed }">{{ message }}</p>
      <fieldset :disabled="busy || platform.running">
        <div v-for="field in platform.description.fields" :key="field.name" class="field">
          <label :for="`${platform.name}-${field.name}`">{{ field.label }}</label>
          <input v-if="field.type === 'boolean'" :id="`${platform.name}-${field.name}`" v-model="values[field.name]" type="checkbox" />
          <input v-else :id="`${platform.name}-${field.name}`" v-model="values[field.name]"
            :type="field.secret ? 'password' : 'text'" :autocomplete="field.secret ? 'new-password' : 'off'"
            :placeholder="field.secret && platform.credentials_saved ? '已保存；留空保留原值' : ''"
            :aria-describedby="field.help ? `${platform.name}-${field.name}-help` : undefined" />
          <small v-if="field.help" :id="`${platform.name}-${field.name}-help`">{{ field.help }}</small>
        </div>
        <p v-if="platform.credentials_saved" class="saved">凭据已加密保存。替换账号时请重新填写完整凭据。</p>
        <div class="actions">
          <button type="submit">{{ busy ? '处理中…' : '保存配置' }}</button>
          <button v-if="platform.credentials_saved" type="button" @click="submit(true)">清除凭据并停用</button>
          <button v-if="platform.name === 'moodle' && platform.configured" type="button" @click="check">验证连接</button>
        </div>
      </fieldset>
    </form>
  </details>
</template>

<style scoped>
.platform-config { padding: 12px 16px; border-top: 1px solid var(--border); font-size: 12px; }
summary { cursor: pointer; padding: 6px 0; font-weight: 600; }
fieldset { border: 0; padding: 12px 0 0; min-width: 0; }
.field { display: grid; grid-template-columns: 160px minmax(0, 1fr); align-items: center; gap: 8px 12px; margin-bottom: 14px; }
input { min-height: 34px; padding: 6px 9px; background: var(--bg); color: var(--text); border: 1px solid var(--border-strong); border-radius: 4px; min-width: 0; }
input[type=checkbox] { justify-self: start; width: 18px; min-height: 18px; }
small { grid-column: 2; color: var(--muted); line-height: 1.6; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
button { min-height: 36px; border: 1px solid var(--border-strong); border-radius: 4px; background: var(--surface); color: var(--text); padding: 7px 12px; cursor: pointer; }
button:disabled, fieldset:disabled { opacity: .65; }
input:focus-visible, button:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.error { color: var(--urgent); }
.saved { color: var(--muted); }
@media (max-width: 640px) { .field { grid-template-columns: 1fr; } small { grid-column: 1; } }
</style>
