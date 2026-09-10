-- =====================================================================
-- BLOOD STUDY LIMS — SCHEMA
-- Toutes les migrations sont versionnées ICI. Aucune ALTER TABLE ne
-- doit plus être exécutée depuis le code applicatif (voir historique :
-- l'ancienne page de validation biologique le faisait à chaque
-- rechargement de page, ce qui est fragile et invisible en revue de
-- code). Toute évolution future de schéma = une nouvelle section
-- numérotée ci-dessous, jamais un ALTER TABLE caché dans app.py.
-- =====================================================================
 
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
    visit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id  TEXT NOT NULL,
    visit_code  TEXT NOT NULL CHECK (visit_code IN ('VINC', 'V1', 'V2')),
    visit_date  TEXT NOT NULL,
    visit_num   INTEGER NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES PATIENTS(patient_id),
    UNIQUE (patient_id, visit_code)
);
 
-- ---------------------------------------------------------------------
-- SAMPLES : chaîne de conservation (chain of custody).
-- barcode_value : identifiant encodé en Code128 sur l'étiquette physique
--   (par défaut = sample_id, mais séparé pour pouvoir réimprimer une
--   étiquette avec une autre nomenclature sans toucher aux clés
--   internes).
-- label_printed / label_printed_at : traçabilité de l'édition d'étiquette.
-- ---------------------------------------------------------------------
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
    storage_location      TEXT,
    barcode_value        TEXT UNIQUE,
    label_printed         INTEGER NOT NULL DEFAULT 0,
    label_printed_at      TEXT,
    label_printed_by      TEXT,
    FOREIGN KEY (patient_id) REFERENCES PATIENTS(patient_id),
    FOREIGN KEY (visit_id) REFERENCES VISITES(visit_id),
    UNIQUE (patient_id, visit_id, sample_type)
);
 
-- ---------------------------------------------------------------------
-- Emplacement physique de stockage (congélateur / rack / boîte / puits).
-- Séparée de SAMPLES car un échantillon peut être déplacé plusieurs
-- fois pendant l'étude (chaque déplacement = une nouvelle ligne, la
-- plus récente fait foi) plutôt que d'écraser une position unique.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS STORAGE_LOCATIONS (
    location_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id            TEXT NOT NULL,
    freezer_id           TEXT DEFAULT 'FREEZER-80C-01',
    rack_id               TEXT NOT NULL,
    box_id                TEXT NOT NULL,
    position_well          TEXT NOT NULL,   -- ex : 'B4'
    freeze_thaw_cycles    INTEGER DEFAULT 0,
    volume_ul             REAL,             -- volume restant en microlitres
    moved_at               TEXT DEFAULT (datetime('now')),
    moved_by               TEXT,
    FOREIGN KEY (sample_id) REFERENCES SAMPLES(sample_id)
);
 
-- ---------------------------------------------------------------------
-- LAB_RESULTS : workflow de validation à 2 niveaux.
--   PENDING       -> à la charge du technicien de laboratoire
--   TECHNICAL_OK  -> validé techniquement, à la charge du biologiste
--   REVIEWED      -> validé biologiquement (état final)
-- remarks : commentaire de synthèse au moment de la signature
-- biologique (distinct des notes libres de la table REMARKS, qui
-- peuvent être ajoutées par plusieurs rôles à tout moment).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS LAB_RESULTS (
    result_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id                 INTEGER NOT NULL,
    sample_id                 TEXT,
    test_code                 TEXT NOT NULL,
    test_name                 TEXT NOT NULL,
    result_value               REAL,
    result_unit                 TEXT,
    result_date                 TEXT NOT NULL,
    ref_low                     REAL,
    ref_high                     REAL,
    critical_low                 REAL,
    critical_high                 REAL,
    status                       TEXT NOT NULL DEFAULT 'PENDING'
                                 CHECK (status IN ('PENDING', 'TECHNICAL_OK', 'REVIEWED')),
    technical_validated_by         TEXT,
    technical_validated_at         TEXT,
    biologist_validated_by         TEXT,
    biologist_validated_at         TEXT,
    signature_reason               TEXT,   -- "meaning of signature" — exigence 21 CFR Part 11 §11.50
    remarks                         TEXT,
    lab_source                       TEXT DEFAULT 'Central Lab Results',
    FOREIGN KEY (visit_id) REFERENCES VISITES(visit_id),
    FOREIGN KEY (sample_id) REFERENCES SAMPLES(sample_id)
);
 
-- ---------------------------------------------------------------------
-- AUDIT_TRAIL : une seule forme d'écriture possible, via log_audit()
-- dans audit.py. Ne JAMAIS insérer directement dans cette table
-- ailleurs dans le code, et ne jamais lui ajouter de colonne à la volée.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS AUDIT_TRAIL (
    audit_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name          TEXT NOT NULL,
    action               TEXT NOT NULL,
    user_name             TEXT NOT NULL,
    event_timestamp        TEXT DEFAULT (datetime('now')),
    record_ref              TEXT,   -- ex : result_id, sample_id concernés
    comment                   TEXT
);
 
-- Immutabilité de la piste d'audit (21 CFR Part 11 / Annexe 11 §9) :
-- interdiction, au niveau base, de supprimer ou modifier une ligne.
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
 
