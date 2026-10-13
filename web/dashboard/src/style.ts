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
