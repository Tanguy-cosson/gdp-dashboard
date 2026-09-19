"""
db.py — Connexion SQLite, lecture/écriture des tables métier.

Toute logique de schéma (colonnes, migrations) vit dans schema.sql.
Ce module ne fait jamais d'ALTER TABLE : si un nouveau champ est
nécessaire, on l'ajoute dans schema.sql et on documente la migration
là-bas (voir l'en-tête du fichier).
"""
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blood_study.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


@st.cache_resource
def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()

    # Rattrape le schéma des bases créées par une version antérieure de
    # schema.sql (voir migrations.py). Import différé pour éviter un
    # cycle (migrations.py importe get_setting/set_setting d'ici).
    from migrations import run_migrations
    run_migrations(conn)

    return conn


def now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------
def get_setting(conn, key, default=None):
    cur = conn.execute("SELECT setting_value FROM SETTINGS WHERE setting_key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row and row[0] not in (None, "") else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO SETTINGS (setting_key, setting_value) VALUES (?, ?) "
        "ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------------
# SITES / PATIENTS / VISITES
# ---------------------------------------------------------------------
def get_or_create_site(conn, site_id, site_name=None, country=None):
    cur = conn.execute("SELECT site_id FROM SITES WHERE site_id = ?", (site_id,))
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO SITES (site_id, site_name, country) VALUES (?, ?, ?)",
            (site_id, site_name or site_id, country or "UNK"),
        )
        conn.commit()
    return site_id


def get_or_create_patient(conn, patient_id, usubjid, site_id, subjid, sex, birth_year):
    cur = conn.execute("SELECT patient_id FROM PATIENTS WHERE patient_id = ?", (patient_id,))
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO PATIENTS (patient_id, usubjid, site_id, subjid, sex, birth_year) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (patient_id, usubjid, site_id, subjid, sex, birth_year),
        )
        conn.commit()
    return patient_id