-- ---------------------------------------------------------------------
-- RBAC : 5 rôles métier.
-- ---------------------------------------------------------------------
-- active / failed_login_count / locked_until / must_change_password :
-- gestion des comptes (verrouillage anti brute-force, changement de mot
-- de passe forcé après réinitialisation par un administrateur CRO).
CREATE TABLE IF NOT EXISTS USERS (
    username                TEXT PRIMARY KEY,
    full_name                TEXT NOT NULL,
    role                      TEXT NOT NULL CHECK (role IN
                              ('LAB_TECH', 'BIOLOGIST', 'PHYSICIAN', 'CRO', 'SPONSOR')),
    password_hash              TEXT NOT NULL,
    email                        TEXT,
    active                        INTEGER NOT NULL DEFAULT 1,
    failed_login_count             INTEGER NOT NULL DEFAULT 0,
    locked_until                     TEXT,
    must_change_password               INTEGER NOT NULL DEFAULT 0,
    created_at                           TEXT DEFAULT (datetime('now'))
);
 
-- Jetons de réinitialisation de mot de passe à usage unique (flux
-- "mot de passe oublié" en self-service, sans intervention CRO).
CREATE TABLE IF NOT EXISTS PASSWORD_RESET_TOKENS (
    token           TEXT PRIMARY KEY,
    username         TEXT NOT NULL,
    created_at         TEXT DEFAULT (datetime('now')),
    expires_at           TEXT NOT NULL,
    used_at                TEXT,
    FOREIGN KEY (username) REFERENCES USERS(username)
);
 
CREATE TABLE IF NOT EXISTS REMARKS (
    remark_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id        INTEGER NOT NULL,
    remark_text        TEXT NOT NULL,
    user_name            TEXT NOT NULL,
    created_at            TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (result_id) REFERENCES LAB_RESULTS(result_id)
);
 
CREATE TABLE IF NOT EXISTS SETTINGS (
    setting_key     TEXT PRIMARY KEY,
    setting_value    TEXT
);
 
-- Comptes de démonstration — mots de passe en clair UNIQUEMENT pour cet
-- exercice pédagogique (à retirer / régénérer avant toute mise en
-- production réelle avec des données patients).
-- lab_tech1 / labtech2026   (Technicien de laboratoire)
-- biologist1 / bio2026       (Biologiste)
-- physician1 / doc2026         (Médecin investigateur)
-- cro_arc / cro2026               (CRO)
-- sponsor_lph / sponsor2026          (Promoteur)
INSERT OR IGNORE INTO USERS (username, full_name, role, password_hash, email) VALUES
    ('lab_tech1',  'Technicien de laboratoire',    'LAB_TECH',
     '$2b$12$BPUObptOfVjmafvxxGAoZ.6a4R0fJfo3quDP4jwEeJeWYlb9Z2Rlu', 'labtech1@example.com'),
    ('biologist1', 'Dr. Biologiste',                'BIOLOGIST',
     '$2b$12$viRgcn3ACvAapXR6jzP5mey2U5lhbfq.IRwgCENVvzf1L3oEKoTUm', 'biologist1@example.com'),
    ('physician1', 'Dr. Medecin Investigateur',      'PHYSICIAN',
     '$2b$12$dpBj2k/gbHzt3dVlpFxQU.uO22DS.1lJj6saCwPVBSU17kAQ/yLuO', 'physician1@example.com'),
    ('cro_arc',    'ARC - Clinical Services',        'CRO',
     '$2b$12$KtPxzSk4lhKesScoqnvbL.6pgYPvcqMBXMR26M7cMlJsH.602HSYS', 'cro_arc@example.com'),
    ('sponsor_lph','Promoteur LPH',                  'SPONSOR',
     '$2b$12$obXTbTSMD8C6V8fIhZOcauTb8T6iBRsd1ZfRlMcntlygOpZnykU/u', 'sponsor_lph@example.com');
 
INSERT OR IGNORE INTO SETTINGS (setting_key, setting_value) VALUES
    ('company_name', 'Clinical Services'),
    ('logo_base64', ''),
    -- Automatisation : intervalle des relances (jours) et activation
    ('automation_enabled', '1'),
    ('reminder_ingestion_days', '7'),
    ('last_reminder_ingestion_sent', ''),
    ('last_reminder_sponsor_sent', ''),
    ('last_reminder_critical_sent', ''),
    ('notify_emails_lab', ''),      -- liste séparée par des virgules
    ('notify_emails_sponsor', ''),
    ('notify_emails_critical', '');
-- Remarque : PAS de 'schema_version' ici volontairement. Le numéro de
-- version dans SETTINGS n'est mis à jour QUE par migrations.py, une
-- fois qu'il a réellement vérifié/appliqué chaque migration — jamais
-- par une valeur par défaut insérée ici, qui donnerait un faux
-- sentiment de "déjà à jour" à une base ancienne qui ne l'est pas
-- (voir l'en-tête de migrations.py pour le pourquoi).
 
-- ---------------------------------------------------------------------
-- Vue principale : jointure complète résultats + échantillon + patient
-- + site. Champs échantillon nullable pour compatibilité ascendante.
-- ---------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS V_LAB_RESULTS_FULL AS
SELECT lr.result_id, p.usubjid, p.patient_id, p.sex, p.birth_year,
       s.site_id, s.site_name, s.country,
       v.visit_code, v.visit_num, v.visit_date,
       lr.sample_id, sa.sample_type, sa.collection_datetime, sa.receipt_datetime,
       sa.barcode_value, sa.status AS sample_status,
       lr.test_code, lr.test_name, lr.result_value, lr.result_unit, lr.result_date,
       lr.ref_low, lr.ref_high, lr.critical_low, lr.critical_high,
       lr.status,
       lr.technical_validated_by, lr.technical_validated_at,
       lr.biologist_validated_by, lr.biologist_validated_at,
       lr.signature_reason, lr.remarks
FROM LAB_RESULTS lr
JOIN VISITES v ON lr.visit_id = v.visit_id
JOIN PATIENTS p ON v.patient_id = p.patient_id
JOIN SITES s ON p.site_id = s.site_id
LEFT JOIN SAMPLES sa ON lr.sample_id = sa.sample_id;
