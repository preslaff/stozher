import { useState } from 'react'
import type { Waypoint } from '../route'
import { observedAt, PASSABILITY, REPORT_KINDS, WHEN_OPTIONS } from '../hazards'
import type { HazardKind, Passability } from '../types'

interface Props {
  placing: boolean
  point: Waypoint | null
  onPlacing: (on: boolean) => void
  onClear: () => void
  onSubmit: (r: {
    kind: HazardKind
    passability: Passability
    lon: number
    lat: number
    observedAt: Date
  }) => Promise<void>
}

/** Report an obstacle: a place, a kind, a passability and a time. Nothing else is collected. */
export function ReportPanel({ placing, point, onPlacing, onClear, onSubmit }: Props) {
  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<HazardKind>('flood')
  const [pass, setPass] = useState<Passability>('blocked')
  const [when, setWhen] = useState(0)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  if (!open)
    return (
      <button className="reportopen" onClick={() => setOpen(true)}>
        Report obstacle
      </button>
    )

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!point) return
    setBusy(true)
    setNote(null)
    try {
      await onSubmit({
        kind,
        passability: pass,
        lon: point[0],
        lat: point[1],
        observedAt: observedAt(when),
      })
      setNote(
        'Sent. It is shown as unverified until an independent report or an operator supports it.',
      )
      onClear()
    } catch (err) {
      setNote(err instanceof Error ? err.message : 'Could not send the report')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="reportpanel" onSubmit={submit} aria-label="Report an obstacle">
      <div className="rp-head">
        <h2>Report obstacle</h2>
        <button
          type="button"
          className="link"
          onClick={() => {
            onPlacing(false)
            onClear()
            setOpen(false)
          }}
        >
          Close
        </button>
      </div>
      <label className="rp-row">
        What
        <select value={kind} onChange={(e) => setKind(e.target.value as HazardKind)}>
          {REPORT_KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      <label className="rp-row">
        Road is
        <select value={pass} onChange={(e) => setPass(e.target.value as Passability)}>
          {PASSABILITY.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      <label className="rp-row">
        Seen
        <select value={when} onChange={(e) => setWhen(Number(e.target.value))}>
          {WHEN_OPTIONS.map((w) => (
            <option key={w.minutes} value={w.minutes}>
              {w.label}
            </option>
          ))}
        </select>
      </label>
      <div className="rp-row">
        <button type="button" className={placing ? 'on' : ''} onClick={() => onPlacing(!placing)}>
          {placing ? 'Click the map…' : point ? 'Move the point' : 'Place on map'}
        </button>
        <span className="muted small">
          {point ? `${point[1].toFixed(4)}, ${point[0].toFixed(4)}` : 'no place chosen'}
        </span>
      </div>
      <button type="submit" disabled={busy || !point}>
        Send report
      </button>
      {note && <p className="muted small">{note}</p>}
      <p className="muted small">
        Places only: no photos, text or names are sent, and your device location is never read. The
        point is the one you chose on the map.
      </p>
    </form>
  )
}
