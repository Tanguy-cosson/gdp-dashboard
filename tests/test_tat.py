"""Tests de compute_tat_hours : sert au KPI 'temps moyen de traitement'
sur le dashboard CRO. Doit rester correct même avec des données
partielles (champs optionnels selon la source d'import)."""
import pandas as pd

from business_logic import compute_tat_hours


def test_tat_computed_from_complete_timestamps():
    df = pd.DataFrame([{
        "receipt_datetime": "2026-01-01T08:00:00Z",
        "technical_validated_at": "2026-01-01T10:00:00Z",   # +2h
        "biologist_validated_at": "2026-01-01T14:00:00Z",   # +4h après tech
    }])
    tat1, tat2 = compute_tat_hours(df)
    assert tat1 == 2.0
    assert tat2 == 4.0


def test_tat_averages_multiple_rows():
    df = pd.DataFrame([
        {"receipt_datetime": "2026-01-01T08:00:00Z",
         "technical_validated_at": "2026-01-01T10:00:00Z",
         "biologist_validated_at": None},
        {"receipt_datetime": "2026-01-01T08:00:00Z",
         "technical_validated_at": "2026-01-01T12:00:00Z",
         "biologist_validated_at": None},
    ])
    tat1, tat2 = compute_tat_hours(df)
    assert tat1 == 3.0  # moyenne de 2h et 4h
    assert tat2 is None  # aucune ligne n'a de biologist_validated_at


def test_tat_missing_columns_returns_none_none():
    df = pd.DataFrame([{"result_value": 5.0}])  # aucune colonne temporelle
    tat1, tat2 = compute_tat_hours(df)
    assert tat1 is None
    assert tat2 is None


def test_tat_empty_dataframe_with_columns_returns_none():
    df = pd.DataFrame(columns=["receipt_datetime", "technical_validated_at", "biologist_validated_at"])
    tat1, tat2 = compute_tat_hours(df)
    assert tat1 is None
    assert tat2 is None


def test_tat_ignores_rows_with_unparsable_dates():
    df = pd.DataFrame([{
        "receipt_datetime": "not-a-date",
        "technical_validated_at": "2026-01-01T10:00:00Z",
        "biologist_validated_at": None,
    }])
    tat1, tat2 = compute_tat_hours(df)
    assert tat1 is None  # la seule ligne est invalide -> pas de moyenne calculable
