import { deliveryText, FRESH_COLOR, freshness, sinceText } from '../style'
import type { NodeInfo } from '../types'

/** Operators only: when each witness was last heard and whether it has been told what arrived. */
export function NodesPanel({ nodes, now }: { nodes: NodeInfo[]; now: number }) {
  if (nodes.length === 0) return null
  return (
    <section>
      <h2>
        Witness nodes <span className="count">{nodes.length}</span>
      </h2>
      <ul className="list">
        {nodes.map((n) => {
          const f = freshness(n.last_seen, now)
          return (
            <li key={n.id} className="nodeitem">
              <span className="dot" style={{ background: FRESH_COLOR[f] }} aria-label={`${f}`} />
              <span className="body">
                <span className="msg">
                  {n.name}
                  {n.sim && <span className="chip sim">SIM</span>}
                </span>
                <span className="meta">
                  {n.status !== 'active' ? `${n.status} · ` : ''}
                  {n.last_seen ? `heard ${sinceText(n.last_seen, now)}` : 'never heard'}
                </span>
                <span className="meta">{deliveryText(n.delivery, now)}</span>
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
