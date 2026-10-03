"""Controlled training-history loader for BLOOD LIMS.

Loads the V14 multi-month demo CSVs into an empty database and creates a
realistic mixture of PENDING / TECHNICAL_OK / REVIEWED states plus historical
archive artefacts. This module is strictly for training/demo data.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from archive_service import archive_document
from business_logic import compute_oor_flag, select_vinc_for_export
from db import (
    DB_PATH,
    get_connection,
    import_lab_results_batch,
    mark_biological_validation,
    mark_technical_validation,
    register_export_package,
    set_setting,
    read_full_results,
)
from export_package import build_vinc_package
from audit import log_audit
from mailbox import send_internal_message_to_many

BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "demo_data_v14" / "history"


REVIEW_PLANS = {
    # Historical weeks: completed and sponsor-ready.
    "W03": "REVIEWED",
    "W05": "REVIEWED",
    "W07": "REVIEWED",
    "W09": "REVIEWED",
    "W11": "REVIEWED",
    "W13": "REVIEWED",
    "W15": "REVIEWED",
    # Recent period: deliberately mixed for dashboard realism.
    "W17": "MIXED",
    "W19": "MIXED",
    "W21": "TECHNICAL_OK",
    "W23": "PENDING",
    "W25": "PENDING",
}


def _list_files():
    return sorted(HISTORY_DIR.glob("*.csv"))


def _empty_result_state(conn) -> bool:
    row = conn.execute("SELECT COUNT(*) FROM LAB_RESULTS").fetchone()
    return not row or int(row[0]) == 0


def _apply_status_plan(conn, batch_id, plan):
    ids = [
        int(r[0]) for r in conn.execute(
            "SELECT result_id FROM LAB_RESULTS WHERE import_batch_id=? ORDER BY result_id",
            (batch_id,),
        ).fetchall()
    ]
    if not ids:
        return 0

    if plan == "REVIEWED":
        mark_technical_validation(conn, ids, "lab_tech1")
        mark_biological_validation(
            conn, ids, "biologist1", "Reviewed and approved",
            "Historical demo batch reviewed according to the training scenario.",
        )
        return len(ids)

    if plan == "TECHNICAL_OK":
        mark_technical_validation(conn, ids, "lab_tech1")
        return len(ids)

    if plan == "MIXED":
        split = max(1, int(len(ids) * 0.55))
        tech_ids = ids[:split]
        reviewed_ids = ids[split:]
        if tech_ids:
            mark_technical_validation(conn, tech_ids, "lab_tech1")
        if reviewed_ids:
            mark_technical_validation(conn, reviewed_ids, "lab_tech1")
            mark_biological_validation(
                conn, reviewed_ids, "biologist1", "Reviewed and approved",
                "Historical demo subset reviewed according to the training scenario.",
            )
        return len(ids)

    return 0


def _create_historical_packages(conn):
    """Create monthly sponsor package artefacts for Jan-Jun 2026."""
    monthly_cutoffs = [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
        date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30),
    ]
    for cutoff in monthly_cutoffs:
        all_results = read_full_results(conn)
        if all_results.empty:
            continue
        vinc = compute_oor_flag(all_results[all_results["visit_code"] == "VINC"].copy())
        eligible = select_vinc_for_export(vinc, cutoff)
        if eligible.empty:
            continue
        package = build_vinc_package(eligible, cutoff, "system")
        archive_dt = datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc)
        archive_document(
            conn, package["package_bytes"], package["filename"],
            stakeholder="Clinical_Services", direction="OUTBOUND", document_type="VINC_PACKAGE",
            created_by="system", related_id=package["package_id"],
            description="Historical training package generated to demonstrate monthly sponsor archiving.",
            archive_datetime=archive_dt, mimetype="application/zip",
        )
        register_export_package(
            conn, package["package_id"], "VINC_MONTHLY_CRO_TO_SPONSOR", "system", cutoff,
            package["row_count"], package["csv_sha256"], package["package_sha256"], package["filename"], status="SENT"
        )
        # A separate sponsor receipt copy demonstrates the two-sided archive.
        archive_document(
            conn, package["package_bytes"], package["filename"],
            stakeholder="LPH_Sponsor", direction="INBOUND", document_type="SPONSOR_RECEIPT",
            created_by="system", related_id=package["package_id"],
            description="Historical sponsor receipt copy for demonstration and retrieval testing.",
            archive_datetime=archive_dt, mimetype="application/zip",
        )
        conn.execute(
            "UPDATE EXPORT_PACKAGES SET sent_at=?, sent_by='system', delivery_reference=? WHERE package_id=?",
            (datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc).isoformat(timespec="seconds"),
             f"DEMO-HISTORY-{cutoff.isoformat()}", package["package_id"]),
        )
        conn.commit()
        set_setting(conn, "last_vinc_sent_period", cutoff.strftime("%Y-%m"))
        send_internal_message_to_many(
            conn, ["sponsor_lph"],
            f"BLOOD Study — Historical monthly VINC package — {cutoff.isoformat()}",
            f"Historical demonstration package. Cut-off: {cutoff.isoformat()}. Records: {package['row_count']}.",
            sender_username="system", sender_label="BLOOD LIMS Demo Loader",
            attachment_bytes=package["package_bytes"], attachment_name=package["filename"],
            attachment_mimetype="application/zip",
        )


def load_demo_history(conn):
    """Load multi-month history into an empty training DB.

    Returns a dict with a human-readable message and counters. Raises if the
    database is not empty to prevent accidental mixing of demo states.
    """
    if not _empty_result_state(conn):
        return {"status": "SKIPPED", "message": "Demo history was not loaded because LAB_RESULTS is not empty. Reset the training database first."}

    files = _list_files()
    if not files:
        return {"status": "ERROR", "message": "No V14 demo history CSV files were found."}

    total_rows = 0
    batches = []
    reviewed = 0
    tech_ok = 0

    for path in files:
        df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        received = str(df["source_received_datetime"].dropna().iloc[0]) if "source_received_datetime" in df.columns else None
        batch_id, inserted, skipped = import_lab_results_batch(
            conn, df, "lab_tech1", path.name, sha, received_at=received
        )
        # The UI normally archives after an interactive import. The loader does
        # the same automatically for each historical source file.
        archive_dt = None
        if received:
            archive_dt = pd.to_datetime(received, utc=True).to_pydatetime()
        archive_document(
            conn, data, path.name,
            stakeholder="Central_Lab_Results", direction="INBOUND", document_type="CSV",
            created_by="lab_tech1", related_id=batch_id,
            description="Historical Central Laboratory source received in the V14 training scenario.",
            archive_datetime=archive_dt, mimetype="text/csv",
        )
        plan_key = next((p for p in REVIEW_PLANS if p in path.stem), None)
        plan = REVIEW_PLANS.get(plan_key or "", "PENDING")
        n = _apply_status_plan(conn, batch_id, plan)
        if plan == "REVIEWED":
            reviewed += n
        elif plan in {"TECHNICAL_OK", "MIXED"}:
            tech_ok += n
        total_rows += inserted
        batches.append(batch_id)

    _create_historical_packages(conn)
    set_setting(conn, "demo_history_seeded", "1")
    set_setting(conn, "demo_history_version", "14")
    log_audit(
        conn, "DEMO_DATA", "DEMO_HISTORY_LOADED", "system",
        comment=f"files={len(files)}; rows={total_rows}; batches={len(batches)}",
    )
    return {
        "status": "LOADED",
        "message": f"Loaded {len(files)} chronological CSV batches ({total_rows} results) with historical workflow states and sponsor archives.",
        "files": len(files), "rows": total_rows, "batches": len(batches),
        "reviewed": reviewed, "technical_ok_or_mixed": tech_ok,
    }


if __name__ == "__main__":
    conn = get_connection()
    result = load_demo_history(conn)
    print(result["message"])
"""Controlled training-history loader for BLOOD LIMS.

