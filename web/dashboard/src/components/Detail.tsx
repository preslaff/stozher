import { sinceText } from '../style'
import type { HotspotDetail } from '../types'

export function Detail({
  h,
  now,
  onClose,
}: {
  h: HotspotDetail
  now: number
  onClose: () => void
}) {
  const nodes = new Set(h.timeline.map((t) => t.node_id)).size
  return (
    <aside className="detail" aria-label="Interference detail">
      <button className="close" onClick={onClose} aria-label="Close">
        ×
      </button>
      <h2>
        {h.kind === 'spoofing' ? 'Spoofing' : 'Jamming'} interference{' '}
        {h.sim && <span className="chip sim">SIM</span>}
      </h2>
      <dl>
        <dt>State</dt>
        <dd>{h.state}</dd>
        <dt>Evidence</dt>
        <dd>
          {h.event_count} reports from {nodes || h.independent_sources} witnesses
        </dd>
        <dt>Confidence</dt>
        <dd>{Math.round(h.confidence * 100)}%</dd>
        <dt>Severity</dt>
        <dd>{Math.round(h.severity * 100)}%</dd>
        <dt>Position estimate</dt>
        <dd>
          {h.centroid.coordinates[1].toFixed(4)}, {h.centroid.coordinates[0].toFixed(4)}
          <span className="muted">
            {' '}
            · uncertainty {(h.ellipse.semi_major_m / 1000).toFixed(1)} ×{' '}
            {(h.ellipse.semi_minor_m / 1000).toFixed(1)} km
          </span>
        </dd>
        <dt>First seen</dt>
        <dd>{new Date(h.first_seen).toLocaleString()}</dd>
        <dt>Last report</dt>
        <dd>{sinceText(h.last_seen, now)}</dd>
      </dl>
      <h3>Timeline</h3>
      <ol className="timeline">
        {h.timeline
          .slice(-12)
          .reverse()
          .map((t) => (
            <li key={t.event_id}>
              {new Date(t.observed_at).toLocaleTimeString()} · node {t.node_id} · confidence{' '}
              {t.confidence}/31
            </li>
          ))}
      </ol>
      <p className="muted small">
        The ellipse expresses uncertainty from the witnesses' positions, not the range of a
        transmitter.
      </p>
    </aside>
  )
}
