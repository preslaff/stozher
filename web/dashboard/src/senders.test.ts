import { describe, expect, it } from 'vitest'
import { bindConfirmText, recoveredText, senderSummary } from './senders'
import type { RebindResult, UnknownSender } from './types'

const base: UnknownSender = {
  transport: 'mesh',
  external_id: '!0b533538',
  first_seen: '2026-10-15T06:12:00Z',
  last_seen: '2026-10-15T06:12:00Z',
  count: 1,
  candidate_node_id: 4243,
  candidate_name: 'T-Beam a4b0',
}

describe('senders text', () => {
  it('summarises packets and age', () => {
    const now = Date.parse('2026-10-15T06:17:00Z')
    expect(senderSummary(base, now)).toBe('1 packet · last 5 min ago')
    expect(senderSummary({ ...base, count: 3 }, now)).toContain('3 packets')
  })

  it('the confirmation names the new ID, the node and the audit', () => {
    const t = bindConfirmText(base)
    expect(t).toContain('!0b533538')
    expect(t).toContain('4243')
    expect(t).toContain('T-Beam a4b0')
    expect(t).toContain('audit log')
  })

  it('reports how many packets were recovered', () => {
    const node = {} as RebindResult['node']
    const acc = { status: 'accepted', reason: null }
    expect(recoveredText({ node, recovered: [] })).toContain('No earlier packet')
    expect(recoveredText({ node, recovered: [acc] })).toContain('1 earlier packet was recovered')
    expect(recoveredText({ node, recovered: [acc, acc] })).toContain('2 earlier packets were')
  })
})
