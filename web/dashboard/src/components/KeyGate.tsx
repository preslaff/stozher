import { useState } from 'react'
import { API_URL } from '../api'

export function KeyGate({ onKey, error }: { onKey: (k: string) => void; error: string | null }) {
  const [value, setValue] = useState('')
  return (
    <main className="gate">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (value.trim()) onKey(value.trim())
        }}
      >
        <h1>STOZHER</h1>
        <p className="muted">Situational awareness for degraded environments · GNSS Trust</p>
        <label htmlFor="key">API key</label>
        <input
          id="key"
          type="password"
          autoComplete="off"
          placeholder="stz_live_…"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={!value.trim()}>
          Connect
        </button>
        <p className="muted small">
          Server: {API_URL}. The key is kept for this browser tab only. A responder key shows
          hotspots and alerts; an operator key also shows nodes.
        </p>
      </form>
    </main>
  )
}
