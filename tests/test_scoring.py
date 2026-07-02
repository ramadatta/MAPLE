"""Tests for scoring utilities."""

from utils.scoring import (
    clamp_score,
    compute_consensus_score,
    evidence_boost_from_items,
    marker_overlap_score,
    score_to_label,
)


def test_clamp_score_lower_bound():
    assert clamp_score(-0.5) == 0.0


def test_clamp_score_upper_bound():
    assert clamp_score(1.5) == 1.0


def test_clamp_score_within_range():
    assert clamp_score(0.5) == 0.5


def test_marker_overlap_score():
    score = marker_overlap_score(["COL1A1", "DCN"], ["COL1A1", "COL3A1", "DCN", "LUM"])
    assert 0.0 <= score <= 1.0
    assert score == 0.5


def test_consensus_score_stays_in_range():
    for base in [-1.0, 0.0, 0.5, 1.0, 2.0]:
        for boost in [-0.5, 0.0, 0.5]:
            for adj in [-0.5, 0.0, 0.3]:
                for pen in [0.0, 0.5, 1.0]:
                    result = compute_consensus_score(base, boost, adj, pen)
                    assert 0.0 <= result <= 1.0


def test_score_to_label_mapping():
    assert score_to_label(0.8) == "High"
    assert score_to_label(0.6) == "Medium"
    assert score_to_label(0.3) == "Low"
    assert score_to_label(0.1) == "Insufficient"


def test_evidence_boost_zero_when_no_evidence():
    assert evidence_boost_from_items(0, 0, 0, 0) == 0.0
