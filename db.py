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
from datetime import datetime, timezone

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
