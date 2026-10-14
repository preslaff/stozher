import { useState } from 'react'
import { kmText, rangeText, verdict } from '../route'
import { ageText, TRUST_COLOR } from '../style'
import { KIND_LABEL, PASSABILITY_LABEL, TIER_COLOR } from '../hazards'
import type { HazardRoute, HazardTier, MinLevel, RouteTrust } from '../types'

interface Props {
  waypoints: number
  drawing: boolean
  result: RouteTrust | null
  hazards: HazardRoute | null
  hazardNote: string | null
  error: string | null
  bufferM: number
  canWatch: boolean
  onBuffer: (m: number) => void
  onDrawing: (on: boolean) => void
  onUndo: () => void
  onClear: () => void
  onWatch: (w: {
    name: string
    minLevel: MinLevel
    hazardMinTier: HazardTier
    hours: number
  }) => Promise<void>
}

const LEVELS: { value: MinLevel; label: string }[] = [
  { value: 'degraded', label: 'Degraded or worse' },
  { value: 'jammed', label: 'Jammed or worse' },
  { value: 'spoofed', label: 'Spoofed only' },
]

export function RoutePanel(p: Props) {
  const [name, setName] = useState('')
  const [level, setLevel] = useState<MinLevel>('degraded')
  const [hours, setHours] = useState(24)
  const [tier, setTier] = useState<HazardTier>('corroborated')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const r = p.result

  async function watch(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setNote(null)
    try {
      await p.onWatch({ name: name.trim(), minLevel: level, hazardMinTier: tier, hours })
      setNote(`Watching “${name.trim()}”. You will get an alert if its trust worsens.`)
      setName('')
    } catch (err) {
      setNote(err instanceof Error ? err.message : 'Could not watch this route')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="routepanel" aria-label="Route planner">
      <div className="rp-head">
        <h2>Route</h2>
        <div className="rp-actions">
          <button className={p.drawing ? 'on' : ''} onClick={() => p.onDrawing(!p.drawing)}>
            {p.drawing ? 'Stop drawing' : p.waypoints ? 'Add points' : 'Draw route'}
          </button>
          <button onClick={p.onUndo} disabled={!p.waypoints}>
            Undo
          </button>
          <button onClick={p.onClear} disabled={!p.waypoints && !p.drawing}>
            Clear
          </button>
        </div>
      </div>
      {p.drawing && p.waypoints < 2 && (
        <p className="muted small">Click the map to place the route&apos;s points.</p>
      )}
      <label className="rp-row">
        Corridor
        <select value={p.bufferM} onChange={(e) => p.onBuffer(Number(e.target.value))}>
          {[100, 300, 1000, 2000].map((m) => (
            <option key={m} value={m}>
              ±{kmText(m)}
            </option>
          ))}
        </select>
      </label>
      {p.error && <p className="error small">{p.error}</p>}
      {r && (
        <>
          <div className="verdict" style={{ borderColor: TRUST_COLOR[r.overall.state] }}>
            <strong style={{ color: TRUST_COLOR[r.overall.state] }}>{r.overall.state}</strong>
            <span>{verdict(r.overall.state)}</span>
            <span className="muted small">
              {kmText(r.length_m)} · worst: segment {r.overall.worst_segment + 1} ·{' '}
              {r.overall.score === null ? 'no score' : `score ${r.overall.score}`} · evidence{' '}
              {ageText(r.overall.data_age_s)}
              {r.sim && <span className="chip sim">SIM</span>}
            </span>
          </div>
          <ol className="segments">
            {r.segments.map((s) => (
              <li key={s.index}>
                <i style={{ background: TRUST_COLOR[s.state] }} />
                <span>{rangeText(s)}</span>
                <span className="muted">
                  {s.state.toLowerCase()}
                  {s.score !== null && ` · ${s.score}`}
                  {s.independent_sources > 0 && ` · ${s.independent_sources} witnesses`}
                </span>
              </li>
            ))}
          </ol>
          {r.segments.some((s) => s.state === 'UNKNOWN') && (
            <p className="muted small">
              No recent evidence is not the same as safe: those stretches are unchecked.
            </p>
          )}
          <div className="hazardcheck" aria-label="Road hazards on this route">
            <h3>Road hazards</h3>
            {p.hazardNote && <p className="muted small">{p.hazardNote}</p>}
            {p.hazards && p.hazards.count === 0 && (
              <p className="muted small">
                No reported hazards on this route. That is not a guarantee: roads nobody has
                reported are unchecked.
              </p>
            )}
            <ul className="hazlist">
              {p.hazards?.items.map((h) => (
                <li key={h.id}>
                  <i style={{ background: TIER_COLOR[h.tier] }} />
                  <span>
                    km {(h.at_m / 1000).toFixed(1)} · {KIND_LABEL[h.kind]}
                    {h.sim && <span className="chip sim">SIM</span>}
                  </span>
                  <span className="muted">
                    {PASSABILITY_LABEL[h.passability].toLowerCase()} · {h.tier} ·{' '}
                    {ageText(h.data_age_s)}
                    {h.evidence &&
                      ` · ${h.evidence.reports} report${h.evidence.reports === 1 ? '' : 's'}`}
                  </span>
                </li>
              ))}
            </ul>
            <p className="muted small">Shown separately: hazards do not change the GNSS verdict.</p>
          </div>
          {p.canWatch ? (
            <form className="watchform" onSubmit={watch}>
              <input
                placeholder="Name this route to watch it"
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
                aria-label="Route name"
              />
              <div className="rp-row">
                <select
                  value={level}
                  onChange={(e) => setLevel(e.target.value as MinLevel)}
                  aria-label="Alert level"
                >
                  {LEVELS.map((l) => (
                    <option key={l.value} value={l.value}>
                      {l.label}
                    </option>
                  ))}
                </select>
                <select
                  value={hours}
                  onChange={(e) => setHours(Number(e.target.value))}
                  aria-label="Watch for"
                >
                  {[6, 24, 72, 168].map((h) => (
                    <option key={h} value={h}>
                      {h < 48 ? `${h} h` : `${h / 24} days`}
                    </option>
                  ))}
                </select>
              </div>
              <select
                value={tier}
                onChange={(e) => setTier(e.target.value as HazardTier)}
                aria-label="Alert on hazards that are"
              >
                <option value="corroborated">Hazards: corroborated or better</option>
                <option value="confirmed">Hazards: confirmed only</option>
                <option value="unverified">Hazards: also unverified</option>
              </select>
              <button type="submit" disabled={busy || !name.trim()}>
                Watch this route
              </button>
              {note && <p className="muted small">{note}</p>}
              <p className="muted small">
                STOZHER stores only the route you drew, never anyone&apos;s position.
              </p>
            </form>
          ) : (
            <p className="muted small">A responder key can watch a route and get alerts.</p>
          )}
        </>
      )}
    </section>
  )
}