Loads the V14 multi-month demo CSVs into an empty database and creates a
realistic mixture of PENDING / TECHNICAL_OK / REVIEWED states plus historical
archive artefacts. This module is strictly for training/demo data.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from archive_service import archive_document
from business_logic import compute_oor_flag, select_vinc_for_export
from db import (
    DB_PATH,
    get_connection,
    import_lab_results_batch,
    mark_biological_validation,
    mark_technical_validation,
    register_export_package,
    set_setting,
    read_full_results,
)
from export_package import build_vinc_package
from audit import log_audit
from mailbox import send_internal_message_to_many

BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "demo_data_v14" / "history"


REVIEW_PLANS = {
    # Historical weeks: completed and sponsor-ready.
    "W03": "REVIEWED",
    "W05": "REVIEWED",
    "W07": "REVIEWED",
    "W09": "REVIEWED",
    "W11": "REVIEWED",
    "W13": "REVIEWED",
    "W15": "REVIEWED",
    # Recent period: deliberately mixed for dashboard realism.
    "W17": "MIXED",
    "W19": "MIXED",
    "W21": "TECHNICAL_OK",
    "W23": "PENDING",
    "W25": "PENDING",
    # Extension W27-W49: the batch itself is always REVIEWED (closed,
    # historical month) except the very last one, which is left PENDING so
    # the live demo still has a "current" queue to work through, exactly
    # like W25 did before this extension.
    "W27": "REVIEWED",
    "W29": "REVIEWED",
    "W31": "REVIEWED",
    "W33": "REVIEWED",
    "W35": "REVIEWED",
    "W37": "REVIEWED",
    "W39": "REVIEWED",
    "W41": "REVIEWED",
    "W43": "REVIEWED",
    "W45": "REVIEWED",
    "W47": "REVIEWED",
    "W49": "PENDING",
}


