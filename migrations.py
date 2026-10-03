"""Versioned, idempotent database migrations for the BLOOD LIMS demo.

Derived SQLite objects (indexes/views that depend on migrated columns) are
rebuilt only after all schema migrations have completed. This is important for
legacy SQLite databases where the derived objects may still reference an older
schema.
"""
from audit import log_audit
from db import set_setting


def _column_exists(conn, table, column):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _table_exists(conn, table):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _add_column(conn, table, column_def):
    name = column_def.split()[0]
    if not _column_exists(conn, table, name):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def _migration_1_user_management(conn):
    for definition in [
        "active INTEGER NOT NULL DEFAULT 1",
        "failed_login_count INTEGER NOT NULL DEFAULT 0",
        "locked_until TEXT",
        "must_change_password INTEGER NOT NULL DEFAULT 0",
        "created_at TEXT",
    ]:
        _add_column(conn, "USERS", definition)
    conn.execute("UPDATE USERS SET created_at = datetime('now') WHERE created_at IS NULL")
    if not _table_exists(conn, "PASSWORD_RESET_TOKENS"):
        conn.execute("""
            CREATE TABLE PASSWORD_RESET_TOKENS (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL,
                used_at TEXT,
                FOREIGN KEY (username) REFERENCES USERS(username)
            )
        """)


def _migration_2_gdpr_and_hl7(conn):
    _add_column(conn, "PATIENTS", "anonymized INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "PATIENTS", "anonymized_at TEXT")
    if not _table_exists(conn, "CONSENT"):
        conn.execute("""
            CREATE TABLE CONSENT (
                consent_id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT NOT NULL,
                usubjid TEXT NOT NULL,
                status TEXT NOT NULL,
                document_ref TEXT,
                recorded_by TEXT NOT NULL,
                recorded_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (patient_id) REFERENCES PATIENTS(patient_id)
            )
        """)


def _migration_3_internal_mailbox(conn):
    _add_column(conn, "USERS", "job_title TEXT")
    if not _table_exists(conn, "MESSAGES"):
        conn.execute("""
            CREATE TABLE MESSAGES (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_username TEXT NOT NULL,
                sender_username TEXT,
                sender_label TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                attachment_name TEXT,
                attachment_data BLOB,
                attachment_mimetype TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (recipient_username) REFERENCES USERS(username)
            )
        """)


def _migration_4_audit_record_ref(conn):
    _add_column(conn, "AUDIT_TRAIL", "record_ref TEXT")


def _migration_5_gxp_traceability(conn):
    """Add result lineage, field-level audit metadata and automation/export ledgers."""
    if _table_exists(conn, "LAB_RESULTS"):
        for definition in [
            "record_status TEXT NOT NULL DEFAULT 'ACTIVE'",
            "supersedes_result_id INTEGER",
            "change_reason TEXT",
            "import_batch_id TEXT",
        ]:
            _add_column(conn, "LAB_RESULTS", definition)

    for definition in [
        "old_value TEXT",
        "new_value TEXT",
        "change_reason TEXT",
        "object_type TEXT",
        "object_id TEXT",
    ]:
        _add_column(conn, "AUDIT_TRAIL", definition)

    if not _table_exists(conn, "E_SIGNATURES"):
        conn.execute("""
            CREATE TABLE E_SIGNATURES (
                signature_id INTEGER PRIMARY KEY AUTOINCREMENT,
                result_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                meaning TEXT NOT NULL,
                signed_at TEXT NOT NULL,
                auth_method TEXT NOT NULL DEFAULT 'PASSWORD_REAUTH',
                signature_hash TEXT NOT NULL,
                FOREIGN KEY (result_id) REFERENCES LAB_RESULTS(result_id)
            )
        """)

    if not _table_exists(conn, "AUTOMATION_RUNS"):
        conn.execute("""
            CREATE TABLE AUTOMATION_RUNS (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                triggered_by TEXT NOT NULL,
                reference_date TEXT NOT NULL,
                mode TEXT NOT NULL,
                outcome TEXT NOT NULL,
                details TEXT
            )
        """)

    if not _table_exists(conn, "EXPORT_PACKAGES"):
        conn.execute("""
            CREATE TABLE EXPORT_PACKAGES (
                package_id TEXT PRIMARY KEY,
                package_type TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                generated_by TEXT NOT NULL,
                cutoff_date TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                csv_sha256 TEXT NOT NULL,
                package_sha256 TEXT NOT NULL,
                filename TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'GENERATED' CHECK (status IN ('GENERATED','DOWNLOADED','SENT','FAILED')),
                sent_at TEXT,
                sent_by TEXT,
                delivery_reference TEXT
            )
        """)
    else:
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='EXPORT_PACKAGES'"
        ).fetchone()
        sql = (sql_row[0] or "") if sql_row else ""
        if "'SENT'" not in sql:
            conn.execute("ALTER TABLE EXPORT_PACKAGES RENAME TO EXPORT_PACKAGES_OLD")
            conn.execute("""
                CREATE TABLE EXPORT_PACKAGES (
                    package_id TEXT PRIMARY KEY,
                    package_type TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    generated_by TEXT NOT NULL,
                    cutoff_date TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    csv_sha256 TEXT NOT NULL,
                    package_sha256 TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'GENERATED' CHECK (status IN ('GENERATED','DOWNLOADED','SENT','FAILED')),
                    sent_at TEXT,
                    sent_by TEXT,
                    delivery_reference TEXT
                )
            """)
            conn.execute("""
                INSERT INTO EXPORT_PACKAGES (
                    package_id, package_type, generated_at, generated_by, cutoff_date,
                    row_count, csv_sha256, package_sha256, filename, status
                )
                SELECT package_id, package_type, generated_at, generated_by, cutoff_date,
                       row_count, csv_sha256, package_sha256, filename, status
                FROM EXPORT_PACKAGES_OLD
            """)
            conn.execute("DROP TABLE EXPORT_PACKAGES_OLD")

    for key, value in [
        ("automation_demo_mode", "0"),
        ("automation_demo_date", "2026-06-25"),
        ("automation_demo_last_ingestion_date", "2026-06-18"),
        ("automation_last_run_id", ""),
        ("last_vinc_sent_period", ""),
        ("last_weekly_reminder_period", ""),
        ("last_critical_alert_signature", ""),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO SETTINGS(setting_key, setting_value) VALUES (?, ?)",
            (key, value),
        )


