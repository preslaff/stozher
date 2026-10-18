"""Tier 1 of the evaluation set (assistant/evaluation.py): every case with a scripted provider, no model."""

import pytest
from assistant.evaluation import ScriptedProvider, load, run_case

SPEC = load()


@pytest.mark.parametrize("case", SPEC["cases"], ids=[c["id"] for c in SPEC["cases"]])
async def test_case(case: dict) -> None:  # type: ignore[type-arg]
    assert await run_case(SPEC, case, ScriptedProvider(case["script"])) == []


def test_the_set_covers_the_rules_it_exists_for() -> None:
    ids = {c["id"] for c in SPEC["cases"]}
    assert len(ids) == len(SPEC["cases"]) >= 20
    assert {"thin-bad-answer", "empty-all-clear", "expired-overclaim", "sim-labelled"} <= ids
    assert sum(c["id"].startswith("refuse-") for c in SPEC["cases"]) >= 3
