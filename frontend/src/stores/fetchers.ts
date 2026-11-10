import { defineStore } from 'pinia'
import { ref } from 'vue'
import { registerPlatforms } from '../platforms'
import type { FetcherInfo } from '../types'

import { apiFetch } from '../api'

async function errorText(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') return body.detail
  } catch { /* non-JSON body */ }
  return `HTTP ${res.status}`
}

export const useFetchersStore = defineStore('fetchers', () => {
  const fetchers = ref<FetcherInfo[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  function replace(info: FetcherInfo) {
    const i = fetchers.value.findIndex(f => f.name === info.name)
    if (i === -1) fetchers.value.push(info)
    else fetchers.value[i] = info
  }

  async function load() {
    loading.value = true
    try {
      const res = await apiFetch(`/fetchers`)
      if (!res.ok) throw new Error(await errorText(res))
      fetchers.value = await res.json()
      registerPlatforms(fetchers.value)
      error.value = null
    } catch (e: any) {
      error.value = e.message ?? '加载失败'
    } finally {
      loading.value = false
    }
  }

  async function update(name: string, patch: { enabled?: boolean; cron?: string | null }) {
    const res = await apiFetch(`/fetchers/${name}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
    if (!res.ok) throw new Error(await errorText(res))
    replace(await res.json())
  }

  async function runNow(name: string) {
    const res = await apiFetch(`/fetchers/${name}/run`, { method: 'POST' })
    if (!res.ok && res.status !== 409) throw new Error(await errorText(res))
    const f = fetchers.value.find(x => x.name === name)
    if (f) f.running = true
  }

  return { fetchers, loading, error, load, update, runNow }
})
