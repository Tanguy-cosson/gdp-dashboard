"""
db.py — Connexion SQLite, lecture/écriture des tables métier.

Toute logique de schéma (colonnes, migrations) vit dans schema.sql.
Ce module ne fait jamais d'ALTER TABLE : si un nouveau champ est
nécessaire, on l'ajoute dans schema.sql et on documente la migration
là-bas (voir l'en-tête du fichier).
"""
import hashlib
import json
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
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA synchronous = FULL;")
    conn.execute("PRAGMA journal_mode = WAL;")
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
                       critical_low=None, critical_high=None, import_batch_id=None,
                       user_name="system"):
    cur = conn.execute(
        """INSERT INTO LAB_RESULTS (
            visit_id, sample_id, test_code, test_name, result_value, result_unit,
            result_date, ref_low, ref_high, critical_low, critical_high, import_batch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (visit_id, sample_id, test_code, test_name, value, unit, result_date,
         ref_low, ref_high, critical_low, critical_high, import_batch_id),
    )
    result_id = cur.lastrowid
    conn.commit()
    try:
        from audit import log_audit
        log_audit(conn, "LAB_RESULTS", "RESULT_CREATED", user_name, record_ref=str(result_id),
                  new_value=json.dumps({"result_value": value, "unit": unit, "test_code": test_code}, ensure_ascii=False),
                  object_type="LAB_RESULT", object_id=str(result_id))
    except Exception:
        pass
    return result_id


def import_lab_results_batch(conn, df, user_name, source_filename, source_sha256):
    """Import CSV all-or-nothing with file lineage and duplicate protection."""
    # Exact file already accepted: idempotent replay.
    existing_batch = conn.execute(
        "SELECT batch_id, imported_rows, skipped_rows FROM IMPORT_BATCHES "
        "WHERE source_sha256 = ? AND status = 'ACCEPTED' ORDER BY received_at DESC LIMIT 1",
        (source_sha256,),
    ).fetchone()
    if existing_batch:
        return existing_batch[0], 0, int(existing_batch[2] or existing_batch[1] or len(df))

    batch_id = f"IMP-{uuid.uuid4().hex[:12].upper()}"
    conn.execute(
        "INSERT INTO IMPORT_BATCHES (batch_id, source_filename, source_sha256, received_at, "
        "received_by, row_count, status) VALUES (?, ?, ?, ?, ?, ?, 'RECEIVED')",
        (batch_id, source_filename, source_sha256, now_utc_iso(), user_name, len(df)),
    )
    conn.commit()

    inserted = 0
    skipped = 0
    try:
        with conn:
            for _, row in df.iterrows():
                site_id = str(row["site_id"]).strip()
                patient_id = str(row["patient_id"]).strip()
                usubjid = str(row["usubjid"]).strip()
                subjid = str(row["subjid"]).strip()

                site = conn.execute("SELECT site_id FROM SITES WHERE site_id = ?", (site_id,)).fetchone()
                if site is None:
                    conn.execute(
                        "INSERT INTO SITES (site_id, site_name, country) VALUES (?, ?, ?)",
                        (site_id, site_id, "UNK"),
                    )

                patient = conn.execute(
                    "SELECT patient_id, usubjid, site_id, subjid, sex, birth_year FROM PATIENTS WHERE patient_id = ?",
                    (patient_id,),
                ).fetchone()
                if patient is None:
                    conn.execute(
                        "INSERT INTO PATIENTS (patient_id, usubjid, site_id, subjid, sex, birth_year) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (patient_id, usubjid, site_id, subjid, row["sex"], int(row["birth_year"])),
                    )
                else:
                    if tuple(patient[1:]) != (usubjid, site_id, subjid, row["sex"], int(row["birth_year"])):
                        raise ValueError(f"Patient identity conflict for {patient_id}: source attributes do not match the existing record")

                visit_row = conn.execute(
                    "SELECT visit_id, visit_date, visit_num FROM VISITES WHERE patient_id = ? AND visit_code = ?",
                    (patient_id, row["visit_code"]),
                ).fetchone()
                if visit_row:
                    visit_id = visit_row[0]
                    if str(visit_row[1]) != str(row["visit_date"]) or int(visit_row[2]) != int(row["visit_num"]):
                        raise ValueError(f"Visit conflict for {usubjid} / {row['visit_code']}: date or visit number differs")
                else:
                    cur = conn.execute(
                        "INSERT INTO VISITES (patient_id, visit_code, visit_date, visit_num) VALUES (?, ?, ?, ?)",
                        (patient_id, row["visit_code"], row["visit_date"], int(row["visit_num"])),
                    )
                    visit_id = cur.lastrowid

                sample_type = row["sample_type"] if "sample_type" in df.columns and pd.notna(row["sample_type"]) else "SERUM"
                collection_dt = row["collection_datetime"] if "collection_datetime" in df.columns and pd.notna(row["collection_datetime"]) else None
                sample_row = conn.execute(
                    "SELECT sample_id FROM SAMPLES WHERE patient_id = ? AND visit_id = ? AND sample_type = ?",
                    (patient_id, visit_id, sample_type),
                ).fetchone()
                if sample_row:
                    sample_id = sample_row[0]
                else:
                    sample_id = f"S-{uuid.uuid4().hex[:10].upper()}"
                    conn.execute(
                        "INSERT INTO SAMPLES (sample_id, patient_id, visit_id, sample_type, collection_datetime, "
                        "receipt_datetime, status, barcode_value) VALUES (?, ?, ?, ?, ?, ?, 'RECEIVED', ?)",
                        (sample_id, patient_id, visit_id, sample_type, collection_dt, now_utc_iso(), sample_id),
                    )

                ref_low = float(row["ref_low"]) if "ref_low" in df.columns and pd.notna(row["ref_low"]) else None
                ref_high = float(row["ref_high"]) if "ref_high" in df.columns and pd.notna(row["ref_high"]) else None
                crit_low = float(row["critical_low"]) if "critical_low" in df.columns and pd.notna(row["critical_low"]) else None
                crit_high = float(row["critical_high"]) if "critical_high" in df.columns and pd.notna(row["critical_high"]) else None
                result_value = float(row["result_value"])

                existing = conn.execute(
                    "SELECT result_id, result_value, result_unit, ref_low, ref_high, critical_low, critical_high "
                    "FROM LAB_RESULTS WHERE visit_id = ? AND sample_id = ? AND test_code = ? AND result_date = ? "
                    "AND record_status = 'ACTIVE'",
                    (visit_id, sample_id, row["test_code"], row["result_date"]),
                ).fetchone()
                candidate = (result_value, row["result_unit"], ref_low, ref_high, crit_low, crit_high)
                if existing:
                    if tuple(existing[1:]) == candidate:
                        skipped += 1
                        continue
                    raise ValueError(
                        f"Conflicting duplicate for {usubjid} / {row['visit_code']} / {row['test_code']} / {row['result_date']}"
                    )

                conn.execute(
                    "INSERT INTO LAB_RESULTS (visit_id, sample_id, test_code, test_name, result_value, result_unit, "
                    "result_date, ref_low, ref_high, critical_low, critical_high, import_batch_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (visit_id, sample_id, row["test_code"], row["test_name"], result_value,
                     row["result_unit"], row["result_date"], ref_low, ref_high, crit_low, crit_high, batch_id),
                )
                inserted += 1

            conn.execute(
                "UPDATE IMPORT_BATCHES SET imported_rows = ?, skipped_rows = ?, status = 'ACCEPTED' WHERE batch_id = ?",
                (inserted, skipped, batch_id),
            )
    except Exception as exc:
        conn.execute(
            "UPDATE IMPORT_BATCHES SET imported_rows = ?, skipped_rows = ?, status = 'REJECTED', error_message = ? WHERE batch_id = ?",
            (inserted, skipped, str(exc), batch_id),
        )
        conn.commit()
        from audit import log_audit
        log_audit(conn, "IMPORT_BATCHES", "INGESTION_CSV_REJECTED", user_name, record_ref=batch_id, comment=str(exc))
        raise

    from audit import log_audit
    log_audit(
        conn, "IMPORT_BATCHES", "INGESTION_CSV", user_name, record_ref=batch_id,
        comment=f"filename={source_filename}; sha256={source_sha256}; rows={len(df)}; inserted={inserted}; skipped={skipped}",
        object_type="IMPORT_BATCH", object_id=batch_id,
    )
    return batch_id, inserted, skipped


def insert_remark(conn, result_id, remark_text, user_name, reason=None):
    conn.execute(
        "INSERT INTO REMARKS (result_id, remark_text, user_name) VALUES (?, ?, ?)",
        (result_id, remark_text.strip(), user_name),
    )
    conn.commit()
    from audit import log_audit
    log_audit(conn, "REMARKS", "ADD_REMARK", user_name, record_ref=str(result_id),
              new_value=remark_text.strip(), reason=reason,
              object_type="LAB_RESULT", object_id=str(result_id))


def mark_technical_validation(conn, result_ids, user_name):
    if not result_ids:
        return 0
    now = now_utc_iso()
    changed = 0
    from audit import log_audit
    for result_id in result_ids:
        row = conn.execute(
            "SELECT status, record_status FROM LAB_RESULTS WHERE result_id = ?", (result_id,)
        ).fetchone()
        if not row or row[1] != "ACTIVE" or row[0] != "PENDING":
            continue
        conn.execute(
            "UPDATE LAB_RESULTS SET status='TECHNICAL_OK', technical_validated_by=?, technical_validated_at=? "
            "WHERE result_id = ? AND status='PENDING' AND record_status='ACTIVE'",
            (user_name, now, result_id),
        )
        changed += 1
        log_audit(conn, "LAB_RESULTS", "STATUS_CHANGE", user_name, record_ref=str(result_id),
                  old_value="PENDING", new_value="TECHNICAL_OK", reason="Technical validation",
                  object_type="LAB_RESULT", object_id=str(result_id))
    conn.commit()
    return changed


def mark_biological_validation(conn, result_ids, user_name, signature_reason, remark_text=None):
    """Final biological approval for explicitly selected, technically validated results."""
    if not result_ids:
        return 0
    now = now_utc_iso()
    changed = 0
    from audit import log_audit
    for result_id in result_ids:
        row = conn.execute(
            "SELECT status, record_status, result_value, result_unit, test_code FROM LAB_RESULTS WHERE result_id = ?",
            (result_id,),
        ).fetchone()
        if not row or row[1] != "ACTIVE" or row[0] != "TECHNICAL_OK":
            continue
        payload = f"{result_id}|{user_name}|{now}|{signature_reason}|{row[2]}|{row[3]}|{row[4]}"
        signature_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        conn.execute(
            "UPDATE LAB_RESULTS SET status='REVIEWED', biologist_validated_by=?, biologist_validated_at=?, "
            "signature_reason=?, remarks=COALESCE(?, remarks) WHERE result_id=? AND status='TECHNICAL_OK' AND record_status='ACTIVE'",
            (user_name, now, signature_reason, remark_text.strip() if remark_text else None, result_id),
        )
        conn.execute(
            "INSERT INTO E_SIGNATURES (result_id, username, meaning, signed_at, auth_method, signature_hash) "
            "VALUES (?, ?, ?, ?, 'PASSWORD_REAUTH', ?)",
            (result_id, user_name, signature_reason, now, signature_hash),
        )
        changed += 1
        log_audit(conn, "LAB_RESULTS", "STATUS_CHANGE", user_name, record_ref=str(result_id),
                  old_value="TECHNICAL_OK", new_value="REVIEWED", reason=signature_reason,
                  object_type="LAB_RESULT", object_id=str(result_id))
        log_audit(conn, "E_SIGNATURES", "E_SIGNATURE_APPLIED", user_name, record_ref=str(result_id),
                  new_value=json.dumps({"meaning": signature_reason, "signature_hash": signature_hash}),
                  reason=signature_reason, object_type="E_SIGNATURE", object_id=str(result_id))
    conn.commit()
    return changed


def create_result_correction(conn, result_id, new_value, reason, user_name):
    """Controlled correction by superseding the active result with a new PENDING version."""
    if not reason or not reason.strip():
        raise ValueError("A documented change reason is required.")
    row = conn.execute(
        """SELECT visit_id, sample_id, test_code, test_name, result_value, result_unit, result_date,
                  ref_low, ref_high, critical_low, critical_high, status, record_status
           FROM LAB_RESULTS WHERE result_id = ?""",
        (result_id,),
    ).fetchone()
    if not row:
        raise ValueError("Result not found.")
    if row[-1] != "ACTIVE":
        raise ValueError("Only an ACTIVE result can be corrected.")
    now = now_utc_iso()
    cur = conn.execute(
        """INSERT INTO LAB_RESULTS (
            visit_id, sample_id, test_code, test_name, result_value, result_unit, result_date,
            ref_low, ref_high, critical_low, critical_high, status, record_status,
            supersedes_result_id, change_reason, import_batch_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 'ACTIVE', ?, ?, NULL)""",
        (row[0], row[1], row[2], row[3], float(new_value), row[5], row[6], row[7], row[8], row[9], row[10],
         result_id, reason.strip()),
    )
    new_result_id = cur.lastrowid
    conn.execute(
        "UPDATE LAB_RESULTS SET record_status='SUPERSEDED', change_reason=? WHERE result_id=?",
        (reason.strip(), result_id),
    )
    conn.commit()
    from audit import log_audit
    log_audit(conn, "LAB_RESULTS", "RESULT_CORRECTED", user_name, record_ref=str(new_result_id),
              old_value=json.dumps({"result_id": result_id, "result_value": row[4]}),
              new_value=json.dumps({"result_id": new_result_id, "result_value": float(new_value)}),
              reason=reason.strip(), object_type="LAB_RESULT", object_id=str(new_result_id))
    return new_result_id


def void_result(conn, result_id, reason, user_name):
    if not reason or not reason.strip():
        raise ValueError("A documented void reason is required.")
    row = conn.execute("SELECT record_status, status, result_value FROM LAB_RESULTS WHERE result_id=?", (result_id,)).fetchone()
    if not row:
        raise ValueError("Result not found.")
    if row[0] != "ACTIVE":
        raise ValueError("Only an ACTIVE result can be voided.")
    conn.execute("UPDATE LAB_RESULTS SET record_status='VOID', change_reason=? WHERE result_id=?", (reason.strip(), result_id))
    conn.commit()
    from audit import log_audit
    log_audit(conn, "LAB_RESULTS", "RESULT_VOIDED", user_name, record_ref=str(result_id),
              old_value=json.dumps({"status": row[1], "record_status": "ACTIVE", "result_value": row[2]}),
              new_value=json.dumps({"record_status": "VOID"}), reason=reason.strip(),
              object_type="LAB_RESULT", object_id=str(result_id))


def register_export_package(conn, package_id, package_type, generated_by, cutoff_date, row_count,
                            csv_sha256, package_sha256, filename, status="GENERATED"):
    conn.execute(
        """INSERT OR IGNORE INTO EXPORT_PACKAGES (
            package_id, package_type, generated_at, generated_by, cutoff_date, row_count,
            csv_sha256, package_sha256, filename, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (package_id, package_type, now_utc_iso(), generated_by, str(cutoff_date), row_count,
         csv_sha256, package_sha256, filename, status),
    )
    conn.commit()


def mark_export_package_downloaded(conn, package_id, user_name):
    conn.execute(
        "UPDATE EXPORT_PACKAGES SET status=CASE WHEN status='SENT' THEN status ELSE 'DOWNLOADED' END WHERE package_id = ?",
        (package_id,),
    )
    conn.commit()
    from audit import log_audit
    log_audit(conn, "EXPORT_PACKAGES", "EXPORT_PACKAGE_DOWNLOADED", user_name, record_ref=package_id,
              object_type="EXPORT_PACKAGE", object_id=package_id)


def mark_export_package_sent(conn, package_id, user_name, delivery_reference):
    conn.execute(
        "UPDATE EXPORT_PACKAGES SET status='SENT', sent_at=?, sent_by=?, delivery_reference=? WHERE package_id=?",
        (now_utc_iso(), user_name, delivery_reference, package_id),
    )
    conn.commit()
    from audit import log_audit
    log_audit(conn, "EXPORT_PACKAGES", "EXPORT_PACKAGE_SENT", user_name, record_ref=package_id,
              comment=f"delivery_reference={delivery_reference}", object_type="EXPORT_PACKAGE", object_id=package_id)


def record_automation_run(conn, run_id, triggered_by, reference_date, mode, outcome, details=None, started_at=None, finished_at=None):
    conn.execute(
        """INSERT OR REPLACE INTO AUTOMATION_RUNS
           (run_id, started_at, finished_at, triggered_by, reference_date, mode, outcome, details)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, started_at or now_utc_iso(), finished_at, triggered_by, str(reference_date), mode, outcome,
         details[:4000] if details else None),
    )
    conn.commit()


def list_recent_automation_runs(conn, limit=20):
    return pd.read_sql_query(
        "SELECT * FROM AUTOMATION_RUNS ORDER BY started_at DESC LIMIT ?", conn, params=(limit,)
    )



# ---------------------------------------------------------------------
# LECTURE
# ---------------------------------------------------------------------
def read_full_results(conn, include_history=False):
    sql = "SELECT * FROM V_LAB_RESULTS_FULL"
    if not include_history:
        sql += " WHERE record_status = 'ACTIVE'"
    return pd.read_sql_query(sql, conn)


def read_result_history(conn, result_id=None):
    if result_id is None:
        return pd.read_sql_query("SELECT * FROM V_LAB_RESULTS_FULL ORDER BY result_id", conn)
    return pd.read_sql_query("SELECT * FROM V_LAB_RESULTS_FULL WHERE result_id = ? OR supersedes_result_id = ? ORDER BY result_id", conn, params=(result_id, result_id))


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
