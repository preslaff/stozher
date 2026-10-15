import { useState } from 'react'
import { ApiError } from '../api'
import { bindConfirmText, recoveredText, senderSummary } from '../senders'
import type { RebindResult, UnknownSender } from '../types'

interface Props {
  senders: UnknownSender[]
  canBind: boolean
  now: number
  onBind: (s: UnknownSender) => Promise<RebindResult | null>
}

/** Devices that sent a packet we could not place, usually after a reflash. Binding needs proof from the device itself. */
export function SendersPanel({ senders, canBind, now, onBind }: Props) {
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  if (senders.length === 0) return null

  const bind = async (s: UnknownSender) => {
    if (!window.confirm(bindConfirmText(s))) return
    setBusy(s.external_id)
    setNote(null)
    try {
      const r = await onBind(s)
      setNote(r ? recoveredText(r) : null)
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : 'The bind failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <section>
      <h2>
        Unrecognised devices <span className="count">{senders.length}</span>
      </h2>
      <p className="empty">
        A device sent a packet from an ID we do not know, for example after a reflash.
      </p>
      <ul className="list">
        {senders.map((s) => (
          <li key={`${s.transport}:${s.external_id}`}>
            <div className="item">
              <span className="body">
                <span className="msg">
                  {s.external_id} <span className="chip">{s.transport}</span>
                </span>
                <span className="meta">{senderSummary(s, now)}</span>
                <span className="meta">
                  {s.candidate_node_id === null
                    ? 'Does not prove to be any registered node. Not bindable.'
                    : `Proves to be node ${s.candidate_node_id} (${s.candidate_name ?? 'unnamed'})`}
                </span>
                {canBind && s.candidate_node_id !== null && (
                  <button disabled={busy !== null} onClick={() => void bind(s)}>
                    Bind to node {s.candidate_node_id}
                  </button>
                )}
              </span>
            </div>
          </li>
        ))}
      </ul>
      {note && <p className="empty">{note}</p>}
    </section>
  )
}
