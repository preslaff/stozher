import { ageText, LEVEL_COLOR, sinceText, TRUST_COLOR } from '../style'
import { KIND_LABEL, PASSABILITY_LABEL, TIER_COLOR, tierCounts } from '../hazards'
import type {
  Alert,
  Hazard,
  Hotspot,
  RebindResult,
  UnknownSender,
  WatchedRouteItem,
} from '../types'
import { SendersPanel } from './SendersPanel'

interface Props {
  alerts: Alert[]
  hotspots: Hotspot[]
  hazards: Hazard[]
  selectedHazardId: string | null
  onPickHazard: (h: Hazard) => void
  watched: WatchedRouteItem[]
  selectedId: string | null
  now: number
  onPickAlert: (a: Alert) => void
  onPickHotspot: (h: Hotspot) => void
  onPickRoute: (w: WatchedRouteItem) => void
  onDeleteRoute: (w: WatchedRouteItem) => void
  senders: UnknownSender[]
  canBind: boolean
  onBind: (s: UnknownSender) => Promise<RebindResult | null>
}

export function Sidebar({
  alerts,
  hotspots,
  hazards,
  selectedHazardId,
  onPickHazard,
  watched,
  selectedId,
  now,
  onPickAlert,
  onPickHotspot,
  onPickRoute,
  onDeleteRoute,
  senders,
  canBind,
  onBind,
}: Props) {
  const active = hotspots.filter((h) => h.state === 'active')
  const closed = hotspots.filter((h) => h.state === 'closed')
  return (
    <aside className="side">
      <SendersPanel senders={senders} canBind={canBind} now={now} onBind={onBind} />
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
                  <span className="msg">
                    {a.route_id && <span className="chip route">route</span>}
                    {a.message}
                  </span>
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
      <section>
        <h2>
          Road hazards{' '}
          <span className="count">
            {tierCounts(hazards).confirmed} confirmed · {tierCounts(hazards).corroborated}{' '}
            corroborated · {tierCounts(hazards).unverified} unverified
          </span>
        </h2>
        {hazards.filter((h) => h.status === 'active').length === 0 && (
          <p className="empty">No active hazards reported.</p>
        )}
        <ul className="list">
          {hazards
            .filter((h) => h.status === 'active')
            .map((h) => (
              <li key={h.id}>
                <button
                  className={`item ${h.id === selectedHazardId ? 'selected' : ''}`}
                  onClick={() => onPickHazard(h)}
                >
                  <span className="dot" style={{ background: TIER_COLOR[h.tier] }} />
                  <span className="body">
                    <span className="msg">
                      {KIND_LABEL[h.kind]} · {PASSABILITY_LABEL[h.passability].toLowerCase()}
                    </span>
                    <span className="meta">
                      {h.tier} · last report {ageText(h.data_age_s)}
                      {h.sim && <span className="chip sim">SIM</span>}
                    </span>
                  </span>
                </button>
              </li>
            ))}
        </ul>
      </section>
      <section>
        <h2>
          Watched routes <span className="count">{watched.length}</span>
        </h2>
        {watched.length === 0 && (
          <p className="empty">Draw a route on the map, then watch it to get alerts.</p>
        )}
        <ul className="list">
          {watched.map((w) => (
            <li key={w.id} className="withdel">
              <button className="item" onClick={() => onPickRoute(w)}>
                <span
                  className="dot"
                  style={{ background: TRUST_COLOR[w.state ?? 'UNKNOWN'] }}
                  aria-label={w.state ?? 'UNKNOWN'}
                />
                <span className="body">
                  <span className="msg">{w.name}</span>
                  <span className="meta">
                    {(w.state ?? 'UNKNOWN').toLowerCase()} · alerts from {w.min_level} · until{' '}
                    {new Date(w.expires_at).toLocaleString()}
                  </span>
                </span>
              </button>
              <button
                className="del"
                onClick={() => onDeleteRoute(w)}
                aria-label={`Stop watching ${w.name}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </section>
    </aside>
  )
}