def _migration_6_english_demo_labels(conn):
    replacements = {
        ("lab_tech1", "Technicien de laboratoire", "Laboratory Technician"),
        ("biologist1", "Dr. Biologiste", "Dr. Biologist"),
        ("physician1", "Dr. Medecin Investigateur", "Dr. Investigator"),
        ("sponsor_lph", "Promoteur LPH", "LPH Sponsor"),
        ("cro_arc", "ARC - Clinical Services", "CRO - Clinical Services"),
    }
    for username, old_name, new_name in replacements:
        conn.execute(
            "UPDATE USERS SET full_name=? WHERE username=? AND full_name=?",
            (new_name, username, old_name),
        )


def _migration_7_archive_repository(conn):
    if not _table_exists(conn, "ARCHIVE_ENTRIES"):
        conn.execute("""
            CREATE TABLE ARCHIVE_ENTRIES (
                archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
                archive_date TEXT NOT NULL,
                archive_year INTEGER NOT NULL,
                iso_week INTEGER NOT NULL,
                week_key TEXT NOT NULL,
                stakeholder TEXT NOT NULL,
                direction TEXT NOT NULL,
                document_type TEXT NOT NULL,
                filename TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                content BLOB NOT NULL,
                mimetype TEXT,
                related_id TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                description TEXT
            )
        """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_archive_period ON ARCHIVE_ENTRIES(archive_year, iso_week)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_archive_stakeholder ON ARCHIVE_ENTRIES(stakeholder, archive_year, iso_week)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_archive_dedupe ON ARCHIVE_ENTRIES(week_key, stakeholder, filename, sha256)"
    )


