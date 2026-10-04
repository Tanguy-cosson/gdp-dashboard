"""BLOOD LIMS V14 historical demo loader: weekly W03-W39 + Jan-Aug sponsor deliveries.

Demo-only extension: automatically assigns every imported historical demo sample to
one of three freezer racks (R01, A01, B01), each using four demo boxes (one per rack level), with a maximum of 8 samples per box.
Manual imports are NOT auto-stored; only this training-history loader performs the placement.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from archive_service import archive_document
from audit import log_audit
from db import (
    add_storage_location,
    get_connection,
    get_current_storage_location,
    import_lab_results_batch,
    mark_biological_validation,
    mark_technical_validation,
    read_full_results,
    set_setting,
)
from automation import run_automation_now

BASE_DIR = Path(__file__).resolve().parent
HISTORY_DIR = BASE_DIR / "demo_data_v14" / "history"

# ---------------------------------------------------------------------------
# Demo-only physical storage convention
# ---------------------------------------------------------------------------
DEMO_RACKS = ("R01", "A01", "B01")
DEMO_FREEZER = "FREEZER-80C-01"
DEMO_LEVELS_PER_RACK = 4
DEMO_BOX_CAPACITY = 8
DEMO_WELLS = [f"A{col:02d}" for col in range(1, DEMO_BOX_CAPACITY + 1)]


def _empty_result_state(conn) -> bool:
    row = conn.execute("SELECT COUNT(*) FROM LAB_RESULTS").fetchone()
    return not row or int(row[0]) == 0


def _all_result_ids_for_batch(conn, batch_id):
    return [
        int(r[0])
        for r in conn.execute(
            "SELECT result_id FROM LAB_RESULTS WHERE import_batch_id=? ORDER BY result_id",
            (batch_id,),
        ).fetchall()
    ]


def _demo_volume_ul(sample_type: str) -> float:
    """Controlled demo convention for the sample volume shown on Storage Map.
    This is not a clinical protocol value; it is only a training visualization aid.
    """
    return {
        "SERUM": 500.0,
        "PLASMA": 700.0,
        "WHOLE_BLOOD": 1000.0,
    }.get(str(sample_type).upper(), 500.0)


def _sample_demo_storage_exists(conn, sample_id: str) -> bool:
    return get_current_storage_location(conn, sample_id) is not None


def _auto_store_demo_samples(conn) -> dict:
    """Place historical demo samples only into R01/A01/B01.

    Allocation is deterministic by receipt date and sample_id. Each rack has
    exactly four levels/boxes, all four are populated as evenly as possible,
    each box holds at most 8 samples, and positions A01..A08 are unique
    within each box. The function only touches samples with no prior
    STORAGE_LOCATIONS row, so it never overwrites a manual placement.
    """
    samples = pd.read_sql_query(
        """
        SELECT sa.sample_id, sa.sample_type, sa.receipt_datetime, p.usubjid, v.visit_code
        FROM SAMPLES sa
        JOIN PATIENTS p ON sa.patient_id = p.patient_id
        JOIN VISITES v ON sa.visit_id = v.visit_id
        LEFT JOIN STORAGE_LOCATIONS sl ON sa.sample_id = sl.sample_id
        WHERE sl.sample_id IS NULL
        ORDER BY sa.receipt_datetime, sa.sample_id
        """,
        conn,
    )

    if samples.empty:
        empty_levels = {
            f"{rack}-BOX{level:02d}": 0
            for rack in DEMO_RACKS
            for level in range(1, DEMO_LEVELS_PER_RACK + 1)
        }
        return {
            "placed": 0,
            "already_stored": 0,
            "by_rack": {rack: 0 for rack in DEMO_RACKS},
            "by_rack_and_level": empty_levels,
        }

    rack_counts = {rack: 0 for rack in DEMO_RACKS}
    box_counts = {(rack, level): 0 for rack in DEMO_RACKS for level in range(1, DEMO_LEVELS_PER_RACK + 1)}
    placed = 0
    skipped = 0

    # Round-robin by rack, then round-robin through the four levels.
    # This keeps all four boxes visible in the demo and avoids overfilled boxes.
    capacity_per_rack = DEMO_LEVELS_PER_RACK * DEMO_BOX_CAPACITY

    for idx, row in samples.iterrows():
        sample_id = str(row["sample_id"])
        if _sample_demo_storage_exists(conn, sample_id):
            skipped += 1
            continue

        rack_index = placed % len(DEMO_RACKS)
        rack = DEMO_RACKS[rack_index]
        within_rack_index = rack_counts[rack]
        if within_rack_index >= capacity_per_rack:
            raise RuntimeError(
                f"Demo storage capacity exceeded for rack {rack} "
                f"({DEMO_LEVELS_PER_RACK} levels x {DEMO_BOX_CAPACITY} samples)."
            )

        level = within_rack_index % DEMO_LEVELS_PER_RACK + 1
        position_index = box_counts[(rack, level)]
        if position_index >= DEMO_BOX_CAPACITY:
            raise RuntimeError(
                f"Demo storage capacity exceeded for box {rack}-BOX{level:02d}."
            )
        box = f"{rack}-BOX{level:02d}"
        well = DEMO_WELLS[position_index]
        volume = _demo_volume_ul(row["sample_type"])

        add_storage_location(
            conn,
            sample_id,
            rack,
            box,
            well,
            freezer_id=DEMO_FREEZER,
            volume_ul=volume,
            user_name="system-demo",
        )
        # Keep the denormalized field aligned for UI/exports that use it.
        conn.execute(
            "UPDATE SAMPLES SET storage_location=? WHERE sample_id=?",
            (f"{DEMO_FREEZER}/{rack}/{box}/{well}", sample_id),
        )
        conn.commit()

        rack_counts[rack] += 1
        box_counts[(rack, level)] += 1
        placed += 1

    by_rack = dict(rack_counts)
    by_rack_and_level = {}
    for rack in DEMO_RACKS:
        for level in range(1, DEMO_LEVELS_PER_RACK + 1):
            count = conn.execute(
                """
                SELECT COUNT(*) FROM STORAGE_LOCATIONS
                WHERE freezer_id=? AND rack_id=? AND box_id=?
                """,
                (DEMO_FREEZER, rack, f"{rack}-BOX{level:02d}"),
            ).fetchone()[0]
            by_rack_and_level[f"{rack}-BOX{level:02d}"] = int(count)
    return {
        "placed": placed,
        "already_stored": skipped,
        "by_rack": by_rack,
        "by_rack_and_level": by_rack_and_level,
    }


def load_demo_history(conn):
    """Load W03-W39 history, release it, auto-store demo samples, then run Jan-Aug sponsor delivery."""
    if not _empty_result_state(conn):
        return {
            "status": "SKIPPED",
            "message": "LAB_RESULTS is not empty. Reset the training database first.",
        }

    files = sorted(HISTORY_DIR.glob("*.csv"))
    if not files:
        return {"status": "ERROR", "message": "No weekly demo history CSV files found."}

    total_rows = 0
    batches = 0
    reviewed = 0

    for path in files:
        df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        received = (
            str(df["source_received_datetime"].dropna().iloc[0])
            if "source_received_datetime" in df.columns and not df["source_received_datetime"].dropna().empty
            else None
        )

        batch_id, inserted, skipped = import_lab_results_batch(
            conn, df, "lab_tech1", path.name, sha, received_at=received
        )

        archive_dt = pd.to_datetime(received, utc=True).to_pydatetime() if received else None
        archive_document(
            conn,
            path.read_bytes(),
            path.name,
            stakeholder="Central_Lab_Results",
            direction="INBOUND",
            document_type="CSV",
            created_by="lab_tech1",
            related_id=batch_id,
            description="Historical weekly Central Laboratory source for BLOOD LIMS training.",
            archive_datetime=archive_dt,
            mimetype="text/csv",
        )

        ids = _all_result_ids_for_batch(conn, batch_id)
        if ids:
            mark_technical_validation(conn, ids, "lab_tech1")
            mark_biological_validation(
                conn,
                ids,
                "biologist1",
                "Reviewed and approved",
                "Historical training batch completed and released for sponsor reporting.",
            )
            reviewed += len(ids)

        total_rows += inserted
        batches += 1

    # Auto-store the demo samples AFTER all sample records have been created.
    storage_summary = _auto_store_demo_samples(conn)
    set_setting(conn, "demo_auto_storage", "1")
    set_setting(conn, "demo_storage_racks", ",".join(DEMO_RACKS))
    set_setting(conn, "demo_storage_freezer", DEMO_FREEZER)

    # Sponsor recipient and demo settings.
    set_setting(conn, "notify_emails_sponsor", "sponsor_lph")
    set_setting(conn, "notify_emails_lab", "lab_tech1")
    set_setting(conn, "notify_emails_critical", "biologist1")
    set_setting(conn, "automation_demo_mode", "1")
    set_setting(conn, "vinc_reminder_day", "25")
    set_setting(conn, "demo_history_seeded", "1")
    set_setting(conn, "demo_history_version", "14-WEEKLY-W03-W39-AUTO-STORAGE")

    # Jan-Aug sponsor deliveries via the real automation engine.
    monthly_cutoffs = [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
        date(2026, 7, 31),
        date(2026, 8, 31),
    ]
    monthly_runs = []
    for cutoff in monthly_cutoffs:
        run_id, _results = run_automation_now(
            conn,
            triggered_by="system",
            force=True,
            reference_date=cutoff,
        )
        monthly_runs.append(run_id)

    rack_text = ", ".join(f"{rack}={count}" for rack, count in storage_summary["by_rack"].items())
    log_audit(
        conn,
        "DEMO_DATA",
        "DEMO_HISTORY_LOADED",
        "system",
        comment=(
            f"weekly W03-W39; files={batches}; rows={total_rows}; reviewed={reviewed}; "
            f"auto_storage={storage_summary['placed']}; rack_counts={rack_text}; "
            f"rack_levels={storage_summary['by_rack_and_level']}; sponsor_months=Jan-Aug"
        ),
    )
    return {
        "status": "LOADED",
        "message": (
            f"Loaded W03-W39 ({batches} weekly files, {total_rows} results), "
            f"reviewed historical data, auto-stored {storage_summary['placed']} demo samples "
            f"in R01/A01/B01, and ran Jan-Aug sponsor deliveries."
        ),
        "files": batches,
        "rows": total_rows,
        "reviewed": reviewed,
        "auto_stored": storage_summary["placed"],
        "storage_by_rack": storage_summary["by_rack"],
        "storage_by_rack_and_level": storage_summary["by_rack_and_level"],
        "monthly_runs": monthly_runs,
    }


if __name__ == "__main__":
    print(load_demo_history(get_connection()))
