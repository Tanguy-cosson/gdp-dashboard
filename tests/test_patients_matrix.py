"""Tests de build_patients_matrix : le tableau ✅/— utilisé sur
Patient follow-up et Patient Search pour voir d'un coup d'œil quelles
visites sont documentées pour chaque patient."""
import pandas as pd

from business_logic import build_patients_matrix


def test_empty_dataframe_returns_empty_matrix_with_expected_columns():
    df = pd.DataFrame(columns=["usubjid", "site_name", "visit_code", "result_id"])
    matrix = build_patients_matrix(df)
    assert matrix.empty
    assert list(matrix.columns) == ["usubjid", "site_name", "VINC", "V1", "V2"]


def test_patient_with_one_visit_shows_check_only_for_that_visit():
    df = pd.DataFrame([
        {"usubjid": "U1", "site_name": "Site A", "visit_code": "V1", "result_id": 1},
    ])
    matrix = build_patients_matrix(df)
    row = matrix.iloc[0]
    assert row["V1"] == "✅"
    assert row["VINC"] == "—"
    assert row["V2"] == "—"


def test_patient_with_all_visits_shows_all_checks():
    df = pd.DataFrame([
        {"usubjid": "U1", "site_name": "Site A", "visit_code": "VINC", "result_id": 1},
        {"usubjid": "U1", "site_name": "Site A", "visit_code": "V1", "result_id": 2},
        {"usubjid": "U1", "site_name": "Site A", "visit_code": "V2", "result_id": 3},
    ])
    matrix = build_patients_matrix(df)
    row = matrix.iloc[0]
    assert row["VINC"] == row["V1"] == row["V2"] == "✅"


def test_multiple_patients_produce_multiple_rows():
    df = pd.DataFrame([
        {"usubjid": "U1", "site_name": "Site A", "visit_code": "V1", "result_id": 1},
        {"usubjid": "U2", "site_name": "Site B", "visit_code": "V1", "result_id": 2},
    ])
    matrix = build_patients_matrix(df)
    assert len(matrix) == 2
    assert set(matrix["usubjid"]) == {"U1", "U2"}
