export function sevClass(households) {
  if (households >= 200) return 'crit'
  if (households >= 80) return 'high'
  if (households >= 20) return 'med'
  return 'low'
}

export const SEV_COLOR = {
  crit: '#ff4d4f', high: '#ff7a45', med: '#ffc53d', low: '#8b98a9',
}

export function faultLabel(t) {
  switch (t.localization_kind) {
    case 'span_point': return `Span ${t.span_from_pole} → ${t.span_to_pole}`
    case 'span_range': return `Span ~${t.span_from_pole} → ${t.span_to_pole}`
    case 'dt_area': return `Area under ${t.dt_id}`
    case 'dt_equipment': return `Transformer ${t.dt_id}`
    case 'feeder_area': return `Feeder ${t.feeder_id}`
    case 'sensor_point': return `Sensor at ${t.span_to_pole}`
    default: return t.fault_type
  }
}

export function kindShort(k) {
  return {
    span_point: 'SPAN', span_range: 'SPAN±', dt_area: 'DT-AREA',
    dt_equipment: 'DT', feeder_area: 'FEEDER', sensor_point: 'SENSOR',
  }[k] || k
}

export function timeAgo(iso) {
  if (!iso) return ''
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${Math.floor(s)}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}
