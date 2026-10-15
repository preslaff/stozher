// Text for the "Unrecognised devices" panel (docs/mesh.md, "After reflashing a device"). Pure, so it is tested.
import { sinceText } from './style'
import type { RebindResult, UnknownSender } from './types'

export function senderSummary(s: UnknownSender, now: number): string {
  const n = `${s.count} ${s.count === 1 ? 'packet' : 'packets'}`
  return `${n} · last ${sinceText(s.last_seen, now)}`
}

/** What the operator confirms: names the node and the new ID, and says what will be replaced. */
export function bindConfirmText(s: UnknownSender): string {
  return (
    `Attach ${s.external_id} to node ${s.candidate_node_id} (${s.candidate_name ?? 'unnamed'})?\n\n` +
    `The node's current ${s.transport} ID is replaced. This is recorded in the audit log under your name. ` +
    `A packet from this device proved it holds the node's key.`
  )
}

export function recoveredText(r: RebindResult): string {
  const kept = r.recovered.filter((x) => x.status === 'accepted').length
  return kept === 0
    ? 'Bound. No earlier packet needed recovery.'
    : `Bound. ${kept} earlier ${kept === 1 ? 'packet was' : 'packets were'} recovered.`
}
