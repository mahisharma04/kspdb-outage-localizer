import { sevClass, faultLabel, kindShort, timeAgo } from '../utils.js'

export default function IncidentList({ tickets, selectedId, onSelect, scope, setScope, counts }) {
  return (
    <div>
      <div className="tabs">
        {['active', 'planned', 'closed'].map((s) => (
          <div key={s} className={'tab' + (scope === s ? ' active' : '')} onClick={() => setScope(s)}>
            {s[0].toUpperCase() + s.slice(1)} ({counts[s] ?? 0})
          </div>
        ))}
      </div>
      <div className="list-head">
        {scope === 'active' ? 'Live incidents — most households first' : scope + ' tickets'}
      </div>
      {tickets.length === 0 && <div className="empty">No {scope} incidents.</div>}
      {tickets.map((t) => (
        <div key={t.id}
          className={'card' + (t.id === selectedId ? ' sel' : '')}
          onClick={() => onSelect(t.id)}>
          <div className="row">
            <span className={'sev ' + sevClass(t.households_affected)} />
            <span className="title">{faultLabel(t)}</span>
            <span className="spacer1" />
            <span className={'badge ' + t.confidence_band}>{t.confidence_band}</span>
          </div>
          <div className="meta">
            <b>{t.households_affected}</b> homes · {t.poles_affected} poles · {t.pincode || '—'}
          </div>
          <div className="row" style={{ marginTop: 6, gap: 6 }}>
            <span className="badge topo">{kindShort(t.localization_kind)}</span>
            <span className="badge topo">{t.topology_source}</span>
            <span className="spacer1" />
            <span className="meta" style={{ margin: 0 }}>{t.status} · {timeAgo(t.detected_at)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}
