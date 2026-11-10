<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiFetch } from '../api'
const enabled = ref(true)
const leads = ref('1440, 60')
const message = ref('')
const busy = ref(false)
onMounted(async () => {
  try {
    const response = await apiFetch('/reminders/settings')
    if (!response.ok) throw new Error()
    const value = await response.json()
    enabled.value = value.enabled; leads.value = value.leads.join(', ')
  } catch { message.value = '提醒设置暂时无法加载，请稍后重试。' }
})
async function save() {
  busy.value = true; message.value = ''
  try {
    const values = leads.value.split(/[,，\s]+/).filter(Boolean).map(Number)
    if (!values.length || values.length > 5 || values.some(x => !Number.isInteger(x) || x < 0 || x > 10080))
      throw new Error('请输入 0–10080 之间的整数分钟，最多 5 项。')
    const response = await apiFetch('/reminders/settings', { method: 'PUT', body: JSON.stringify({ enabled: enabled.value, leads: values }) })
    if (!response.ok) throw new Error('提醒设置保存失败。')
    message.value = '已保存；从现在开始计算提醒，不补发启用前的历史通知。'
  } catch (error) { message.value = error instanceof Error ? error.message : '保存失败' }
  finally { busy.value = false }
}
</script>
<template>
  <details class="reminder-settings">
    <summary>系统提醒设置</summary>
    <form @submit.prevent="save">
      <label><input v-model="enabled" type="checkbox" /> 启用系统提醒</label>
      <label for="reminder-leads">提前分钟数（逗号分隔，默认 1440 和 60）</label>
      <input id="reminder-leads" v-model="leads" type="text" aria-describedby="reminder-help" />
      <p id="reminder-help">比赛和考试按开始时间，作业按截止时间；全天事项按当地 09:00。关闭窗口仍会提醒，完全退出后停止。</p>
      <button :disabled="busy">{{ busy ? '保存中…' : '保存提醒设置' }}</button>
      <p v-if="message" role="status">{{ message }}</p>
    </form>
  </details>
</template>
<style scoped>
.reminder-settings { margin-bottom: 18px; padding: 12px 16px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); font-size: 12px; }
summary { cursor: pointer; padding: 6px 0; font-weight: 600; }
form { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; padding-top: 12px; }
input[type=text], button { border: 1px solid var(--border-strong); background: var(--bg); color: var(--text); border-radius: 4px; padding: 8px 10px; min-height: 36px; }
p { color: var(--muted); line-height: 1.6; }
</style>
