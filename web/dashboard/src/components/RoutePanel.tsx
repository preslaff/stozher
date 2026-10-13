import { useState } from 'react'
import { kmText, rangeText, verdict } from '../route'
import { ageText, TRUST_COLOR } from '../style'
import type { MinLevel, RouteTrust } from '../types'

interface Props {
  waypoints: number
  drawing: boolean
  result: RouteTrust | null
  error: string | null
  bufferM: number
  canWatch: boolean
  onBuffer: (m: number) => void
  onDrawing: (on: boolean) => void
  onUndo: () => void
  onClear: () => void
  onWatch: (w: { name: string; minLevel: MinLevel; hours: number }) => Promise<void>
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
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const r = p.result

  async function watch(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setNote(null)
    try {
      await p.onWatch({ name: name.trim(), minLevel: level, hours })
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
