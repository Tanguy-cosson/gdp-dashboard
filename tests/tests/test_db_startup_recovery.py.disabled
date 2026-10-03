import sqlite3
from pathlib import Path

import db


def test_initialize_schema_upgrades_legacy_lab_results_before_deferred_index_and_view(tmp_path, monkeypatch):
    """Regression test for the Streamlit startup failure seen on an older DB.

    The legacy LAB_RESULTS table deliberately lacks V12 columns such as
    record_status/import_batch_id/supersedes_result_id. The current schema
    contains indexes and a view using those columns. Startup must therefore
    apply migrations before creating those deferred objects.
    """
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE SITES (site_id TEXT PRIMARY KEY, site_name TEXT NOT NULL, country TEXT NOT NULL);
        CREATE TABLE PATIENTS (
            patient_id TEXT PRIMARY KEY, usubjid TEXT NOT NULL UNIQUE, site_id TEXT NOT NULL,
            subjid TEXT NOT NULL, sex TEXT, birth_year INTEGER
        );
        CREATE TABLE VISITES (
            visit_id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT NOT NULL,
            visit_code TEXT NOT NULL, visit_date TEXT NOT NULL, visit_num INTEGER NOT NULL
        );
        CREATE TABLE SAMPLES (
            sample_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, visit_id INTEGER NOT NULL,
            sample_type TEXT NOT NULL, collection_datetime TEXT, receipt_datetime TEXT,
            status TEXT NOT NULL, storage_location TEXT, barcode_value TEXT,
            label_printed INTEGER NOT NULL DEFAULT 0, label_printed_at TEXT, label_printed_by TEXT
        );
        CREATE TABLE IMPORT_BATCHES (
            batch_id TEXT PRIMARY KEY, source_filename TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            received_at TEXT NOT NULL, received_by TEXT NOT NULL, row_count INTEGER NOT NULL,
            imported_rows INTEGER NOT NULL DEFAULT 0, skipped_rows INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL, error_message TEXT
        );
        CREATE TABLE LAB_RESULTS (
            result_id INTEGER PRIMARY KEY AUTOINCREMENT, visit_id INTEGER NOT NULL,
            sample_id TEXT, test_code TEXT NOT NULL, test_name TEXT NOT NULL, result_value REAL,
            result_unit TEXT, result_date TEXT NOT NULL, ref_low REAL, ref_high REAL,
            critical_low REAL, critical_high REAL, status TEXT NOT NULL DEFAULT 'PENDING',
            technical_validated_by TEXT, technical_validated_at TEXT,
            biologist_validated_by TEXT, biologist_validated_at TEXT,
            signature_reason TEXT, remarks TEXT, lab_source TEXT DEFAULT 'Central Lab Results'
        );
        CREATE TABLE USERS (
            username TEXT PRIMARY KEY, full_name TEXT NOT NULL, role TEXT NOT NULL,
            password_hash TEXT NOT NULL, email TEXT
        );
        CREATE TABLE AUDIT_TRAIL (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL,
            action TEXT NOT NULL, user_name TEXT NOT NULL,
            event_timestamp TEXT DEFAULT (datetime('now')), comment TEXT
        );
        CREATE TABLE SETTINGS (setting_key TEXT PRIMARY KEY, setting_value TEXT);
        INSERT INTO USERS VALUES ('legacy_user', 'Legacy User', 'CRO', 'hash', 'legacy@example.com');
        INSERT INTO SITES VALUES ('FR-001', 'Legacy Site', 'FR');
        INSERT INTO PATIENTS VALUES ('P-1', 'BLOOD-001', 'FR-001', '001', 'F', 1970);
        INSERT INTO VISITES VALUES (1, 'P-1', 'VINC', '2026-06-01', 1);
        INSERT INTO LAB_RESULTS (visit_id, test_code, test_name, result_value, result_unit, result_date, ref_low, ref_high, critical_low, critical_high, status)
        VALUES (1, 'HBA1C', 'Hemoglobin A1c', 5.8, '%', '2026-06-01', 4.0, 6.0, NULL, 12.0, 'PENDING');
        """
    )
    conn.commit()

    monkeypatch.setattr(db, "SCHEMA_PATH", str(Path(__file__).resolve().parents[1] / "schema.sql"))
    db._initialize_schema(conn)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(LAB_RESULTS)").fetchall()}
    assert {"record_status", "supersedes_result_id", "change_reason", "import_batch_id"}.issubset(columns)

    index_names = {row[1] for row in conn.execute("PRAGMA index_list(LAB_RESULTS)").fetchall()}
    assert "idx_lab_results_record_status" in index_names
    assert "idx_lab_results_import_batch" in index_names
    assert "idx_lab_results_supersedes" in index_names

    view_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='V_LAB_RESULTS_FULL'"
    ).fetchone()[0]
    assert "record_status" in view_sql

    result = conn.execute("SELECT result_id, record_status FROM LAB_RESULTS").fetchone()
    assert result == (1, "ACTIVE")
