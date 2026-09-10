"""Tests de validate_ingestion_dataframe : c'est le garde-fou qui
empêche d'écrire des données incohérentes en base au moment de
l'ingestion CSV (tout-ou-rien). Le comportement attendu est de
lister TOUTES les erreurs trouvées, jamais de s'arrêter à la
première ni de planter avec une exception non gérée."""
import pandas as pd

from business_logic import validate_ingestion_dataframe

REQUIRED_ROW = {
    "site_id": "FR-001", "patient_id": "P1", "usubjid": "U1", "subjid": "001",
    "sex": "M", "birth_year": 1980, "visit_code": "V1", "visit_date": "2026-01-01",
    "visit_num": 2, "test_code": "HBA1C", "test_name": "A1c",
    "result_value": 5.5, "result_unit": "%", "result_date": "2026-01-01",
}


def test_valid_row_passes():
    df = pd.DataFrame([REQUIRED_ROW])
    ok, errors = validate_ingestion_dataframe(df)
    assert ok is True
    assert errors == []


def test_missing_required_column_is_reported():
    df = pd.DataFrame([{k: v for k, v in REQUIRED_ROW.items() if k != "sex"}])
    ok, errors = validate_ingestion_dataframe(df)
    assert ok is False
    assert any("sex" in e for e in errors)


def test_invalid_sex_is_reported():
    row = dict(REQUIRED_ROW, sex="X")
    df = pd.DataFrame([row])
    ok, errors = validate_ingestion_dataframe(df)
    assert ok is False
    assert any("sex" in e for e in errors)


def test_invalid_visit_code_is_reported():
    row = dict(REQUIRED_ROW, visit_code="V9")
    df = pd.DataFrame([row])
    ok, errors = validate_ingestion_dataframe(df)
    assert ok is False
    assert any("visit_code" in e for e in errors)


def test_non_numeric_result_value_is_reported():
    row = dict(REQUIRED_ROW, result_value="abnormal")
    df = pd.DataFrame([row])
    ok, errors = validate_ingestion_dataframe(df)
    assert ok is False
    assert any("result_value" in e for e in errors)


def test_non_integer_birth_year_is_reported():
    row = dict(REQUIRED_ROW, birth_year="nineteen-eighty")
    df = pd.DataFrame([row])
    ok, errors = validate_ingestion_dataframe(df)
    assert ok is False
    assert any("birth_year" in e for e in errors)


def test_empty_required_field_is_reported():
    row = dict(REQUIRED_ROW, test_code="")
    df = pd.DataFrame([row])
    ok, errors = validate_ingestion_dataframe(df)
    assert ok is False
    assert any("test_code" in e for e in errors)


def test_all_errors_in_one_row_are_collected_not_just_the_first():
    row = dict(REQUIRED_ROW, sex="X", visit_code="V9", result_value="bad")
    df = pd.DataFrame([row])
    ok, errors = validate_ingestion_dataframe(df)
    assert ok is False
    assert len(errors) >= 3


def test_multiple_rows_report_correct_line_numbers():
    good_row = dict(REQUIRED_ROW)
    bad_row = dict(REQUIRED_ROW, sex="X")
    df = pd.DataFrame([good_row, bad_row])
    ok, errors = validate_ingestion_dataframe(df)
    assert ok is False
    # bad_row est en position 1 (0-based) -> ligne 3 dans le fichier (1 header + 1-based + 1)
    assert any("Ligne 3" in e for e in errors)
