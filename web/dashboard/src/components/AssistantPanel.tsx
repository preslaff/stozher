import { useState } from 'react'
import type { Api, Bbox } from '../api'
import { ApiError } from '../api'
import { ageText } from '../style'
import type { AssistantAnswer } from '../types'

const bboxText = (b: Bbox | null) => (b ? b.map((n) => n.toFixed(4)).join(',') : undefined)

/** Ask about the situation or write a report. The answer is labelled by the server: data age, sources, SIM, caveats. */
export function AssistantPanel({ api, bbox }: { api: Api; bbox: Bbox | null }) {
  const [open, setOpen] = useState(false)
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [answer, setAnswer] = useState<AssistantAnswer | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async (mode: 'ask' | 'report') => {
    setBusy(true)
    setError(null)
    try {
      setAnswer(await api.assistant({ mode, question, bbox: bboxText(bbox) }))
    } catch (e) {
      setAnswer(null)
      setError(
        e instanceof ApiError && e.status === 503
          ? 'The assistant has no engine available right now. Reports still work.'
          : 'The assistant could not answer.',
      )
    } finally {
      setBusy(false)
    }
  }

  if (!open)
    return (
      <button className="assistantopen" onClick={() => setOpen(true)}>
        Assistant
      </button>
    )
  return (
    <div className="assistant" role="dialog" aria-label="Assistant">
      <div className="assistanthead">
        <strong>Assistant</strong>
        <button className="link" onClick={() => setOpen(false)}>
          Close
        </button>
      </div>
      <p className="muted small">
        Answers come from STOZHER data only, with its age. It never raises alerts. Area: the current
        map view.
      </p>
      <textarea
        aria-label="Question"
        rows={3}
        maxLength={600}
        placeholder="Can I trust GNSS on the coastal road?"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
      />
      <div className="assistantbtns">
        <button disabled={busy || !question.trim()} onClick={() => void run('ask')}>
          Ask
        </button>
        <button disabled={busy || !bbox} onClick={() => void run('report')}>
          Situation report
        </button>
      </div>
      {busy && <p className="muted small">Working…</p>}
      {error && <p className="note">{error}</p>}
      {answer && (
        <div className="assistantanswer">
          <div className="assistantmeta">
            {answer.sim && <span className="chip sim">SIM</span>}
            <span className="chip">
              {answer.data_age_s === null
                ? 'no recent evidence'
                : `evidence ${ageText(answer.data_age_s)}`}
            </span>
            <span className="chip">
              {answer.engine === 'none' ? 'no model: facts only' : answer.engine}
            </span>
          </div>
          <p className="answertext">{answer.answer}</p>
          <p className="muted small">
            Sources: {answer.sources.length ? answer.sources.join(', ') : 'none'}. Used:{' '}
            {answer.tools
              .map((t) => t.name + (t.error ? ' (failed)' : t.expired ? ' (expired)' : ''))
              .join(', ') || 'nothing'}
            .
          </p>
        </div>
      )}
    </div>
  )
}