def get_or_create_visit(conn, patient_id, visit_code, visit_date, visit_num):
    cur = conn.execute(
        "SELECT visit_id FROM VISITES WHERE patient_id = ? AND visit_code = ?",
        (patient_id, visit_code),
    )
    row = cur.fetchone()
    if row is not None:
        return row[0]
    cur = conn.execute(
        "INSERT INTO VISITES (patient_id, visit_code, visit_date, visit_num) VALUES (?, ?, ?, ?)",
        (patient_id, visit_code, visit_date, visit_num),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------
# SAMPLES — avec code-barres auto-assigné à la création
# ---------------------------------------------------------------------
def get_or_create_sample(conn, patient_id, visit_id, sample_type,
                          collection_datetime=None, receipt_datetime=None,
                          storage_location=None):
    """Un échantillon par (patient, visite, type). Un identifiant
    code-barres (Code128-safe : lettres/chiffres/tirets) est assigné
    automatiquement à la création — c'est lui qui est imprimé sur
    l'étiquette physique du tube."""
    sample_type = sample_type or "SERUM"
    cur = conn.execute(
        "SELECT sample_id FROM SAMPLES WHERE patient_id = ? AND visit_id = ? AND sample_type = ?",
        (patient_id, visit_id, sample_type),
    )
    row = cur.fetchone()
    if row is not None:
        return row[0]

    sample_id = f"S-{uuid.uuid4().hex[:10].upper()}"
    conn.execute(
        "INSERT INTO SAMPLES (sample_id, patient_id, visit_id, sample_type, "
        "collection_datetime, receipt_datetime, status, storage_location, barcode_value) "
        "VALUES (?, ?, ?, ?, ?, ?, 'RECEIVED', ?, ?)",
        (sample_id, patient_id, visit_id, sample_type, collection_datetime,
         receipt_datetime or now_utc_iso(), storage_location, sample_id),
    )
    conn.commit()
    return sample_id


def find_sample_by_barcode(conn, barcode_value):
    """Recherche un échantillon par sa valeur de code-barres scannée
    (fonctionne aussi bien avec un scanner USB — qui se comporte comme
    un clavier — qu'avec une lecture par caméra)."""
    cur = conn.execute(
        "SELECT sample_id FROM SAMPLES WHERE barcode_value = ? OR sample_id = ?",
        (barcode_value.strip(), barcode_value.strip()),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_samples_pending_labels(conn):
    return pd.read_sql_query(
        """
        SELECT sa.sample_id, sa.barcode_value, sa.sample_type, sa.receipt_datetime,
               sa.label_printed, p.usubjid, v.visit_code, s.site_name
        FROM SAMPLES sa
        JOIN PATIENTS p ON sa.patient_id = p.patient_id
        JOIN VISITES v ON sa.visit_id = v.visit_id
        JOIN SITES s ON p.site_id = s.site_id
        ORDER BY sa.label_printed ASC, sa.receipt_datetime DESC
        """,
        conn,
    )


def mark_labels_printed(conn, sample_ids, user_name):
    if not sample_ids:
        return
    placeholders = ",".join("?" for _ in sample_ids)
    conn.execute(
        f"UPDATE SAMPLES SET label_printed = 1, label_printed_at = ?, label_printed_by = ? "
        f"WHERE sample_id IN ({placeholders})",
        [now_utc_iso(), user_name] + sample_ids,
    )
    conn.commit()


def add_storage_location(conn, sample_id, rack_id, box_id, position_well,
                          freezer_id="FREEZER-80C-01", volume_ul=None, user_name=None):
    conn.execute(
        "INSERT INTO STORAGE_LOCATIONS (sample_id, freezer_id, rack_id, box_id, "
        "position_well, volume_ul, moved_at, moved_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sample_id, freezer_id, rack_id, box_id, position_well, volume_ul,
         now_utc_iso(), user_name),
    )
    conn.commit()


def get_current_storage_location(conn, sample_id):
    cur = conn.execute(
        "SELECT freezer_id, rack_id, box_id, position_well, freeze_thaw_cycles, "
        "volume_ul, moved_at FROM STORAGE_LOCATIONS WHERE sample_id = ? "
        "ORDER BY moved_at DESC, location_id DESC LIMIT 1",
        (sample_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    keys = ["freezer_id", "rack_id", "box_id", "position_well",
            "freeze_thaw_cycles", "volume_ul", "moved_at"]
    return dict(zip(keys, row))


def get_all_current_storage_locations(conn):
    """Position ACTUELLE (la plus récente) de chaque échantillon
    stocké au moins une fois — un échantillon déplacé plusieurs fois a
    plusieurs lignes dans STORAGE_LOCATIONS, on ne garde que la
    dernière par sample_id."""
    return pd.read_sql_query(
        """
        SELECT sl.sample_id, sl.freezer_id, sl.rack_id, sl.box_id, sl.position_well,
               sl.volume_ul, sl.moved_at, sa.sample_type, p.usubjid
        FROM STORAGE_LOCATIONS sl
        JOIN (
            SELECT sample_id, MAX(moved_at) AS latest
            FROM STORAGE_LOCATIONS
            GROUP BY sample_id
        ) latest_per_sample
          ON sl.sample_id = latest_per_sample.sample_id AND sl.moved_at = latest_per_sample.latest
        JOIN SAMPLES sa ON sl.sample_id = sa.sample_id
        JOIN PATIENTS p ON sa.patient_id = p.patient_id
        ORDER BY sl.freezer_id, sl.rack_id, sl.box_id, sl.position_well
        """,
        conn,
    )


def get_samples_without_storage(conn):
    """Échantillons reçus mais jamais rangés physiquement (aucune ligne
    dans STORAGE_LOCATIONS) — utile pour repérer ce qui traîne sur la
    paillasse plutôt qu'au congélateur."""
    return pd.read_sql_query(
        """
        SELECT sa.sample_id, sa.sample_type, sa.receipt_datetime, p.usubjid, v.visit_code
        FROM SAMPLES sa
        JOIN PATIENTS p ON sa.patient_id = p.patient_id
        JOIN VISITES v ON sa.visit_id = v.visit_id
        WHERE sa.sample_id NOT IN (SELECT DISTINCT sample_id FROM STORAGE_LOCATIONS)
        ORDER BY sa.receipt_datetime DESC
        """,
        conn,
    )


# ---------------------------------------------------------------------
# LAB_RESULTS
# ---------------------------------------------------------------------
def insert_lab_result(conn, visit_id, test_code, test_name, value, unit, result_date,
                       ref_low=None, ref_high=None, sample_id=None,
                       critical_low=None, critical_high=None):
    conn.execute(
        "INSERT INTO LAB_RESULTS (visit_id, sample_id, test_code, test_name, result_value, "
        "result_unit, result_date, ref_low, ref_high, critical_low, critical_high) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (visit_id, sample_id, test_code, test_name, value, unit, result_date,
         ref_low, ref_high, critical_low, critical_high),
    )
    conn.commit()


def insert_remark(conn, result_id, remark_text, user_name):
    conn.execute(
        "INSERT INTO REMARKS (result_id, remark_text, user_name) VALUES (?, ?, ?)",
        (result_id, remark_text, user_name),
    )
    conn.commit()


def mark_technical_validation(conn, result_ids, user_name):
    if not result_ids:
        return 0
    placeholders = ",".join("?" for _ in result_ids)
    conn.execute(
        f"UPDATE LAB_RESULTS SET status='TECHNICAL_OK', technical_validated_by=?, "
        f"technical_validated_at=? WHERE result_id IN ({placeholders}) AND status='PENDING'",
        [user_name, now_utc_iso()] + result_ids,
    )
    conn.commit()
    return len(result_ids)


def mark_biological_validation(conn, result_ids, user_name, signature_reason, remark_text=None):
    """Valide UNIQUEMENT les result_id passés explicitement (contrairement
    à l'ancienne version qui validait tout LAB_RESULTS.status='TECHNICAL_OK'
    sans distinction). signature_reason porte le "meaning of signature"
    exigé par 21 CFR Part 11 §11.50."""
    if not result_ids:
        return 0
    placeholders = ",".join("?" for _ in result_ids)
    conn.execute(
        f"UPDATE LAB_RESULTS SET status='REVIEWED', biologist_validated_by=?, "
        f"biologist_validated_at=?, signature_reason=?, remarks=COALESCE(?, remarks) "
        f"WHERE result_id IN ({placeholders}) AND status='TECHNICAL_OK'",
        [user_name, now_utc_iso(), signature_reason, remark_text] + result_ids,
    )
    conn.commit()
    return len(result_ids)


# ---------------------------------------------------------------------
# LECTURE
# ---------------------------------------------------------------------
def read_full_results(conn):
    return pd.read_sql_query("SELECT * FROM V_LAB_RESULTS_FULL", conn)


def read_remarks(conn):
    return pd.read_sql_query(
        """
        SELECT r.remark_id, r.result_id, lr.test_code, lr.test_name, p.usubjid,
               r.remark_text, r.user_name, r.created_at
        FROM REMARKS r
        JOIN LAB_RESULTS lr ON r.result_id = lr.result_id
        JOIN VISITES v ON lr.visit_id = v.visit_id
        JOIN PATIENTS p ON v.patient_id = p.patient_id
        ORDER BY r.created_at DESC
        """,
        conn,
    )


def read_audit_trail(conn):
    return pd.read_sql_query(
        "SELECT * FROM AUDIT_TRAIL ORDER BY event_timestamp DESC, audit_id DESC", conn
    )


def count_by_status(conn, status):
    cur = conn.execute("SELECT COUNT(*) FROM LAB_RESULTS WHERE status = ?", (status,))
    return cur.fetchone()[0]


# ---------------------------------------------------------------------
# USERS — administration (création, désactivation, verrouillage)
# ---------------------------------------------------------------------
def list_users(conn):
    return pd.read_sql_query(
        "SELECT username, full_name, role, email, active, failed_login_count, "
        "locked_until, must_change_password, created_at FROM USERS ORDER BY created_at DESC",
        conn,
    )


def username_exists(conn, username):
    cur = conn.execute("SELECT 1 FROM USERS WHERE username = ?", (username,))
    return cur.fetchone() is not None


def create_user(conn, username, full_name, role, email, password_hash):
    conn.execute(
        "INSERT INTO USERS (username, full_name, role, password_hash, email, "
        "active, must_change_password, created_at) VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
        (username, full_name, role, password_hash, email, now_utc_iso()),
    )
    conn.commit()


def set_user_active(conn, username, active: bool):
    conn.execute("UPDATE USERS SET active = ? WHERE username = ?", (1 if active else 0, username))
    conn.commit()


def set_user_role(conn, username, role):
    conn.execute("UPDATE USERS SET role = ? WHERE username = ?", (role, username))
    conn.commit()


def admin_reset_password(conn, username, password_hash):
    """Réinitialisation par un administrateur (CRO) : force un
    changement de mot de passe à la prochaine connexion."""
    conn.execute(
        "UPDATE USERS SET password_hash = ?, must_change_password = 1, "
        "failed_login_count = 0, locked_until = NULL WHERE username = ?",
        (password_hash, username),
    )
    conn.commit()


def clear_must_change_password(conn, username):
    conn.execute("UPDATE USERS SET must_change_password = 0 WHERE username = ?", (username,))
    conn.commit()


def record_login_failure(conn, username, lockout_threshold, lockout_minutes):
    """Incrémente le compteur d'échecs ; verrouille le compte si le
    seuil est atteint. Renvoie True si le compte vient d'être verrouillé."""
    cur = conn.execute("SELECT failed_login_count FROM USERS WHERE username = ?", (username,))
    row = cur.fetchone()
    if row is None:
        return False
    new_count = (row[0] or 0) + 1
    just_locked = new_count >= lockout_threshold
    if just_locked:
        locked_until = (datetime.now(timezone.utc) + timedelta(
            minutes=lockout_minutes)).isoformat(timespec="seconds")
        conn.execute("UPDATE USERS SET failed_login_count = ?, locked_until = ? WHERE username = ?",
                      (new_count, locked_until, username))
    else:
        conn.execute("UPDATE USERS SET failed_login_count = ? WHERE username = ?",
                      (new_count, username))
    conn.commit()
    return just_locked


def record_login_success(conn, username):
    conn.execute(
        "UPDATE USERS SET failed_login_count = 0, locked_until = NULL WHERE username = ?",
        (username,),
    )
    conn.commit()


# ---------------------------------------------------------------------
# Réinitialisation de mot de passe en self-service (jeton par e-mail)
# ---------------------------------------------------------------------
def create_password_reset_token(conn, username, token, expires_minutes):
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO PASSWORD_RESET_TOKENS (token, username, expires_at) VALUES (?, ?, ?)",
        (token, username, expires_at),
    )
    conn.commit()


def consume_password_reset_token(conn, token):
    """Valide un jeton (non expiré, non déjà utilisé), le marque comme
    utilisé, et renvoie le username associé — ou None si invalide."""
    cur = conn.execute(
        "SELECT username, expires_at, used_at FROM PASSWORD_RESET_TOKENS WHERE token = ?",
        (token,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    username, expires_at, used_at = row
    if used_at is not None:
        return None
    expires_dt = datetime.fromisoformat(expires_at)
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_dt:
        return None
    conn.execute("UPDATE PASSWORD_RESET_TOKENS SET used_at = ? WHERE token = ?",
                  (now_utc_iso(), token))
    conn.commit()
    return username


def find_user_by_username_or_email(conn, identifier):
    cur = conn.execute(
        "SELECT username FROM USERS WHERE username = ? OR email = ?",
        (identifier, identifier),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_usubjids_for_results(conn, result_ids):
    """Renvoie la liste (dédupliquée) des usubjid concernés par un lot
    de result_id — utilisé pour savoir à quels patients envoyer un
    compte rendu après une signature biologique groupée."""
    if not result_ids:
        return []
    placeholders = ",".join("?" for _ in result_ids)
    cur = conn.execute(
        f"""
        SELECT DISTINCT p.usubjid
        FROM LAB_RESULTS lr
        JOIN VISITES v ON lr.visit_id = v.visit_id
        JOIN PATIENTS p ON v.patient_id = p.patient_id
        WHERE lr.result_id IN ({placeholders})
        """,
        result_ids,
    )
    return [row[0] for row in cur.fetchall()]
