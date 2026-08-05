import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from 'react-leaflet'
import { useEffect } from 'react'
import { SEV_COLOR, sevClass, faultLabel } from '../utils.js'

function FlyTo({ target }) {
  const map = useMap()
  useEffect(() => {
    if (target && target.lat) map.flyTo([target.lat, target.lon], 16, { duration: 0.8 })
  }, [target, map])
  return null
}

export default function MapView({ poles, tickets, selected, onSelect, center }) {
  // Poles for the selected fault's DT — reveals the live/dark boundary.
  const focusPoles = selected
    ? poles.filter((p) => p.dt_id === selected.dt_id)
    : []
  const darkPoles = poles.filter((p) => p.energized === false)

  return (
    <MapContainer center={center} zoom={13} preferCanvas zoomControl={true}>
      <TileLayer
        attribution='&copy; OpenStreetMap contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FlyTo target={selected} />

      {/* Network context: poles of the focused DT, green live / red dark */}
      {focusPoles.map((p) => (
        <CircleMarker
          key={'f' + p.pole_id}
          center={[p.lat, p.lon]}
          radius={4}
          pathOptions={{
            color: p.energized === false ? '#ff4d4f' : p.has_device ? '#3fb950' : '#8b98a9',
            fillOpacity: 0.9, weight: 1,
          }}
        >
          <Popup>{p.pole_id} · {p.energized === false ? 'DARK' : p.has_device ? 'live' : 'no device'}</Popup>
        </CircleMarker>
      ))}

      {/* All dark poles across the network (faint) so nothing is missed */}
      {!selected && darkPoles.map((p) => (
        <CircleMarker key={'d' + p.pole_id} center={[p.lat, p.lon]} radius={3}
          pathOptions={{ color: '#ff4d4f', fillOpacity: 0.7, weight: 0 }} />
      ))}

      {/* Incident markers, sized by households, coloured by severity */}
      {tickets.map((t) => {
        if (t.lat == null) return null
        const sev = sevClass(t.households_affected)
        const isSel = selected && selected.id === t.id
        return (
          <CircleMarker
            key={t.id}
            center={[t.lat, t.lon]}
            radius={Math.min(26, 8 + Math.sqrt(t.households_affected))}
            pathOptions={{
              color: '#fff', weight: isSel ? 3 : 1.5,
              fillColor: SEV_COLOR[sev], fillOpacity: isSel ? 0.9 : 0.65,
            }}
            eventHandlers={{ click: () => onSelect(t.id) }}
          >
            <Popup>
              <b>{faultLabel(t)}</b><br />
              {t.households_affected} homes · {t.poles_affected} poles<br />
              confidence {t.confidence} ({t.confidence_band})
            </Popup>
          </CircleMarker>
        )
      })}
    </MapContainer>
  )
}
