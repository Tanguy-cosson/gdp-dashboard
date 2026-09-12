"""
gdpr.py — Droits RGPD au niveau applicatif.

Ce module rapproche l'application de la conformité RGPD (droit
d'accès, minimisation, journal de consentement), mais ne remplace pas
l'obligation d'héberger de véritables données de santé chez un
hébergeur certifié HDS.

Note sur le "droit à l'effacement" en essai clinique : les données
d'un essai clinique sont soumises à des obligations légales de
conservation qui priment sur une suppression immédiate. La bonne
pratique est donc de PSEUDONYMISER (pas de supprimer) — voir
`anonymize_patient`.
"""
from db import now_utc_iso, read_full_results, read_remarks
from audit import log_audit


def export_patient_data(conn, usubjid):
    """Droit d'accès (RGPD art. 15) : renvoie toutes les données liées
    à un patient, structurées pour export JSON."""
    df = read_full_results(conn)
    patient_df = df[df["usubjid"] == usubjid]
    if patient_df.empty:
        return None

    remarks_df = read_remarks(conn)
    patient_remarks = remarks_df[remarks_df["usubjid"] == usubjid] if not remarks_df.empty else remarks_df

    first = patient_df.iloc[0]
    return {
        "usubjid": usubjid,
        "patient_id": first["patient_id"],
        "site_id": first["site_id"],
        "site_name": first["site_name"],
        "sex": first["sex"],
        "birth_year": int(first["birth_year"]) if first["birth_year"] else None,
        "results": patient_df[[
            "visit_code", "test_code", "test_name", "result_value", "result_unit",
            "result_date", "status", "sample_id", "barcode_value",
        ]].to_dict(orient="records"),
        "notes": (patient_remarks[["remark_text", "user_name", "created_at"]]
                  .to_dict(orient="records") if patient_remarks is not None and not patient_remarks.empty else []),
        "exported_at_utc": now_utc_iso(),
    }


def anonymize_patient(conn, usubjid, user_name):
    """Pseudonymise un patient : généralise l'année de naissance en
    tranche de 5 ans, retire le sous-identifiant local, marque le
    dossier comme anonymisé. Les résultats cliniques sont CONSERVÉS."""
    cur = conn.execute("SELECT patient_id, birth_year FROM PATIENTS WHERE usubjid = ?", (usubjid,))
    row = cur.fetchone()
    if row is None:
        return False

    patient_id, birth_year = row
    if birth_year:
        generalized_year = (int(birth_year) // 5) * 5
    else:
        generalized_year = None

    conn.execute(
        "UPDATE PATIENTS SET birth_year = ?, subjid = 'ANONYMIZED', "
        "anonymized = 1, anonymized_at = ? WHERE patient_id = ?",
        (generalized_year, now_utc_iso(), patient_id),
    )
    conn.commit()
    log_audit(conn, "PATIENTS", "GDPR_ANONYMIZED", user_name, record_ref=usubjid)
    return True


def is_anonymized(conn, usubjid):
    cur = conn.execute("SELECT anonymized FROM PATIENTS WHERE usubjid = ?", (usubjid,))
    row = cur.fetchone()
    return bool(row[0]) if row else False


def record_consent(conn, patient_id, usubjid, status, document_ref, user_name):
    """status : 'GRANTED', 'WITHDRAWN', ou autre libellé métier.
    document_ref : référence du formulaire signé (cette fonction ne
    stocke PAS le document lui-même)."""
    conn.execute(
        "INSERT INTO CONSENT (patient_id, usubjid, status, document_ref, recorded_by, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (patient_id, usubjid, status, document_ref, user_name, now_utc_iso()),
    )
    conn.commit()
    log_audit(conn, "CONSENT", f"CONSENT_{status}", user_name, record_ref=usubjid,
              comment=document_ref)


def get_consent_history(conn, usubjid):
    import pandas as pd
    return pd.read_sql_query(
        "SELECT status, document_ref, recorded_by, recorded_at FROM CONSENT "
        "WHERE usubjid = ? ORDER BY recorded_at DESC",
        conn, params=(usubjid,),
    )


def get_all_consent(conn):
    import pandas as pd
    return pd.read_sql_query(
        "SELECT usubjid, status, document_ref, recorded_by, recorded_at FROM CONSENT "
        "ORDER BY recorded_at DESC",
        conn,
    )
