/** Server-sent events over fetch: EventSource cannot send the X-API-Key header. */

export interface SseEvent {
  id?: string
  event: string
  data: string
}

/** Split a text buffer into complete events; returns the unconsumed rest. Handles CRLF and comments. */
export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const text = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const blocks = text.split('\n\n')
  const rest = blocks.pop() ?? ''
  const events: SseEvent[] = []
  for (const block of blocks) {
    let id: string | undefined
    let event = 'message'
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (line === '' || line.startsWith(':')) continue
      const i = line.indexOf(':')
      const field = i === -1 ? line : line.slice(0, i)
      const value = i === -1 ? '' : line.slice(i + 1).replace(/^ /, '')
      if (field === 'id') id = value
      else if (field === 'event') event = value
      else if (field === 'data') data.push(value)
    }
    if (data.length > 0 || event !== 'message') events.push({ id, event, data: data.join('\n') })
  }
  return { events, rest }
}

export type StreamState = 'connecting' | 'live' | 'reconnecting' | 'denied'

interface Options {
  url: string
  key: string
  onEvent: (e: SseEvent) => void
  onState: (s: StreamState) => void
}

/** Connect and keep reconnecting with backoff; resumes after the last alert id. */
export function connectStream({ url, key, onEvent, onState }: Options): () => void {
  const abort = new AbortController()
  let lastId: string | undefined
  let delay = 1000

  async function loop() {
    while (!abort.signal.aborted) {
      try {
        onState(delay === 1000 && !lastId ? 'connecting' : 'reconnecting')
        const headers: Record<string, string> = { 'X-API-Key': key, Accept: 'text/event-stream' }
        if (lastId) headers['Last-Event-ID'] = lastId
        const r = await fetch(url, { headers, signal: abort.signal })
        if (r.status === 401 || r.status === 403) {
          onState('denied')
          return
        }
        if (!r.ok || !r.body) throw new Error(`stream ${r.status}`)
        onState('live')
        delay = 1000
        const reader = r.body.pipeThrough(new TextDecoderStream()).getReader()
        let buf = ''
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          buf += value
          const { events, rest } = parseSse(buf)
          buf = rest
          for (const e of events) {
            if (e.id) lastId = e.id
            onEvent(e)
          }
        }
      } catch {
        if (abort.signal.aborted) return
      }
      onState('reconnecting')
      await new Promise((res) => setTimeout(res, delay))
      delay = Math.min(delay * 2, 15000)
    }
  }
  void loop()
  return () => abort.abort()
}
