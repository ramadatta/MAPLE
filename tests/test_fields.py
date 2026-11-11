"""Tests for field normalisation and home / near / far placement."""
import pytest

from maples.fields import (
    classify_ring,
    home_from_input,
    normalize_context,
    text_matches_home,
)


def test_normalises_synonyms_and_infers_organ_from_disease():
    f = normalize_context("", "Idiopathic Pulmonary Fibrosis (IPF)", "Homo sapiens")
    assert (f.organ, f.organ_system, f.disease_short, f.disease_group, f.species) == (
        "lung", "respiratory", "IPF", "fibrosis", "Human"
    )
    assert f.label == "Lung · IPF · Human"


def test_mouse_synonyms():
    assert normalize_context(species="Mus musculus").species == "Mouse"
    assert normalize_context(species="murine").species == "Mouse"


def test_longest_organ_synonym_wins():
    assert normalize_context("renal cortical tubules").organ == "kidney"
    assert normalize_context("small airway epithelium").organ == "airway"


def test_empty_values_are_none():
    f = normalize_context("not stated", "N/A", "")
    assert f.is_empty and f.label == "Not stated"


HOME = home_from_input("lung", "idiopathic pulmonary fibrosis", "human")


@pytest.mark.parametrize(
    "tissue,disease,species,ring",
    [
        ("lung", "IPF", "human", "home"),
        ("honeycomb cysts", "IPF", "human", "home"),        # unknown tissue, disease places it
        ("lung", "pulmonary fibrosis", "human", "home"),     # broader name for the same disease
        ("lung", "interstitial lung disease", "human", "near"),
        ("distal lung", "COPD", "human", "near"),            # same organ, other disease
        ("lung", "bleomycin-induced fibrosis", "mouse", "near"),  # same organ, other species
        ("airway", "asthma", "human", "near"),               # same organ system
        ("kidney", "acute kidney injury", "mouse", "far"),
        ("", "pancreatic ductal adenocarcinoma", "human", "far"),  # organ inferred from disease
        ("liver", "liver fibrosis", "human", "far"),         # same disease group, other organ
        ("", "", "human", "unplaced"),
        ("organoids", "", "", "unplaced"),
    ],
)
def test_rings_relative_to_full_home(tissue, disease, species, ring):
    assert classify_ring(normalize_context(tissue, disease, species), HOME) == ring


def test_no_home_means_no_ring():
    assert home_from_input(None, "", None) is None
    assert classify_ring(normalize_context("lung", "IPF", "human"), None) is None


def test_organ_only_home():
    home = home_from_input("lung", None, None)
    assert classify_ring(normalize_context("lung", "COPD", "mouse"), home) == "home"
    assert classify_ring(normalize_context("trachea", "", ""), home) == "near"
    assert classify_ring(normalize_context("kidney", "", ""), home) == "far"


def test_disease_only_home_covers_the_disease_in_any_organ():
    home = home_from_input(None, "fibrosis", None)
    assert classify_ring(normalize_context("liver", "liver fibrosis", ""), home) == "home"
    assert classify_ring(normalize_context("skin", "systemic sclerosis", ""), home) == "near"
    assert classify_ring(normalize_context("colon", "colorectal cancer", ""), home) == "far"


def test_species_only_home():
    home = home_from_input(None, None, "Human")
    assert classify_ring(normalize_context("lung", "", "human"), home) == "home"
    assert classify_ring(normalize_context("lung", "", "mouse"), home) == "near"
    assert classify_ring(normalize_context("lung", "", ""), home) == "unplaced"


def test_text_matches_home():
    assert text_matches_home("Single-cell atlas of the human lung in IPF", HOME)
    assert text_matches_home("Aberrant basaloid cells in idiopathic pulmonary fibrosis", HOME)
    assert not text_matches_home("Failed repair in the injured kidney", HOME)
