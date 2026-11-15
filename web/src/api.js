// Thin fetch wrapper: JSON in/out, FastAPI error details surfaced as Error messages.
export async function api(path, { method = 'GET', body, headers } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json', ...headers } : headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const d = data.detail
    throw new Error(typeof d === 'string' ? d : d ? JSON.stringify(d) : `HTTP ${res.status}`)
  }
  return data
}

export const fmtKm = (km) => (km == null ? '—' : km < 10 ? `${(km * 1000).toFixed(0)} m` : `${km.toFixed(1)} km`)
export const fmtTime = (iso) => (iso ? iso.replace('T', ' ').replace(/\.\d+Z$/, 'Z') : '—')
export const levelClass = (l) => ({ RED: 'lvl-red', AMBER: 'lvl-amber', WATCH: 'lvl-watch' })[l] || ''
