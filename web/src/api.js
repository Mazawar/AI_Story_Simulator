// 与本地 FastAPI 通信：token 取自 URL（pywebview/serve 启动时注入）→ localStorage
const params = new URLSearchParams(location.search)
if (params.get('token')) localStorage.setItem('token', params.get('token'))
export const TOKEN = localStorage.getItem('token') || ''

function withToken(path) {
  return path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN)
}

export async function api(path, options = {}) {
  const res = await fetch(withToken(path), {
    headers: { 'Content-Type': 'application/json', 'X-Auth-Token': TOKEN },
    ...options,
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

export function sseUrl(path) {
  return withToken(path)
}
