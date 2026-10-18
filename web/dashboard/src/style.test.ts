import { describe, expect, it } from 'vitest'
import { parseSse } from './sse'
import { ageText, freshness, historyText, quietNote, TRUST_COLOR } from './style'

describe('parseSse', () => {
  it('parses CRLF events and keeps a partial tail', () => {
    const { events, rest } = parseSse(
      'event: hello\r\ndata: {"a":1}\r\n\r\nid: 7\r\nevent: alert\r\ndata: x\r\n\r\nevent: ref',
    )
    expect(events).toEqual([
      { id: undefined, event: 'hello', data: '{"a":1}' },
      { id: '7', event: 'alert', data: 'x' },
    ])
    expect(rest).toBe('event: ref')
  })

  it('ignores keep-alive comments and joins multi-line data', () => {
    const { events } = parseSse(': ping\n\ndata: a\ndata: b\n\n')
    expect(events).toEqual([{ id: undefined, event: 'message', data: 'a\nb' }])
  })

  it('works when a chunk boundary splits an event', () => {
    const first = parseSse('event: alert\ndata: {"id"')
    expect(first.events).toEqual([])
    const second = parseSse(first.rest + ':1}\n\n')
    expect(second.events[0].data).toBe('{"id":1}')
  })
})

describe('style helpers', () => {
  it('formats data age', () => {
    expect(ageText(null)).toBe('no data')
    expect(ageText(30)).toBe('30 s ago')
    expect(ageText(600)).toBe('10 min ago')
    expect(ageText(7200)).toBe('2.0 h ago')
  })

  it('has a colour for every trust state', () => {
    expect(Object.keys(TRUST_COLOR).sort()).toEqual([
      'DEGRADED',
      'JAMMED',
      'SPOOFED',
      'TRUSTED',
      'UNKNOWN',
    ])
  })
})

describe('empty map explanations', () => {
  it('says nothing while cells are shown or the grid is not loaded', () => {
    expect(quietNote(null, 100)).toBeNull()
    expect(quietNote(3, 100)).toBeNull()
  })
  it('tells how old the newest reading is and that empty is not safe', () => {
    const t = quietNote(0, 7200)
    expect(t).toContain('last 90 min')
    expect(t).toContain('Newest 2.0 h ago')
    expect(t).toContain('does not mean safe')
    expect(quietNote(0, null)).toContain('No reading with a position has been received yet')
    const live = quietNote(0, 7200, 96)
    expect(live).toContain('Readings are arriving (newest 2 min ago)')
    expect(live).toContain('without a GNSS fix')
    expect(quietNote(0, 7200, 20000)).not.toContain('arriving')
  })
  it('grades node freshness from last_seen', () => {
    const now = Date.parse('2026-10-18T12:00:00Z')
    expect(freshness(null, now)).toBe('never')
    expect(freshness('2026-10-18T11:30:00Z', now)).toBe('fresh')
    expect(freshness('2026-10-18T06:00:00Z', now)).toBe('stale')
    expect(freshness('2026-10-15T12:00:00Z', now)).toBe('silent')
  })
  it('labels history as expired', () => {
    const t = historyText({ last_state: 'JAMMED', data_age_s: 10800, event_count: 1 })
    expect(t).toContain('Expired')
    expect(t).toContain('3.0 h ago')
    expect(t).toContain('(1 report)')
    expect(t).toContain('Not the current state')
  })
})
