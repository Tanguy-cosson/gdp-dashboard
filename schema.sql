CREATE TABLE IF NOT EXISTS SITES (
    site_id     TEXT PRIMARY KEY,
    site_name   TEXT NOT NULL,
    country     TEXT NOT NULL
);
 
CREATE TABLE IF NOT EXISTS PATIENTS (
    patient_id  TEXT PRIMARY KEY,
    usubjid     TEXT NOT NULL UNIQUE,
    site_id     TEXT NOT NULL,
    subjid      TEXT NOT NULL,
    sex         TEXT CHECK (sex IN ('M', 'F')),
    birth_year  INTEGER,
    FOREIGN KEY (site_id) REFERENCES SITES(site_id)
);
 
CREATE TABLE IF NOT EXISTS VISITES (
    visit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      TEXT NOT NULL,
    visit_code      TEXT NOT NULL CHECK (visit_code IN ('VINC', 'V1', 'V2')),
    visit_date      TEXT NOT NULL,
    visit_num       INTEGER NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES PATIENTS(patient_id),
    UNIQUE (patient_id, visit_code)
);
 
CREATE TABLE IF NOT EXISTS LAB_RESULTS (
    result_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id        INTEGER NOT NULL,
    test_code       TEXT NOT NULL,
    test_name       TEXT NOT NULL,
    result_value    REAL,
    result_unit     TEXT,
    result_date     TEXT NOT NULL,
    ref_low         REAL,
    ref_high        REAL,
    status          TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'REVIEWED')),
    lab_source      TEXT DEFAULT 'Central Lab Results',
    FOREIGN KEY (visit_id) REFERENCES VISITES(visit_id)
);
 
CREATE TABLE IF NOT EXISTS AUDIT_TRAIL (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name      TEXT NOT NULL,
    action          TEXT NOT NULL,
    user_name       TEXT NOT NULL,
    event_timestamp TEXT DEFAULT (datetime('now')),
    comment         TEXT
);
 
CREATE TABLE IF NOT EXISTS USERS (
    username        TEXT PRIMARY KEY,
    full_name       TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('CENTRAL_LAB', 'CRO', 'SPONSOR')),
    password_hash   TEXT NOT NULL
);
 
CREATE TABLE IF NOT EXISTS REMARKS (
    remark_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id    INTEGER NOT NULL,
    remark_text  TEXT NOT NULL,
    user_name    TEXT NOT NULL,
    created_at   TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (result_id) REFERENCES LAB_RESULTS(result_id)
);
 
-- NOUVELLE TABLE : parametres d'apparence (logo, nom d'entreprise)
CREATE TABLE IF NOT EXISTS SETTINGS (
    setting_key    TEXT PRIMARY KEY,
    setting_value  TEXT
);
 
-- Comptes de demonstration. Mots de passe en clair UNIQUEMENT pour cet exercice
-- pedagogique (a communiquer separement, jamais dans le code en production reelle) :
--   labcentral  / labcentral2026
--   cro_arc     / cro2026
--   sponsor_lph / sponsor2026
-- Les hash ci-dessous sont generes avec bcrypt (voir generate_password_hash.py
-- pour creer vos propres comptes avec vos propres mots de passe).
INSERT OR IGNORE INTO USERS (username, full_name, role, password_hash) VALUES
    ('labcentral', 'Central Lab Results', 'CENTRAL_LAB', '$2b$12$mgcfU1GcDMWbAlyxNjDYdOnahPMoyl.3DNOqrH9hRXH2u/yXIAqBu'),
    ('cro_arc',    'ARC - Clinical Services', 'CRO', '$2b$12$KtPxzSk4lhKesScoqnvbL.6pgYPvcqMBXMR26M7cMlJsH.602HSYS'),
    ('sponsor_lph','Promoteur LPH', 'SPONSOR', '$2b$12$obXTbTSMD8C6V8fIhZOcauTb8T6iBRsd1ZfRlMcntlygOpZnykU/u');
 
INSERT OR IGNORE INTO SETTINGS (setting_key, setting_value) VALUES
    ('company_name', 'Clinical Services'),
    ('logo_base64', '');
 
CREATE VIEW IF NOT EXISTS V_LAB_RESULTS_FULL AS
SELECT  lr.result_id, p.usubjid, p.patient_id, s.site_id, s.site_name, s.country,
        v.visit_code, v.visit_num, v.visit_date,
        lr.test_code, lr.test_name, lr.result_value, lr.result_unit, lr.result_date,
        lr.ref_low, lr.ref_high, lr.status
FROM LAB_RESULTS lr
JOIN VISITES v ON lr.visit_id = v.visit_id
JOIN PATIENTS p ON v.patient_id = p.patient_id
JOIN SITES s ON p.site_id = s.site_id;
 