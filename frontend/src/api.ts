type ApiReply = { status: number; body: string }
declare global {
  interface Window {
    concourse?: { request: (path: string, method: string, body?: string) => Promise<ApiReply> }
  }
}

// Desktop credentials remain in the main process; the renderer never receives them.
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (window.concourse) {
    const reply = await window.concourse.request(path, init.method ?? 'GET',
      typeof init.body === 'string' ? init.body : undefined)
    return new Response(reply.status === 204 ? null : reply.body, { status: reply.status })
  }
  const base = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
  const headers = new Headers(init.headers)
  if (import.meta.env.VITE_API_TOKEN) headers.set('Authorization', `Bearer ${import.meta.env.VITE_API_TOKEN}`)
  return fetch(base + path, { ...init, headers })
}
