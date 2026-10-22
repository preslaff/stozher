import type { StreamState } from '../sse'
import { ageText, moduleChip } from '../style'
import type { Health } from '../types'

const STREAM_TEXT: Record<StreamState, string> = {
  connecting: 'CONNECTING',
  live: 'LIVE',
  reconnecting: 'RECONNECTING',
  denied: 'ACCESS DENIED',
}

interface Props {
  stream: StreamState
  role: string | null
  health: Health | null
  hasSim: boolean
  lastLoad: number | null
  now: number
  onLogout: () => void
}

export function StatusBar({ stream, role, health, hasSim, lastLoad, now, onLogout }: Props) {
  const secs = lastLoad === null ? null : Math.max(0, Math.round((now - lastLoad) / 1000))
  return (
    <header className="status">
      <div className="brand">
        <strong>STOZHER</strong>
        <span className="muted">GNSS Trust</span>
      </div>
      <div className="chips">
        <span className={`chip stream-${stream}`}>{STREAM_TEXT[stream]}</span>
        {hasSim && (
          <span className="chip sim" title="Simulated data is shown">
            SIM
          </span>
        )}
        {health &&
          Object.entries(health.modules).map(([name, state]) => {
            const chip = moduleChip(name, state, health.mesh_bridges)
            return (
              <span key={name} className={`chip module ${state}`} title={chip.title}>
                {chip.label}
              </span>
            )
          })}
      </div>
      <div className="right">
        <span className="muted small">updated {ageText(secs)}</span>
        {role && <span className="chip role">{role}</span>}
        <button className="link" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </header>
  )
}
