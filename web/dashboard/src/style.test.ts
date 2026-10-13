import { describe, expect, it } from 'vitest'
import { parseSse } from './sse'
import { ageText, TRUST_COLOR } from './style'

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
