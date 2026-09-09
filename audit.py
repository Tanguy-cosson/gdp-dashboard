"""
audit.py — Point d'écriture UNIQUE de la piste d'audit.

Règle d'or : personne n'écrit dans AUDIT_TRAIL directement avec une
requête SQL ailleurs dans le code. On passe systématiquement par
log_audit(), pour garantir que toutes les lignes ont la même forme et
restent exploitables lors d'une extraction réglementaire / inspection.
"""
from db import now_utc_iso


def log_audit(conn, table_name, action, user_name, record_ref=None, comment=None):
    conn.execute(
        "INSERT INTO AUDIT_TRAIL (table_name, action, user_name, event_timestamp, "
        "record_ref, comment) VALUES (?, ?, ?, ?, ?, ?)",
        (table_name, action, user_name, now_utc_iso(), record_ref, comment),
    )
    conn.commit()
