import type { AlertLevel, TrustState } from './types'

/** Trust colours (also used by the legend). Colour-blind safe pairs differ in lightness too. */
export const TRUST_COLOR: Record<TrustState, string> = {
  TRUSTED: '#2f9e6e',
  DEGRADED: '#e0a526',
  JAMMED: '#d6453d',
  SPOOFED: '#a64ac9',
  UNKNOWN: '#6b7280',
}

export const LEVEL_COLOR: Record<AlertLevel, string> = {
  info: '#5b8def',
  warning: '#e0a526',
  critical: '#d6453d',
}

export function ageText(s: number | null | undefined): string {
  if (s === null || s === undefined) return 'no data'
  if (s < 90) return `${s} s ago`
  if (s < 5400) return `${Math.round(s / 60)} min ago`
  return `${(s / 3600).toFixed(1)} h ago`
}

export function sinceText(iso: string, now: number = Date.now()): string {
  return ageText(Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000)))
}

/** The grid is only requested when zoomed in enough that the cell count stays small. */
export const MIN_GRID_ZOOM = 9

/** The live grid only holds this much evidence (docs/trust.md). Older evidence is history, not trust. */
export const GRID_WINDOW_MIN = 90

/** Why the map looks empty. null while the grid is not loaded or has cells. An empty map never means safe. */
export function quietNote(
  gridCells: number | null,
  newestEvidenceAgeS: number | null | undefined,
): string | null {
  if (gridCells !== 0) return null
  if (newestEvidenceAgeS === null || newestEvidenceAgeS === undefined)
    return 'No evidence has been received yet. Uncoloured does not mean safe.'
  return `No evidence in the last ${GRID_WINDOW_MIN} min. Newest reading ${ageText(newestEvidenceAgeS)}. Uncoloured does not mean safe.`
}

export type Freshness = 'fresh' | 'stale' | 'silent' | 'never'
const FRESH_MAX_S = 5400 // a healthy node sends a heartbeat every hour
const STALE_MAX_S = 86400

export function freshness(lastSeen: string | null, now: number = Date.now()): Freshness {
  if (!lastSeen) return 'never'
  const age = Math.max(0, (now - new Date(lastSeen).getTime()) / 1000)
  return age <= FRESH_MAX_S ? 'fresh' : age <= STALE_MAX_S ? 'stale' : 'silent'
}

export const FRESH_COLOR: Record<Freshness, string> = {
  fresh: '#2f9e6e',
  stale: '#e0a526',
  silent: '#6b7280',
  never: '#6b7280',
}

/** Text for an expired history cell. Always says it is not current. */
export function historyText(p: {
  last_state: string
  data_age_s: number
  event_count: number
}): string {
  return `Expired: last evidence ${p.last_state.toLowerCase()}, ${ageText(p.data_age_s)} (${p.event_count} report${p.event_count === 1 ? '' : 's'}). Not the current state.`
}
