import { faultLabel, timeAgo } from '../utils.js'

export default function IncidentDetail({ ticket, onAck, onAssign, onResolve, onRepair }) {
  if (!ticket) return <div className="empty">Select an incident to see the dispatch briefing, confidence reasoning, and controls.</div>

  const copy = () => navigator.clipboard?.writeText(`${ticket.lat}, ${ticket.lon}`)
  const prog = Math.round((ticket.restoration_progress || 0) * 100)
  const closed = ['verified', 'closed'].includes(ticket.status)

  return (
    <div className="detail-body">
      <h2>{faultLabel(ticket)}</h2>
      <div className="row" style={{ gap: 6 }}>
        <span className={'badge ' + ticket.confidence_band}>{ticket.confidence_band} · {ticket.confidence}</span>
        <span className="badge topo">{ticket.topology_source}</span>
        <span className="badge status">{ticket.status}</span>
      </div>

      <div className="brief">
        <span className="tag">Dispatch briefing (AI)</span>
        {ticket.ai_summary}
      </div>

      <div className="kv">
        <span className="k">Navigate</span>
        <span className="coord" title="click to copy" onClick={copy}>
          {ticket.lat?.toFixed(5)}, {ticket.lon?.toFixed(5)} ⧉
        </span>
        <span className="k">PIN / ward</span><span>{ticket.pincode || '—'} · {ticket.ward || '—'}</span>
        <span className="k">Feeder / DT</span><span>{ticket.feeder_id || '—'} · {ticket.dt_id || '—'}</span>
        <span className="k">Affected</span><span>{ticket.households_affected} homes · {ticket.poles_affected} poles</span>
        <span className="k">First seen</span><span>{timeAgo(ticket.first_symptom_ts || ticket.detected_at)}</span>
      </div>

      <div>
        <span className="k" style={{ color: 'var(--muted)', fontSize: 12 }}>Restoration (from telemetry)</span>
        <div className="progress"><div style={{ width: prog + '%' }} /></div>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>{prog}% of affected poles live</span>
      </div>

      <div className="reasons">
        <span className="k" style={{ color: 'var(--muted)', fontSize: 12 }}>Why this location & confidence</span>
        <ul>{ticket.reasons?.map((r, i) => <li key={i}>{r}</li>)}</ul>
      </div>

      <div className="timeline">
        {ticket.events?.map((e, i) => (
          <div className="ev" key={i}>
            <div><b>{e.kind}</b></div>
            <div>{e.detail}</div>
            <div className="t">{timeAgo(e.ts)}</div>
          </div>
        ))}
      </div>

      {!closed && (
        <div className="actions">
          <button onClick={() => onAck(ticket.id)} disabled={ticket.status !== 'detected'}>Acknowledge</button>
          <button onClick={() => onAssign(ticket.id)} disabled={!['detected', 'acknowledged'].includes(ticket.status)}>Assign crew</button>
          <button className="warn" onClick={() => onResolve(ticket.id)}>Mark resolved</button>
          <button className="primary" onClick={() => onRepair(ticket.incident_key)}>⚡ Simulate repair</button>
        </div>
      )}
    </div>
  )
}
