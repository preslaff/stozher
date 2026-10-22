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
  newestPlacedAgeS: number | null | undefined,
  newestUnplacedAgeS: number | null | undefined = null,
): string | null {
  if (gridCells !== 0) return null
  const unplaced =
    newestUnplacedAgeS !== null &&
    newestUnplacedAgeS !== undefined &&
    newestUnplacedAgeS * 1 <= GRID_WINDOW_MIN * 60 &&
    (newestPlacedAgeS === null ||
      newestPlacedAgeS === undefined ||
      newestUnplacedAgeS < newestPlacedAgeS)
  const placed =
    newestPlacedAgeS === null || newestPlacedAgeS === undefined
      ? 'No reading with a position has been received yet.'
      : `No reading with a position in the last ${GRID_WINDOW_MIN} min. Newest ${ageText(newestPlacedAgeS)}.`
  const extra = unplaced
    ? ` Readings are arriving (newest ${ageText(newestUnplacedAgeS)}) but without a GNSS fix, so they cannot be placed.`
    : ''
  return `${placed}${extra} Uncoloured does not mean safe.`
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

/** One line on what the cloud holds of a node's records and whether the node was told (it can then stop retransmitting). */
export function deliveryText(
  d:
    | {
        records: number
        missing: number
        last_ack_at: string | null
        ack_current: boolean | null
        acked_through?: number | null
        unacked?: number
      }
    | null
    | undefined,
  now: number = Date.now(),
): string {
  if (!d) return 'No records yet'
  const held = `${d.records} record${d.records === 1 ? '' : 's'} held${d.missing > 0 ? `, ${d.missing} missing` : ''}`
  if (d.ack_current === null) return `${held}. Never acknowledged.`
  const when = d.last_ack_at ? ` (last ack ${sinceText(d.last_ack_at, now)})` : ''
  // an ack signed before old gaps were given up can be pinned behind one (docs/control.md)
  if (d.ack_current && d.unacked && d.acked_through != null)
    return `${held}. Ack stuck at seq ${d.acked_through} behind a gap: ${d.unacked} not acknowledged${when}.`
  return d.ack_current
    ? `${held}. Node told${when}.`
    : `${held}. New records not yet acknowledged${when}.`
}

/** Label and tooltip of a module chip. With more than one mesh bridge (a gateway per LoRa band), say how many are up. */
export function moduleChip(
  name: string,
  state: 'present' | 'absent',
  bridges?: Record<string, 'present' | 'absent'>,
): { label: string; title: string } {
  const label = name.replace('_', ' ')
  const list = name === 'mesh_bridge' && bridges ? Object.entries(bridges) : []
  if (list.length < 2) return { label, title: `${name}: ${state}` }
  const up = list.filter(([, s]) => s === 'present').length
  return {
    label: `${label} ${up}/${list.length}`,
    title: list.map(([b, s]) => `${b}: ${s}`).join(', '),
  }
}
