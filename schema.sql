-- =====================================================================
-- BLOOD STUDY LIMS — FINAL DEMONSTRATION SCHEMA
-- Educational GxP-oriented prototype. Production deployment requires
-- formal validation, qualified infrastructure, controlled change control,
-- persistent validated storage, security controls and approved SOPs.
-- =====================================================================

CREATE TABLE IF NOT EXISTS SITES (
    site_id     TEXT PRIMARY KEY,
    site_name   TEXT NOT NULL,
    country     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS PATIENTS (
    patient_id      TEXT PRIMARY KEY,
    usubjid         TEXT NOT NULL UNIQUE,
    site_id         TEXT NOT NULL,
    subjid          TEXT NOT NULL,
    sex             TEXT CHECK (sex IN ('M', 'F')),
    birth_year      INTEGER,
    anonymized      INTEGER NOT NULL DEFAULT 0,
    anonymized_at   TEXT,
    FOREIGN KEY (site_id) REFERENCES SITES(site_id)
);

CREATE TABLE IF NOT EXISTS CONSENT (
    consent_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      TEXT NOT NULL,
    usubjid         TEXT NOT NULL,
    status          TEXT NOT NULL,
    document_ref    TEXT,
    recorded_by     TEXT NOT NULL,
    recorded_at     TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (patient_id) REFERENCES PATIENTS(patient_id)
);

CREATE TABLE IF NOT EXISTS VISITES (
    visit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  TEXT NOT NULL,
    visit_code  TEXT NOT NULL CHECK (visit_code IN ('VINC', 'V1', 'V2')),
    visit_date  TEXT NOT NULL,
    visit_num   INTEGER NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES PATIENTS(patient_id),
    UNIQUE (patient_id, visit_code)
);

CREATE TABLE IF NOT EXISTS SAMPLES (
    sample_id            TEXT PRIMARY KEY,
    patient_id           TEXT NOT NULL,
    visit_id             INTEGER NOT NULL,
    sample_type          TEXT NOT NULL DEFAULT 'SERUM'
                          CHECK (sample_type IN ('WHOLE_BLOOD', 'SERUM', 'PLASMA')),
    collection_datetime  TEXT,
    receipt_datetime     TEXT,
    status               TEXT NOT NULL DEFAULT 'RECEIVED'
                          CHECK (status IN ('COLLECTED', 'RECEIVED', 'ANALYZED', 'ARCHIVED')),
    storage_location     TEXT,
    barcode_value        TEXT UNIQUE,
    label_printed        INTEGER NOT NULL DEFAULT 0,
    label_printed_at     TEXT,
    label_printed_by     TEXT,
    FOREIGN KEY (patient_id) REFERENCES PATIENTS(patient_id),
    FOREIGN KEY (visit_id) REFERENCES VISITES(visit_id),
    UNIQUE (patient_id, visit_id, sample_type)
);

CREATE TABLE IF NOT EXISTS STORAGE_LOCATIONS (
    location_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id            TEXT NOT NULL,
    freezer_id           TEXT DEFAULT 'FREEZER-80C-01',
    rack_id              TEXT NOT NULL,
    box_id               TEXT NOT NULL,
    position_well        TEXT NOT NULL,
    freeze_thaw_cycles   INTEGER DEFAULT 0,
    volume_ul            REAL,
    moved_at             TEXT DEFAULT (datetime('now')),
    moved_by             TEXT,
    FOREIGN KEY (sample_id) REFERENCES SAMPLES(sample_id)
);

CREATE TABLE IF NOT EXISTS IMPORT_BATCHES (
    batch_id             TEXT PRIMARY KEY,
    source_filename      TEXT NOT NULL,
    source_sha256        TEXT NOT NULL,
    received_at          TEXT NOT NULL,
    received_by          TEXT NOT NULL,
    row_count            INTEGER NOT NULL,
    imported_rows        INTEGER NOT NULL DEFAULT 0,
    skipped_rows         INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL CHECK (status IN ('RECEIVED','ACCEPTED','REJECTED')),
    error_message        TEXT
);

CREATE TABLE IF NOT EXISTS LAB_RESULTS (
    result_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id                 INTEGER NOT NULL,
    sample_id                TEXT,
    test_code                TEXT NOT NULL,
    test_name                TEXT NOT NULL,
    result_value             REAL,
    result_unit              TEXT,
    result_date              TEXT NOT NULL,
    ref_low                  REAL,
    ref_high                 REAL,
    critical_low             REAL,
    critical_high            REAL,
    status                   TEXT NOT NULL DEFAULT 'PENDING'
                             CHECK (status IN ('PENDING', 'TECHNICAL_OK', 'REVIEWED')),
    record_status            TEXT NOT NULL DEFAULT 'ACTIVE'
                             CHECK (record_status IN ('ACTIVE', 'SUPERSEDED', 'VOID')),
    supersedes_result_id     INTEGER,
    change_reason            TEXT,
    import_batch_id          TEXT,
    technical_validated_by   TEXT,
    technical_validated_at   TEXT,
    biologist_validated_by   TEXT,
    biologist_validated_at   TEXT,
    signature_reason         TEXT,
    remarks                  TEXT,
    lab_source               TEXT DEFAULT 'Central Lab Results',
    FOREIGN KEY (visit_id) REFERENCES VISITES(visit_id),
    FOREIGN KEY (sample_id) REFERENCES SAMPLES(sample_id),
    FOREIGN KEY (supersedes_result_id) REFERENCES LAB_RESULTS(result_id),
    FOREIGN KEY (import_batch_id) REFERENCES IMPORT_BATCHES(batch_id)
);

CREATE TABLE IF NOT EXISTS EXPORT_PACKAGES (
    package_id           TEXT PRIMARY KEY,
    package_type         TEXT NOT NULL,
    generated_at         TEXT NOT NULL,
    generated_by         TEXT NOT NULL,
    cutoff_date          TEXT NOT NULL,
    row_count            INTEGER NOT NULL,
    csv_sha256           TEXT NOT NULL,
    package_sha256       TEXT NOT NULL,
    filename             TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'GENERATED'
                         CHECK (status IN ('GENERATED','DOWNLOADED','SENT','FAILED')),
    sent_at              TEXT,
    sent_by              TEXT,
    delivery_reference   TEXT
);

CREATE TABLE IF NOT EXISTS E_SIGNATURES (
    signature_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id         INTEGER NOT NULL,
    username          TEXT NOT NULL,
    meaning           TEXT NOT NULL,
    signed_at         TEXT NOT NULL,
    auth_method       TEXT NOT NULL DEFAULT 'PASSWORD_REAUTH',
    signature_hash    TEXT NOT NULL,
    FOREIGN KEY (result_id) REFERENCES LAB_RESULTS(result_id)
);

CREATE TABLE IF NOT EXISTS AUTOMATION_RUNS (
    run_id          TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    triggered_by    TEXT NOT NULL,
    reference_date  TEXT NOT NULL,
    mode            TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    details         TEXT
);

CREATE TABLE IF NOT EXISTS AUDIT_TRAIL (
    audit_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name         TEXT NOT NULL,
    action             TEXT NOT NULL,
    user_name          TEXT NOT NULL,
    event_timestamp    TEXT DEFAULT (datetime('now')),
    record_ref         TEXT,
    comment            TEXT,
    old_value          TEXT,
    new_value          TEXT,
    change_reason      TEXT,
    object_type        TEXT,
    object_id          TEXT
);

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON AUDIT_TRAIL
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_TRAIL is append-only: deletion is not permitted.');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON AUDIT_TRAIL
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_TRAIL is append-only: modification is not permitted.');
END;

CREATE INDEX IF NOT EXISTS idx_lab_results_status ON LAB_RESULTS(status);
CREATE INDEX IF NOT EXISTS idx_lab_results_record_status ON LAB_RESULTS(record_status);
CREATE INDEX IF NOT EXISTS idx_lab_results_import_batch ON LAB_RESULTS(import_batch_id);
CREATE INDEX IF NOT EXISTS idx_lab_results_supersedes ON LAB_RESULTS(supersedes_result_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_timestamp ON AUDIT_TRAIL(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_import_source_sha256 ON IMPORT_BATCHES(source_sha256);
CREATE INDEX IF NOT EXISTS idx_export_packages_period ON EXPORT_PACKAGES(package_type, cutoff_date, status);

CREATE TABLE IF NOT EXISTS USERS (
    username                TEXT PRIMARY KEY,
    full_name               TEXT NOT NULL,
    role                    TEXT NOT NULL CHECK (role IN
                              ('LAB_TECH', 'BIOLOGIST', 'PHYSICIAN', 'CRO', 'SPONSOR')),
    password_hash           TEXT NOT NULL,
    email                   TEXT,
    job_title               TEXT,
    active                  INTEGER NOT NULL DEFAULT 1,
    failed_login_count      INTEGER NOT NULL DEFAULT 0,
    locked_until            TEXT,
    must_change_password    INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS MESSAGES (
    message_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_username    TEXT NOT NULL,
    sender_username       TEXT,
    sender_label          TEXT NOT NULL,
    subject               TEXT NOT NULL,
    body                  TEXT NOT NULL,
    attachment_name       TEXT,
    attachment_data       BLOB,
    attachment_mimetype   TEXT,
    is_read               INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (recipient_username) REFERENCES USERS(username)
);

CREATE INDEX IF NOT EXISTS idx_messages_recipient ON MESSAGES(recipient_username, is_read);

CREATE TABLE IF NOT EXISTS PASSWORD_RESET_TOKENS (
    token           TEXT PRIMARY KEY,
    username        TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL,
    used_at         TEXT,
    FOREIGN KEY (username) REFERENCES USERS(username)
);

CREATE TABLE IF NOT EXISTS REMARKS (
    remark_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id       INTEGER NOT NULL,
    remark_text     TEXT NOT NULL,
    user_name       TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (result_id) REFERENCES LAB_RESULTS(result_id)
);

CREATE TABLE IF NOT EXISTS SETTINGS (
    setting_key     TEXT PRIMARY KEY,
    setting_value   TEXT
);

INSERT OR IGNORE INTO USERS (username, full_name, role, password_hash, email) VALUES
    ('lab_tech1',   'Laboratory Technician', 'LAB_TECH',
     '$2b$12$BPUObptOfVjmafvxxGAoZ.6a4R0fJfo3quDP4jwEeJeWYlb9Z2Rlu', 'labtech1@example.com'),
    ('biologist1',  'Dr. Biologist', 'BIOLOGIST',
     '$2b$12$viRgcn3ACvAapXR6jzP5mey2U5lhbfq.IRwgCENVvzf1L3oEKoTUm', 'biologist1@example.com'),
    ('physician1',  'Dr. Investigator', 'PHYSICIAN',
     '$2b$12$dpBj2k/gbHzt3dVlpFxQU.uO22DS.1lJj6saCwPVBSU17kAQ/yLuO', 'physician1@example.com'),
    ('cro_arc',     'CRO - Clinical Services', 'CRO',
     '$2b$12$KtPxzSk4lhKesScoqnvbL.6pgYPvcqMBXMR26M7cMlJsH.602HSYS', 'cro_arc@example.com'),
    ('sponsor_lph', 'LPH Sponsor', 'SPONSOR',
     '$2b$12$obXTbTSMD8C6V8fIhZOcauTb8T6iBRsd1ZfRlMcntlygOpZnykU/u', 'sponsor_lph@example.com');

INSERT OR IGNORE INTO SETTINGS (setting_key, setting_value) VALUES
    ('company_name', 'Clinical Services'),
    ('logo_base64', ''),
    ('automation_enabled', '1'),
    ('reminder_ingestion_days', '7'),
    ('vinc_reminder_day', '25'),
    ('last_reminder_ingestion_sent', ''),
    ('last_reminder_sponsor_sent', ''),
    ('last_reminder_critical_sent', ''),
    ('last_weekly_reminder_period', ''),
    ('last_critical_alert_signature', ''),
    ('last_vinc_sent_period', ''),
    ('automation_demo_mode', '0'),
    ('automation_demo_date', '2026-06-25'),
    ('automation_demo_last_ingestion_date', '2026-06-18'),
    ('automation_last_run_id', ''),
    ('notify_emails_lab', ''),
    ('notify_emails_sponsor', ''),
    ('notify_emails_critical', ''),
    ('notify_emails_physician', ''),
    ('auto_send_reports_enabled', '0');

CREATE VIEW IF NOT EXISTS V_LAB_RESULTS_FULL AS
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
LEFT JOIN SAMPLES sa ON lr.sample_id = sa.sample_id;