def _list_files():
    return sorted(HISTORY_DIR.glob("*.csv"))


def _empty_result_state(conn) -> bool:
    row = conn.execute("SELECT COUNT(*) FROM LAB_RESULTS").fetchone()
    return not row or int(row[0]) == 0


def _apply_status_plan(conn, batch_id, plan):
    ids = [
        int(r[0]) for r in conn.execute(
            "SELECT result_id FROM LAB_RESULTS WHERE import_batch_id=? ORDER BY result_id",
            (batch_id,),
        ).fetchall()
    ]
    if not ids:
        return 0

    if plan == "REVIEWED":
        mark_technical_validation(conn, ids, "lab_tech1")
        mark_biological_validation(
            conn, ids, "biologist1", "Reviewed and approved",
            "Historical demo batch reviewed according to the training scenario.",
        )
        return len(ids)

    if plan == "TECHNICAL_OK":
        mark_technical_validation(conn, ids, "lab_tech1")
        return len(ids)

    if plan == "MIXED":
        split = max(1, int(len(ids) * 0.55))
        tech_ids = ids[:split]
        reviewed_ids = ids[split:]
        if tech_ids:
            mark_technical_validation(conn, tech_ids, "lab_tech1")
        if reviewed_ids:
            mark_technical_validation(conn, reviewed_ids, "lab_tech1")
            mark_biological_validation(
                conn, reviewed_ids, "biologist1", "Reviewed and approved",
                "Historical demo subset reviewed according to the training scenario.",
            )
        return len(ids)

    return 0


