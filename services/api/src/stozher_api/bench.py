"""The bench (docs/trust.md "The bench is not the field"): one SQL test shared by every reader of evidence."""

from datetime import timedelta

from sqlalchemy import ColumnElement, exists, select

from .models import Event, NodeBenchPeriod

BENCH_DEFAULT = timedelta(hours=24)  # without an end time the node is back in the field after this
BENCH_MAX = timedelta(days=7)  # a forgotten switch never silences a witness for long


def on_bench() -> ColumnElement[bool]:
    """True for an event made while its node was on the bench (by observed time, not arrival)."""
    return exists(
        select(NodeBenchPeriod.id).where(
            NodeBenchPeriod.node_id == Event.node_id,
            NodeBenchPeriod.started_at <= Event.observed_at,
            NodeBenchPeriod.ends_at > Event.observed_at,
        )
    )
