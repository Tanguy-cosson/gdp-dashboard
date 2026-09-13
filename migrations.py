"""
migrations.py — Migrations de schéma versionnées et idempotentes.

Contexte : une base créée par une ancienne version de schema.sql (avant
l'ajout de la gestion des utilisateurs) n'a pas les colonnes
USERS.active / failed_login_count / locked_until / must_change_password
ni la table PASSWORD_RESET_TOKENS. `CREATE TABLE IF NOT EXISTS` dans
schema.sql ne les ajoutera jamais à une table qui existe déjà.

Principe volontairement robuste : chaque migration se vérifie elle-même
par INTROSPECTION du schéma réel (ex: "la colonne USERS.active
existe-t-elle ?"), PAS en se fiant uniquement à un compteur de version
stocké dans SETTINGS. Un compteur seul peut mentir (ex: une valeur par
défaut insérée par erreur ferait croire qu'une base ancienne est déjà
à jour). schema_version n'est donc mis à jour qu'APRÈS coup, comme
trace/audit — jamais comme condition d'exécution.

C'est l'inverse volontaire de l'ancien pattern
'ALTER TABLE ... ; except: pass' exécuté à chaque page : ici c'est
tracé, appliqué au plus une fois par base (grâce à l'introspection),
et audité.
"""
from audit import log_audit
from db import set_setting


def _column_exists(conn, table, column):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _table_exists(conn, table):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _migration_1_user_management(conn):
    """Ajoute la gestion de compte (verrouillage, désactivation,
    changement forcé) et la table des jetons de réinitialisation."""
    if not _column_exists(conn, "USERS", "active"):
        conn.execute("ALTER TABLE USERS ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if not _column_exists(conn, "USERS", "failed_login_count"):
        conn.execute("ALTER TABLE USERS ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0")
    if not _column_exists(conn, "USERS", "locked_until"):
        conn.execute("ALTER TABLE USERS ADD COLUMN locked_until TEXT")
    if not _column_exists(conn, "USERS", "must_change_password"):
        conn.execute("ALTER TABLE USERS ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
    if not _column_exists(conn, "USERS", "created_at"):
        # SQLite interdit un DEFAULT non constant dans ALTER TABLE ADD COLUMN
        # (ex: datetime('now')) -> on ajoute la colonne nue, puis on
        # rétro-remplit les lignes existantes en une passe.
        conn.execute("ALTER TABLE USERS ADD COLUMN created_at TEXT")
        conn.execute("UPDATE USERS SET created_at = datetime('now') WHERE created_at IS NULL")
    if not _table_exists(conn, "PASSWORD_RESET_TOKENS"):
        conn.execute("""
            CREATE TABLE PASSWORD_RESET_TOKENS (
                token       TEXT PRIMARY KEY,
                username     TEXT NOT NULL,
                created_at     TEXT DEFAULT (datetime('now')),
                expires_at       TEXT NOT NULL,
                used_at             TEXT,
                FOREIGN KEY (username) REFERENCES USERS(username)
            )
        """)
    conn.commit()


def _migration_2_gdpr_and_hl7(conn):
    """Ajoute les champs de pseudonymisation RGPD sur PATIENTS et la
    table CONSENT."""
    if not _column_exists(conn, "PATIENTS", "anonymized"):
        conn.execute("ALTER TABLE PATIENTS ADD COLUMN anonymized INTEGER NOT NULL DEFAULT 0")
    if not _column_exists(conn, "PATIENTS", "anonymized_at"):
        conn.execute("ALTER TABLE PATIENTS ADD COLUMN anonymized_at TEXT")
    if not _table_exists(conn, "CONSENT"):
        conn.execute("""
            CREATE TABLE CONSENT (
                consent_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id       TEXT NOT NULL,
                usubjid           TEXT NOT NULL,
                status             TEXT NOT NULL,
                document_ref         TEXT,
                recorded_by            TEXT NOT NULL,
                recorded_at              TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (patient_id) REFERENCES PATIENTS(patient_id)
            )
        """)
    conn.commit()


def _migration_3_internal_mailbox(conn):
    """Ajoute la messagerie interne (voir mailbox.py) et le champ
    'poste' sur les utilisateurs, pour l'espace Mon Compte."""
    if not _column_exists(conn, "USERS", "job_title"):
        conn.execute("ALTER TABLE USERS ADD COLUMN job_title TEXT")
    if not _table_exists(conn, "MESSAGES"):
        conn.execute("""
            CREATE TABLE MESSAGES (
                message_id           INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_username     TEXT NOT NULL,
                sender_username           TEXT,
                sender_label                 TEXT NOT NULL,
                subject                         TEXT NOT NULL,
                body                               TEXT NOT NULL,
                attachment_name                      TEXT,
                attachment_data                         BLOB,
                attachment_mimetype                        TEXT,
                is_read                                       INTEGER NOT NULL DEFAULT 0,
                created_at                                       TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (recipient_username) REFERENCES USERS(username)
            )
        """)
    conn.commit()


def _migration_4_audit_record_ref(conn):
    """Ajoute AUDIT_TRAIL.record_ref — présente dans schema.sql depuis
    le début de nos échanges, mais jamais migrée pour les bases créées
    avant cet ajout (c'est ce qui a provoqué l'erreur 'no column named
    record_ref' en production)."""
    if not _column_exists(conn, "AUDIT_TRAIL", "record_ref"):
        conn.execute("ALTER TABLE AUDIT_TRAIL ADD COLUMN record_ref TEXT")
    conn.commit()


# (version, nom, fonction). Toujours ajouter en fin de liste, ne
# jamais modifier une migration déjà publiée — en écrire une nouvelle
# à la place si un correctif est nécessaire.
MIGRATIONS = [
    (1, "user_management_and_password_reset", _migration_1_user_management),
    (2, "gdpr_and_hl7", _migration_2_gdpr_and_hl7),
    (3, "internal_mailbox", _migration_3_internal_mailbox),
    (4, "audit_record_ref", _migration_4_audit_record_ref),
]


def run_migrations(conn):
    """Applique chaque migration (elles sont idempotentes en interne :
    rejouer une migration déjà appliquée ne fait rien, grâce à
    l'introspection). schema_version est mis à jour après coup, à titre
    de trace uniquement — il ne conditionne jamais l'exécution."""
    checks = {
        1: lambda: not _column_exists(conn, "USERS", "active"),
        2: lambda: not _column_exists(conn, "PATIENTS", "anonymized"),
        3: lambda: not _column_exists(conn, "USERS", "job_title"),
        4: lambda: not _column_exists(conn, "AUDIT_TRAIL", "record_ref"),
    }
    for version, name, migration_fn in MIGRATIONS:
        was_missing = checks.get(version, lambda: True)()

        migration_fn(conn)

        if was_missing:
            log_audit(conn, "SCHEMA", "MIGRATION_APPLIED", "system", comment=f"v{version}: {name}")

        set_setting(conn, "schema_version", str(version))
