import { useState } from 'react'
import {
  DECISIONS,
  KIND_LABEL,
  PASSABILITY_LABEL,
  reasonLabel,
  TIER_COLOR,
  TIER_TEXT,
} from '../hazards'
import { ageText } from '../style'
import type { Hazard } from '../types'

type Action = keyof typeof DECISIONS
const ACTION_LABEL: Record<Action, string> = {
  confirm: 'Confirm',
  close: 'Close (road clear)',
  dismiss: 'Dismiss (false)',
}

interface Props {
  h: Hazard
  canDecide: boolean
  onDecide: (action: Action, reason: string) => Promise<void>
  onClose: () => void
}

export function HazardDetail({ h, canDecide, onDecide, onClose }: Props) {
  const [action, setAction] = useState<Action>('confirm')
  const [reason, setReason] = useState<string>(DECISIONS.confirm[0])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const ev = h.evidence

  async function decide(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      await onDecide(action, reason)
    } catch (x) {
      setErr(x instanceof Error ? x.message : 'Decision failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside className="detail" aria-label="Hazard detail">
      <button className="close" onClick={onClose} aria-label="Close">
        ×
      </button>
      <h2>
        {KIND_LABEL[h.kind]} {h.sim && <span className="chip sim">SIM</span>}
      </h2>
      <p className="tier" style={{ borderColor: TIER_COLOR[h.tier] }}>
        <strong style={{ color: TIER_COLOR[h.tier] }}>{h.tier}</strong>
        <span>{TIER_TEXT[h.tier]}</span>
      </p>
      <dl>
        <dt>Road is</dt>
        <dd>{PASSABILITY_LABEL[h.passability].toLowerCase()} (as reported)</dd>
        <dt>Status</dt>
        <dd>{h.status}</dd>
        <dt>Last report</dt>
        <dd>{ageText(h.data_age_s)}</dd>
        <dt>Valid until</dt>
        <dd>{new Date(h.valid_until).toLocaleString()}</dd>
        {ev && (
          <>
            <dt>Evidence</dt>
            <dd>
              {ev.reports} report{ev.reports === 1 ? '' : 's'} from {ev.independent_teams}{' '}
              independent team{ev.independent_teams === 1 ? '' : 's'}
              {ev.clear_reports > 0 && `, ${ev.clear_reports} saying clear`}
            </dd>
          </>
        )}
        {h.decision && (
          <>
            <dt>Decision</dt>
            <dd>
              {h.decision}
              {h.decision_reason && ` · ${reasonLabel(h.decision_reason).toLowerCase()}`}
            </dd>
          </>
        )}
      </dl>
      {h.blur_m && (
        <p className="muted small">Position shown on a {h.blur_m} m grid, not exactly.</p>
      )}
      {h.status === 'expired' && (
        <p className="muted small">
          Expired is not the same as clear: nobody has reported this road passable.
        </p>
      )}
      {canDecide && h.status !== 'closed' && h.status !== 'dismissed' && (
        <form className="decide" onSubmit={decide}>
          <h3>Operator decision</h3>
          <select
            aria-label="Decision"
            value={action}
            onChange={(e) => {
              const a = e.target.value as Action
              setAction(a)
              setReason(DECISIONS[a][0])
            }}
          >
            {(Object.keys(DECISIONS) as Action[]).map((a) => (
              <option key={a} value={a}>
                {ACTION_LABEL[a]}
              </option>
            ))}
          </select>
          <select aria-label="Reason" value={reason} onChange={(e) => setReason(e.target.value)}>
            {DECISIONS[action].map((r) => (
              <option key={r} value={r}>
                {reasonLabel(r)}
              </option>
            ))}
          </select>
          <button type="submit" disabled={busy}>
            Record decision
          </button>
          {err && <p className="error small">{err}</p>}
          <p className="muted small">Decisions are recorded with your key and cannot be undone.</p>
        </form>
      )}
    </aside>
  )
}
