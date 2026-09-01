import { useCallback, useEffect, useRef, useState } from 'react'
import { api, subscribe } from './api.js'
import MapView from './components/MapView.jsx'
import IncidentList from './components/IncidentList.jsx'
import IncidentDetail from './components/IncidentDetail.jsx'
import SimPanel from './components/SimPanel.jsx'

const COLS = ['pole_id', 'lat', 'lon', 'dt_id', 'feeder_id', 'has_device', 'energized']

export default function App() {
  const [summary, setSummary] = useState(null)
  const [poles, setPoles] = useState([])
  const [scope, setScope] = useState('active')
  const [tickets, setTickets] = useState([])
  const [counts, setCounts] = useState({})
  const [selectedId, setSelectedId] = useState(null)
  const [selected, setSelected] = useState(null)
  const [conn, setConn] = useState('connecting')
  const [toast, setToast] = useState(null)
  const [center, setCenter] = useState([12.945, 77.6])
  const scopeRef = useRef(scope)
  scopeRef.current = scope

  const flash = (m) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const loadPoles = useCallback(async () => {
    const d = await api.poles()
    const rows = d.poles.map((r) => Object.fromEntries(COLS.map((c, i) => [c, r[i]])))
    setPoles(rows)
    if (rows.length) {
      const la = rows.reduce((s, p) => s + p.lat, 0) / rows.length
      const lo = rows.reduce((s, p) => s + p.lon, 0) / rows.length
      setCenter([la, lo])
    }
  }, [])

  const loadTickets = useCallback(async () => {
    const [a, p, c] = await Promise.all([api.tickets('active'), api.tickets('planned'), api.tickets('closed')])
    setCounts({ active: a.tickets.length, planned: p.tickets.length, closed: c.tickets.length })
    setTickets({ active: a.tickets, planned: p.tickets, closed: c.tickets }[scopeRef.current])
  }, [])

  const refreshSelected = useCallback(async (id) => {
    if (!id) return
    try { setSelected(await api.ticket(id)) } catch { setSelected(null) }
  }, [])

  useEffect(() => {
    api.summary().then(setSummary)
    loadPoles(); loadTickets()
    const unsub = subscribe(() => { loadTickets(); loadPoles(); if (scopeRef.current) refreshSelected(selectedIdRef.current) }, setConn)
    const poll = setInterval(loadPoles, 5000)
    return () => { unsub(); clearInterval(poll) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // keep a ref of selectedId for the SSE closure
  const selectedIdRef = useRef(null)
  useEffect(() => { selectedIdRef.current = selectedId; refreshSelected(selectedId) }, [selectedId, refreshSelected])
  useEffect(() => { loadTickets() }, [scope, loadTickets])

  const select = (id) => setSelectedId(id)

  const runSim = async (kind) => {
    try {
      let r
      if (kind === 'span') r = await api.simSpan()
      else if (kind === 'weakSpan') r = await api.simWeakSpan()
      else if (kind === 'dt') r = await api.simDt()
      else if (kind === 'feeder') r = await api.simFeeder()
      else if (kind === 'deadSensor') r = await api.simDeadSensor()
      else if (kind === 'scheduled') r = await api.simScheduled()
      else if (kind === 'noise') r = await api.simNoise()
      else if (kind === 'reset') { r = await api.reset(); setSelectedId(null) }
      flash(simMsg(kind, r))
      setTimeout(() => { loadTickets(); loadPoles() }, 800)
    } catch (e) { flash('Error: ' + e.message) }
  }

  const act = async (fn, id, ...args) => {
    try { const res = await fn(id, ...args); if (res && res.accepted === false) flash(res.message) }
    catch (e) { flash('Error: ' + e.message) }
    finally { loadTickets(); refreshSelected(id) }
  }

  return (
    <div className="app">
      <div className="header">
        <div>
          <h1>KSPDB · Outage Localizer</h1>
          <div className="sub">{summary?.subdivision || '…'}</div>
        </div>
        <div className="spacer" />
        <div className={'stat' + (counts.active ? ' alarm' : '')}>
          <div className="n">{counts.active ?? 0}</div><div className="l">Active faults</div>
        </div>
        <div className="stat"><div className="n">{counts.planned ?? 0}</div><div className="l">Planned</div></div>
        <div className="stat"><div className="n">{summary?.poles ?? '—'}</div><div className="l">Poles</div></div>
        <div className="stat"><div className="n">{summary ? summary.dts_topology_inferred + '/' + summary.dts : '—'}</div><div className="l">DTs inferred</div></div>
        <div className="conn"><span className={'dot ' + conn} />{conn}</div>
      </div>

      <div className="main">
        <div className="col list">
          <IncidentList tickets={tickets} selectedId={selectedId} onSelect={select}
            scope={scope} setScope={setScope} counts={counts} />
        </div>

        <div className="col map-wrap">
          <MapView poles={poles} tickets={tickets} selected={selected} onSelect={select} center={center} />
          <div className="legend">
            <div className="row"><span className="mini" style={{ background: '#3fb950' }} /> live pole</div>
            <div className="row"><span className="mini" style={{ background: '#ff4d4f' }} /> dark pole</div>
            <div className="row"><span className="mini" style={{ background: '#8b98a9' }} /> no device</div>
            <div className="row" style={{ marginTop: 4, color: 'var(--muted)' }}>bubble size = homes affected</div>
          </div>
          <SimPanel onRun={runSim} />
          {toast && <div className="toast">{toast}</div>}
        </div>

        <div className="col detail">
          <IncidentDetail ticket={selected}
            onAck={(id) => act(api.acknowledge, id)}
            onAssign={(id) => act(api.assign, id, 'Crew-' + Math.ceil(Math.random() * 9))}
            onResolve={(id) => act(api.resolve, id, 'Field crew reports fixed')}
            onRepair={(key) => api.simRepair(key).then(() => flash('Repair telemetry injected — watch it auto-verify')).catch((e) => flash(e.message))}
          />
        </div>
      </div>
    </div>
  )
}

function simMsg(kind, r) {
  if (!r) return 'done'
  if (kind === 'span') return `Span fault injected: ${r.from_pole} → ${r.to_pole} (${r.downstream_poles} poles downstream)`
  if (kind === 'weakSpan') return `Weak-confidence span fault injected: ${r.from_pole} → ${r.to_pole} (deliver_prob=${r.deliver_prob})`
  if (kind === 'dt') return `DT fault injected on ${r.dt_id} (${r.poles} poles)`
  if (kind === 'feeder') return `Feeder fault injected on ${r.feeder_id} (${r.dts} DTs)`
  if (kind === 'deadSensor') return `Dead-sensor injected at ${r.pole_id} — should NOT raise an outage`
  if (kind === 'scheduled') return `Scheduled outage on ${r.target_id} — should be suppressed as planned`
  if (kind === 'noise') return `Duplicate + 5h-stale message injected on ${r.pole_id}`
  if (kind === 'reset') return 'Network reset to all-live'
  return 'done'
}
