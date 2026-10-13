import pytest
from stozher_core.trust import (
    Evidence,
    TrustState,
    adverse_state,
    event_weight,
    is_support,
    score_evidence,
)

J, S, D = TrustState.JAMMED, TrustState.SPOOFED, TrustState.DEGRADED


def ev(node: int, state: TrustState | None, w: float = 1.0) -> Evidence:
    return Evidence(node, state, w)


def test_no_evidence_is_unknown_without_a_score() -> None:
    r = score_evidence([])
    assert (r.state, r.score, r.confidence, r.independent_sources) == (
        TrustState.UNKNOWN,
        None,
        0.0,
        0,
    )


def test_support_only_is_trusted() -> None:
    r = score_evidence([ev(1, None, 1.0)])
    assert r.state is TrustState.TRUSTED and r.score == 100 and r.independent_sources == 1


def test_weak_support_scores_lower_than_strong() -> None:
    assert score_evidence([ev(1, None, 0.1)]).score < score_evidence([ev(1, None, 1.0)]).score  # type: ignore[operator]


def test_adverse_only_takes_the_adverse_state_with_a_low_score() -> None:
    r = score_evidence([ev(1, J, 0.9), ev(2, J, 0.8)])
    assert r.state is J and r.score == 0 and r.independent_sources == 2


def test_adverse_outweighing_support_still_wins() -> None:
    r = score_evidence([ev(1, S, 0.9), ev(2, None, 0.3)])
    assert r.state is S and r.score is not None and r.score <= 30


def test_contested_evidence_is_degraded() -> None:
    r = score_evidence([ev(1, J, 0.2), ev(2, None, 0.9)])
    assert r.state is D and r.score is not None and 36 <= r.score <= 63


def test_jamming_beats_spoofing_on_equal_weight() -> None:
    assert score_evidence([ev(1, J, 0.5), ev(2, S, 0.5)]).state is J


def test_more_independent_nodes_raise_confidence() -> None:
    one = score_evidence([ev(1, J, 0.6), ev(1, J, 0.6)])
    three = score_evidence([ev(1, J, 0.6), ev(2, J, 0.6), ev(3, J, 0.6)])
    assert three.confidence > one.confidence and three.independent_sources == 3


def test_event_weight_decays_with_age_and_grows_with_confidence() -> None:
    assert event_weight(31, 0) == pytest.approx(1.0)
    assert event_weight(31, 1800) == pytest.approx(0.5)
    assert event_weight(0, 0) == pytest.approx(0.25)
    assert event_weight(20, 600) > event_weight(20, 3600)


def test_class_mapping() -> None:
    assert adverse_state(1) is J and adverse_state(2) is S and adverse_state(5) is D
    assert adverse_state(0) is None and adverse_state(4) is None and adverse_state(7) is None
    assert is_support(0) and not is_support(1)
