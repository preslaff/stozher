import { TIER_COLOR } from '../hazards'
import { TRUST_COLOR } from '../style'
import type { TrustState } from '../types'

const LABEL: Record<TrustState, string> = {
  TRUSTED: 'Trusted',
  DEGRADED: 'Degraded',
  JAMMED: 'Jammed',
  SPOOFED: 'Spoofed',
  UNKNOWN: 'No recent evidence',
}

export function Legend({ note }: { note: string | null }) {
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