MIGRATIONS = [
    (1, "user_management_and_password_reset", _migration_1_user_management),
    (2, "gdpr_and_hl7", _migration_2_gdpr_and_hl7),
    (3, "internal_mailbox", _migration_3_internal_mailbox),
    (4, "audit_record_ref", _migration_4_audit_record_ref),
    (5, "gxp_traceability_and_automation", _migration_5_gxp_traceability),
    (6, "english_demo_labels", _migration_6_english_demo_labels),
    (7, "document_archive_repository", _migration_7_archive_repository),
]


def _rebuild_derived_objects(conn):
    """Rebuild indexes/views that depend on migrated columns after migration."""
    conn.execute("DROP VIEW IF EXISTS V_LAB_RESULTS_FULL")
    if _table_exists(conn, "LAB_RESULTS") and _column_exists(conn, "LAB_RESULTS", "record_status"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lab_results_record_status ON LAB_RESULTS(record_status)"
        )
    conn.execute("""
        CREATE VIEW V_LAB_RESULTS_FULL AS
        SELECT lr.result_id, p.usubjid, p.patient_id, p.sex, p.birth_year,
               s.site_id, s.site_name, s.country,
               v.visit_code, v.visit_num, v.visit_date,
               lr.sample_id, sa.sample_type, sa.collection_datetime, sa.receipt_datetime,
               sa.barcode_value, sa.status AS sample_status,
               lr.test_code, lr.test_name, lr.result_value, lr.result_unit, lr.result_date,
               lr.ref_low, lr.ref_high, lr.critical_low, lr.critical_high,
               lr.status, lr.record_status, lr.supersedes_result_id, lr.change_reason,
               lr.import_batch_id, lr.lab_source,
               lr.technical_validated_by, lr.technical_validated_at,
               lr.biologist_validated_by, lr.biologist_validated_at,
               lr.signature_reason, lr.remarks
        FROM LAB_RESULTS lr
        JOIN VISITES v ON lr.visit_id = v.visit_id
        JOIN PATIENTS p ON v.patient_id = p.patient_id
        JOIN SITES s ON p.site_id = s.site_id
        LEFT JOIN SAMPLES sa ON lr.sample_id = sa.sample_id
    """)
    conn.commit()


def run_migrations(conn):
    # record_ref must exist before any migration writes an audit event.
    _migration_4_audit_record_ref(conn)
    checks = {
        1: lambda: not _column_exists(conn, "USERS", "active"),
        2: lambda: not _column_exists(conn, "PATIENTS", "anonymized"),
        3: lambda: not _column_exists(conn, "USERS", "job_title"),
        5: lambda: not _column_exists(conn, "LAB_RESULTS", "record_status"),
        6: lambda: any(
            r[0] in (
                "Technicien de laboratoire",
                "Dr. Biologiste",
                "Dr. Medecin Investigateur",
                "Promoteur LPH",
            )
            for r in conn.execute(
                "SELECT full_name FROM USERS WHERE username IN ('lab_tech1','biologist1','physician1','sponsor_lph')"
            ).fetchall()
        ),
        7: lambda: not _table_exists(conn, "ARCHIVE_ENTRIES"),
    }

    # v4 is idempotently applied before the loop because later migration audit
    # records rely on record_ref.
    for version, name, fn in MIGRATIONS:
        if version == 4:
            continue
        was_missing = checks.get(version, lambda: True)()
        fn(conn)
        if was_missing:
            log_audit(
                conn,
                "SCHEMA",
                "MIGRATION_APPLIED",
                "system",
                comment=f"v{version}: {name}",
            )
        set_setting(conn, "schema_version", str(version))

    _rebuild_derived_objects(conn)
    set_setting(conn, "schema_version", "7")
