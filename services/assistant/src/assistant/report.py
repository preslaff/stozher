"""Situation report: the facts are gathered and written by code; a model, if one is available, only adds a short summary.

Without any engine the report is still complete (engine "none"): it is what the tools return, in plain sentences.
"""

from datetime import UTC, datetime
from typing import Any

from .agent import Answer, ToolUse, compose
from .guard import caveats, freshest_live_age
from .providers import EngineError, Provider, dump
from .tools import ToolResult, Tools

REPORT_SYSTEM = """You write the opening summary of a STOZHER situation report for a duty officer, from the JSON facts you are given and nothing else.
Three to five plain sentences. State the data age. Missing evidence is never an all-clear; do not say anything is safe or clear. \
Expired history is not current. Talk about places and conditions only. Do not add advice beyond checking a second source of position."""


def age(s: int | None) -> str:
    if s is None:
        return "no data"
    if s < 90:
        return f"{s} s ago"
    if s < 5400:
        return f"{round(s / 60)} min ago"
    if s < 172800:
        return f"{s / 3600:.1f} h ago"
    return f"{s / 86400:.1f} days ago"


def sentences(res: dict[str, ToolResult], bbox: str | None, now: datetime) -> list[str]:
    """The deterministic body of the report."""
    out = [f"STOZHER situation report, {now:%Y-%m-%d %H:%M} UTC. Area: {bbox or 'not specified'}."]
    sit = res.get("situation")
    if sit and not sit.error:
        hs = sit.data["hotspots"]
        if hs["active"]:
            kinds = ", ".join(f"{n} {k}" for k, n in hs["by_kind"].items())
            out.append(f"Interference: {hs['active']} active hotspot(s) ({kinds}).")
        else:
            out.append("Interference: no active hotspot.")
        cells = (sit.data["trust"] or {}).get("cells", 0)
        if cells:
            states = ", ".join(f"{n} {k}" for k, n in sit.data["trust"]["by_state"].items())
            out.append(
                f"GNSS trust: {cells} cell(s) with evidence in the last 90 min ({states}); newest {age(sit.data_age_s)}."
            )
        else:
            out.append(
                "GNSS trust: no cell has evidence in the last 90 min. This is not an all-clear: nobody has reported."
            )
    elif bbox is None:
        out.append("Current picture: not requested, because no area was given.")
    else:
        out.append("Current picture: could not be read.")
    ev = res.get("recent_events")
    if ev and not ev.error:
        d = ev.data
        classes = ", ".join(f"class {k}: {n}" for k, n in sorted(d["by_class"].items())) or "none"
        out.append(f"Witness readings, last {d['hours']} h: {d['count']} ({classes}).")
        if d["without_gnss_position"]:
            out.append(
                f"{d['without_gnss_position']} reading(s) arrived without a GNSS position: stored, but they cannot be placed on the map."
            )
        if d["newest_placed_age_s"] is not None:
            out.append(f"Newest reading with a position: {age(d['newest_placed_age_s'])}.")
    hist = res.get("trust_history")
    if hist and not hist.error:
        c = hist.data["cells_by_last_state"]
        if c:
            out.append(
                "Expired history ("
                + hist.data["window"]
                + "): "
                + ", ".join(f"{n} cell(s) last seen {k.lower()}" for k, n in c.items())
                + f", newest {age(hist.data['newest_evidence_age_s'])}. Not the current state."
            )
    hz = res.get("hazards")
    if hz and not hz.error:
        items = hz.data["items"]
        if items:
            tiers: dict[str, int] = {}
            for i in items:
                tiers[i.get("tier", "?")] = tiers.get(i.get("tier", "?"), 0) + 1
            out.append("Road hazards: " + ", ".join(f"{n} {t}" for t, n in tiers.items()) + ".")
        else:
            out.append("Road hazards: none reported. That does not mean the roads are clear.")
    al = res.get("alerts")
    if al and not al.error:
        items = al.data["items"]
        out.append(
            f"Alerts: {len(items)} recent; newest: {items[0]['message']}"
            if items
            else "Alerts: none recent."
        )
    return out


async def report(provider: Provider | None, tools: Tools, bbox: str | None) -> Answer:
    plan: list[tuple[str, dict[str, Any]]] = [
        ("recent_events", {"hours": 24}),
        ("hotspots", {"limit": 5}),
        ("alerts", {"limit": 5}),
        ("hazards", {}),
    ]
    if bbox:
        plan = [("situation", {}), ("trust_history", {"window": "24h"})] + plan
    used: list[tuple[str, ToolResult]] = []
    for name, args in plan:
        used.append((name, await tools.call(name, args)))
    res = {n: r for n, r in used}
    body = sentences(res, bbox, datetime.now(UTC))
    notes = caveats(used)
    sim = any(r.sim for _, r in used)
    engine, model = "none", ""
    summary = ""
    if provider is not None:
        facts = {n: r.data for n, r in used if not r.error}
        try:
            turn = await provider.complete(
                REPORT_SYSTEM, [{"role": "user", "text": dump({"area": bbox, "facts": facts})}], []
            )
            summary, engine, model = turn.text.strip(), provider.name, provider.model
        except EngineError:
            summary = ""  # the report stands without it
    text = "\n".join(body)
    if summary:
        text = summary + "\n\n" + text
    return Answer(
        answer=compose(text, notes, sim),
        engine=engine,
        model=model,
        grounded=True,
        data_age_s=freshest_live_age(used),
        sim=sim,
        sources=sorted({s for _, r in used for s in r.sources}),
        caveats=notes,
        tools=[
            ToolUse(name=n, args=a, data_age_s=r.data_age_s, expired=r.expired, error=r.error)
            for (n, a), (_, r) in zip(plan, used, strict=True)
        ],
    )
