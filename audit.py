"""Centralized append-only audit trail writer.

The application uses this function for all audit events. Audit records retain
field-level old/new values when a controlled data change occurs.
"""
from db import now_utc_iso


def log_audit(
    conn,
    table_name,
    action,
    user_name,
    record_ref=None,
    comment=None,
    old_value=None,
    new_value=None,
    reason=None,
    object_type=None,
    object_id=None,
):
    # Backward-compatible writer: legacy databases may temporarily expose only
    # the original audit columns while migrations are being applied. We insert
    # only columns that exist, while the final schema stores full field-level metadata.
    existing = {row[1] for row in conn.execute("PRAGMA table_info(AUDIT_TRAIL)").fetchall()}
    values = {
        "table_name": table_name,
        "action": action,
        "user_name": user_name,
        "event_timestamp": now_utc_iso(),
        "record_ref": record_ref,
        "comment": comment,
        "old_value": old_value,
        "new_value": new_value,
        "change_reason": reason,
        "object_type": object_type,
        "object_id": object_id,
    }
    columns = [c for c in values if c in existing]
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO AUDIT_TRAIL ({', '.join(columns)}) VALUES ({placeholders})",
        [values[c] for c in columns],
    )
    conn.commit()