def _create_historical_packages(conn):
    """Create monthly sponsor package artefacts for Jan-Jun 2026."""
    monthly_cutoffs = [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
        date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30),
        # Extension W27-W49: July through November 2026 so the sponsor
        # quarterly/monthly archive also covers Q3 and Q4, not just H1.
        date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 30),
        date(2026, 10, 31), date(2026, 11, 30),
    ]
    for cutoff in monthly_cutoffs:
        all_results = read_full_results(conn)
        if all_results.empty:
            continue
        vinc = compute_oor_flag(all_results[all_results["visit_code"] == "VINC"].copy())
        eligible = select_vinc_for_export(vinc, cutoff)
        if eligible.empty:
            continue
        package = build_vinc_package(eligible, cutoff, "system")
        archive_dt = datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc)
        archive_document(
            conn, package["package_bytes"], package["filename"],
            stakeholder="Clinical_Services", direction="OUTBOUND", document_type="VINC_PACKAGE",
            created_by="system", related_id=package["package_id"],
            description="Historical training package generated to demonstrate monthly sponsor archiving.",
            archive_datetime=archive_dt, mimetype="application/zip",
        )
        register_export_package(
            conn, package["package_id"], "VINC_MONTHLY_CRO_TO_SPONSOR", "system", cutoff,
            package["row_count"], package["csv_sha256"], package["package_sha256"], package["filename"], status="SENT"
        )
        # A separate sponsor receipt copy demonstrates the two-sided archive.
        archive_document(
            conn, package["package_bytes"], package["filename"],
            stakeholder="LPH_Sponsor", direction="INBOUND", document_type="SPONSOR_RECEIPT",
            created_by="system", related_id=package["package_id"],
            description="Historical sponsor receipt copy for demonstration and retrieval testing.",
            archive_datetime=archive_dt, mimetype="application/zip",
        )
        conn.execute(
            "UPDATE EXPORT_PACKAGES SET sent_at=?, sent_by='system', delivery_reference=? WHERE package_id=?",
            (datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc).isoformat(timespec="seconds"),
             f"DEMO-HISTORY-{cutoff.isoformat()}", package["package_id"]),
        )
        conn.commit()
        set_setting(conn, "last_vinc_sent_period", cutoff.strftime("%Y-%m"))
        send_internal_message_to_many(
            conn, ["sponsor_lph"],
            f"BLOOD Study — Historical monthly VINC package — {cutoff.isoformat()}",
            f"Historical demonstration package. Cut-off: {cutoff.isoformat()}. Records: {package['row_count']}.",
            sender_username="system", sender_label="BLOOD LIMS Demo Loader",
            attachment_bytes=package["package_bytes"], attachment_name=package["filename"],
            attachment_mimetype="application/zip",
        )


def load_demo_history(conn):
    """Load multi-month history into an empty training DB.

    Returns a dict with a human-readable message and counters. Raises if the
    database is not empty to prevent accidental mixing of demo states.
    """
    if not _empty_result_state(conn):
        return {"status": "SKIPPED", "message": "Demo history was not loaded because LAB_RESULTS is not empty. Reset the training database first."}

    files = _list_files()
    if not files:
        return {"status": "ERROR", "message": "No V14 demo history CSV files were found."}

    total_rows = 0
    batches = []
    reviewed = 0
    tech_ok = 0

    for path in files:
        df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        received = str(df["source_received_datetime"].dropna().iloc[0]) if "source_received_datetime" in df.columns else None
        batch_id, inserted, skipped = import_lab_results_batch(
            conn, df, "lab_tech1", path.name, sha, received_at=received
        )
        # The UI normally archives after an interactive import. The loader does
        # the same automatically for each historical source file.
        archive_dt = None
        if received:
            archive_dt = pd.to_datetime(received, utc=True).to_pydatetime()
        archive_document(
            conn, data, path.name,
            stakeholder="Central_Lab_Results", direction="INBOUND", document_type="CSV",
            created_by="lab_tech1", related_id=batch_id,
            description="Historical Central Laboratory source received in the V14 training scenario.",
            archive_datetime=archive_dt, mimetype="text/csv",
        )
        plan_key = next((p for p in REVIEW_PLANS if p in path.stem), None)
        plan = REVIEW_PLANS.get(plan_key or "", "PENDING")
        n = _apply_status_plan(conn, batch_id, plan)
        if plan == "REVIEWED":
            reviewed += n
        elif plan in {"TECHNICAL_OK", "MIXED"}:
            tech_ok += n
        total_rows += inserted
        batches.append(batch_id)

    _create_historical_packages(conn)
    set_setting(conn, "demo_history_seeded", "1")
    set_setting(conn, "demo_history_version", "14")
    log_audit(
        conn, "DEMO_DATA", "DEMO_HISTORY_LOADED", "system",
        comment=f"files={len(files)}; rows={total_rows}; batches={len(batches)}",
    )
    return {
        "status": "LOADED",
        "message": f"Loaded {len(files)} chronological CSV batches ({total_rows} results) with historical workflow states and sponsor archives.",
        "files": len(files), "rows": total_rows, "batches": len(batches),
        "reviewed": reviewed, "technical_ok_or_mixed": tech_ok,
    }


if __name__ == "__main__":
    conn = get_connection()
    result = load_demo_history(conn)
    print(result["message"])