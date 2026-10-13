import { LEVEL_COLOR, sinceText, TRUST_COLOR } from '../style'
import type { Alert, Hotspot } from '../types'

interface Props {
  alerts: Alert[]
  hotspots: Hotspot[]
  selectedId: string | null
  now: number
  onPickAlert: (a: Alert) => void
  onPickHotspot: (h: Hotspot) => void
}

export function Sidebar({ alerts, hotspots, selectedId, now, onPickAlert, onPickHotspot }: Props) {
  const active = hotspots.filter((h) => h.state === 'active')
  const closed = hotspots.filter((h) => h.state === 'closed')
  return (
    <aside className="side">
      <section>
        <h2>
          Alerts <span className="count">{alerts.length}</span>
        </h2>
        {alerts.length === 0 && <p className="empty">No alerts. Evidence is being watched.</p>}
        <ul className="list">
          {alerts.map((a) => (
            <li key={a.id}>
              <button className="item" onClick={() => onPickAlert(a)}>
                <span
                  className="dot"
                  style={{ background: LEVEL_COLOR[a.level] }}
                  aria-label={a.level}
                />
                <span className="body">
                  <span className="msg">{a.message}</span>
                  <span className="meta">
                    {a.level} · {sinceText(a.created_at, now)}
                    {a.sim && <span className="chip sim">SIM</span>}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2>
          Interference <span className="count">{active.length} active</span>
        </h2>
        {hotspots.length === 0 && <p className="empty">No interference detected.</p>}
        <ul className="list">
          {[...active, ...closed].map((h) => (
            <li key={h.id}>
              <button
                className={`item ${h.id === selectedId ? 'selected' : ''}`}
                onClick={() => onPickHotspot(h)}
              >
                <span
                  className="dot"
                  style={{
                    background: h.kind === 'spoofing' ? TRUST_COLOR.SPOOFED : TRUST_COLOR.JAMMED,
                  }}
                />
                <span className="body">
                  <span className="msg">
                    {h.kind === 'spoofing' ? 'Spoofing' : 'Jamming'} · {h.state}
                  </span>
                  <span className="meta">
                    {h.independent_sources} witnesses · {h.event_count} reports · last{' '}
                    {sinceText(h.last_seen, now)}
                    {h.sim && <span className="chip sim">SIM</span>}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </aside>
  )
}
