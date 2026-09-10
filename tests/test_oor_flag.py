"""Tests de compute_oor_flag : c'est la fonction qui décide si une
valeur est affichée comme Normal / Outlier / CRITICAL partout dans
l'application (dashboard, PDF, bannière d'alerte). Une régression ici
serait silencieuse mais grave (un biologiste pourrait rater une
valeur critique)."""
import pandas as pd

from business_logic import compute_oor_flag
from constants import CRITICAL_FLAG, NORMAL_FLAG, OOR_FLAG


def _row(value, ref_low=None, ref_high=None, crit_low=None, crit_high=None):
    return {
        "result_value": value,
        "ref_low": ref_low, "ref_high": ref_high,
        "critical_low": crit_low, "critical_high": crit_high,
    }


def test_value_within_range_is_normal():
    df = pd.DataFrame([_row(5.0, ref_low=3.9, ref_high=5.6)])
    result = compute_oor_flag(df)
    assert result.iloc[0]["Alerte"] == NORMAL_FLAG


def test_value_above_ref_range_is_outlier():
    df = pd.DataFrame([_row(9.5, ref_low=4.0, ref_high=6.0)])
    result = compute_oor_flag(df)
    assert result.iloc[0]["Alerte"] == OOR_FLAG


def test_value_below_ref_range_is_outlier():
    df = pd.DataFrame([_row(2.0, ref_low=4.0, ref_high=6.0)])
    result = compute_oor_flag(df)
    assert result.iloc[0]["Alerte"] == OOR_FLAG


def test_critical_value_overrides_outlier():
    """Une valeur peut être hors-norme ET critique : le flag CRITICAL
    doit toujours l'emporter sur OOR, jamais l'inverse."""
    df = pd.DataFrame([_row(15.0, ref_low=4.0, ref_high=6.0, crit_high=12.0)])
    result = compute_oor_flag(df)
    assert result.iloc[0]["Alerte"] == CRITICAL_FLAG


def test_critical_low_detected():
    df = pd.DataFrame([_row(1.0, ref_low=4.0, ref_high=6.0, crit_low=2.0)])
    result = compute_oor_flag(df)
    assert result.iloc[0]["Alerte"] == CRITICAL_FLAG


def test_missing_value_gives_no_flag():
    df = pd.DataFrame([_row(None, ref_low=4.0, ref_high=6.0)])
    result = compute_oor_flag(df)
    assert result.iloc[0]["Alerte"] == ""


def test_missing_reference_range_gives_no_flag():
    """Sans ref_low/ref_high (colonnes optionnelles selon la source
    d'import), on ne doit ni planter ni inventer un statut."""
    df = pd.DataFrame([_row(5.0)])
    result = compute_oor_flag(df)
    assert result.iloc[0]["Alerte"] == ""


def test_does_not_mutate_input_dataframe():
    """compute_oor_flag doit renvoyer une copie, pas modifier le
    DataFrame du code appelant sous le tapis."""
    df = pd.DataFrame([_row(5.0, ref_low=3.9, ref_high=5.6)])
    original_columns = list(df.columns)
    compute_oor_flag(df)
    assert list(df.columns) == original_columns
