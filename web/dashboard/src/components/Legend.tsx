import { TIER_COLOR } from '../hazards'
import { TRUST_COLOR } from '../style'
import type { HistoryWindow, TrustState } from '../types'

const LABEL: Record<TrustState, string> = {
  TRUSTED: 'Trusted',
  DEGRADED: 'Degraded',
  JAMMED: 'Jammed',
  SPOOFED: 'Spoofed',
  UNKNOWN: 'No recent evidence',
}

interface HistoryControl {
  available: boolean // responders and above
  on: boolean
  window: HistoryWindow
  onToggle: (on: boolean) => void
  onWindow: (w: HistoryWindow) => void
}

export function Legend({ note, history }: { note: string | null; history: HistoryControl }) {
  return (
    <div className="legend" aria-label="Legend">
      <strong>GNSS trust</strong>
      {(Object.keys(LABEL) as TrustState[])
        .filter((s) => s !== 'UNKNOWN')
        .map((s) => (
          <span key={s}>
            <i style={{ background: TRUST_COLOR[s] }} />
            {LABEL[s]}
          </span>
        ))}
      <span className="muted small">Uncoloured: {LABEL.UNKNOWN.toLowerCase()}</span>
      {history.available && (
        <>
          <label className="histtoggle">
            <input
              type="checkbox"
              checked={history.on}
              onChange={(e) => history.onToggle(e.target.checked)}
            />
            Last readings (expired)
          </label>
          {history.on && (
            <>
              <select
                aria-label="History window"
                value={history.window}
                onChange={(e) => history.onWindow(e.target.value as HistoryWindow)}
              >
                <option value="6h">last 6 h</option>
                <option value="24h">last 24 h</option>
                <option value="7d">last 7 days</option>
              </select>
              <span>
                <i className="histkey" />
                Grey, dashed: not current. Do not treat as safe.
              </span>
            </>
          )}
        </>
      )}
      <strong className="gap">Road hazards</strong>
      {(['unverified', 'corroborated', 'confirmed'] as const).map((t) => (
        <span key={t}>
          <i className="dotkey" style={{ background: TIER_COLOR[t] }} />
          {t.charAt(0).toUpperCase() + t.slice(1)}
        </span>
      ))}
      {note && <span className="note">{note}</span>}
    </div>
  )
}
