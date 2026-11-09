import { useState } from 'react'
import { ApiError } from '../api'
import { benchText, deliveryText, FRESH_COLOR, freshness, receptionText, sinceText } from '../style'
import type { NodeInfo } from '../types'

interface Props {
  nodes: NodeInfo[]
  now: number
  canBench: boolean
  onBench: (nodeId: number, on: boolean) => Promise<void>
}

/** Operators only: when each witness was last heard, how well it receives, and whether it has been told what arrived. */
export function NodesPanel({ nodes, now, canBench, onBench }: Props) {
  const [busy, setBusy] = useState<number | null>(null)
  const [note, setNote] = useState<string | null>(null)
  if (nodes.length === 0) return null

  const toggle = async (n: NodeInfo) => {
    const on = !n.bench
    const ask = on
      ? `Put ${n.name} on the bench? Its records stay visible but stop counting as evidence for the trust grid, hotspots and alerts (24 h at most unless set again).`
      : `Put ${n.name} back in the field? Its new records count as evidence again.`
    if (!window.confirm(ask)) return
    setBusy(n.id)
    setNote(null)
    try {
      await onBench(n.id, on)
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : 'The change failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <section>
      <h2>
        Witness nodes <span className="count">{nodes.length}</span>
      </h2>
      <ul className="list">
        {nodes.map((n) => {
          const f = freshness(n.last_seen, now)
          return (
            <li key={n.id}>
              <div className="item">
                <span className="dot" style={{ background: FRESH_COLOR[f] }} aria-label={`${f}`} />
                <span className="body">
                  <span className="msg">
                    {n.name}
                    {n.sim && <span className="chip sim">SIM</span>}
                    {n.bench && <span className="chip bench">BENCH</span>}
                  </span>
                  <span className="meta">
                    {n.status !== 'active' && n.status !== 'bench' ? `${n.status} · ` : ''}
                    {n.last_seen ? `heard ${sinceText(n.last_seen, now)}` : 'never heard'}
                  </span>
                  {n.bench && <span className="meta">{benchText(n.bench)}</span>}
                  <span className="meta">{receptionText(n.reception)}</span>
                  <span className="meta">{deliveryText(n.delivery, now)}</span>
                  {canBench && n.status !== 'retired' && (
                    <button disabled={busy !== null} onClick={() => void toggle(n)}>
                      {n.bench ? 'Back to the field' : 'Put on the bench'}
                    </button>
                  )}
                </span>
              </div>
            </li>
          )
        })}
      </ul>
      {note && <p className="empty">{note}</p>}
    </section>
  )
}
