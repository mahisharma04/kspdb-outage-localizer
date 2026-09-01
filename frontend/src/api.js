// Same-origin in production (FastAPI serves the built app); Vite proxy in dev.
const BASE = ''

async function j(method, path, body) {
  const r = await fetch(BASE + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status}`)
  return r.status === 204 ? null : r.json()
}

export const api = {
  summary: () => j('GET', '/api/network/summary'),
  poles: () => j('GET', '/api/network/poles'),
  dts: () => j('GET', '/api/network/dts'),
  tickets: (scope = 'active') => j('GET', `/api/tickets?scope=${scope}`),
  ticket: (id) => j('GET', `/api/tickets/${id}`),
  acknowledge: (id) => j('POST', `/api/tickets/${id}/acknowledge`, {}),
  assign: (id, crew) => j('POST', `/api/tickets/${id}/assign`, { crew }),
  resolve: (id, note) => j('POST', `/api/tickets/${id}/resolve`, { note }),
  // simulator
  simSpan: () => j('POST', '/api/sim/span', { deliver_prob: 0.7 }),
  simWeakSpan: () => j('POST', '/api/sim/span', { deliver_prob: 0.15, confidence_mode: 'weak' }),
  simDt: () => j('POST', '/api/sim/dt', { deliver_prob: 0.7 }),
  simFeeder: () => j('POST', '/api/sim/feeder', { deliver_prob: 0.7 }),
  simDeadSensor: () => j('POST', '/api/sim/dead-sensor', {}),
  simNoise: () => j('POST', '/api/sim/noise', {}),
  simScheduled: () => j('POST', '/api/sim/scheduled', { scope: 'dt', darken: true }),
  simRepair: (key) => j('POST', '/api/sim/repair', { key }),
  reset: () => j('POST', '/api/sim/reset', {}),
}

// Server-Sent Events with auto-reconnect (native EventSource).
export function subscribe(onEvent, onStatus) {
  let es
  const connect = () => {
    es = new EventSource(BASE + '/api/stream')
    es.onopen = () => onStatus && onStatus('live')
    es.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)) } catch { /* keep-alive */ }
    }
    es.onerror = () => {
      onStatus && onStatus('reconnecting')
      es.close()
      setTimeout(connect, 2000)
    }
  }
  connect()
  return () => es && es.close()
}
