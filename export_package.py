"""Traceable sponsor delivery packages for BLOOD Study.

This module provides one generic builder for the three protocol visits:
VINC, V1 and V2.  The same controlled eligibility pattern is used for each
visit so that the sponsor only receives active, biologically reviewed records
on or before the selected cut-off date.
"""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
import zipfile

import pandas as pd

from constants import STUDYID


EXPORT_COLUMNS = [
    "usubjid", "site_id", "visit_code", "visit_date", "test_code", "test_name",
    "result_value", "result_unit", "result_date", "status",
]


def build_visit_package(visit_df: pd.DataFrame, visit_code: str, cutoff_date, generated_by: str, period_start=None):
    """Build a ZIP containing the official reviewed package for VINC/V1/V2.

    ``visit_df`` must already contain the records selected by the caller.
    The package itself carries the business eligibility rule in its manifest.
    """
    visit_code = str(visit_code).upper().strip()
    if visit_code not in {"VINC", "V1", "V2"}:
        raise ValueError("visit_code must be VINC, V1 or V2")

    missing = [c for c in EXPORT_COLUMNS if c not in visit_df.columns]
    if missing:
        raise ValueError(f"Missing export columns: {', '.join(missing)}")

    export_df = visit_df[visit_df["visit_code"].astype(str).str.upper() == visit_code].copy()
    export_df = export_df[EXPORT_COLUMNS].sort_values(
        ["usubjid", "visit_date", "test_code"], kind="stable"
    )

    csv_bytes = export_df.to_csv(index=False, lineterminator="\n").encode("utf-8")
    csv_sha256 = hashlib.sha256(csv_bytes).hexdigest()
    cutoff = str(cutoff_date)
    period_start_str = str(period_start) if period_start is not None else None
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    package_type = f"{visit_code}_CRO_TO_SPONSOR"
    package_id = f"EXP-{hashlib.sha256((visit_code + csv_sha256 + cutoff).encode('utf-8')).hexdigest()[:12].upper()}"
    csv_filename = f"BLOOD_{visit_code}_{cutoff}.csv"
    filename = f"BLOOD_{visit_code}_{cutoff}.zip"

    manifest = {
        "package_id": package_id,
        "study_id": STUDYID,
        "package_type": package_type,
        "generated_at_utc": generated_at,
        "generated_by": generated_by,
        "visit_code": visit_code,
        "cutoff_date": cutoff,
        "period_start": period_start_str,
        "eligibility_rule": (
            f"visit_code={visit_code} AND status=REVIEWED AND record_status=ACTIVE "
            + ("AND period_start<=visit_date<=cutoff_date" if period_start_str else "AND visit_date<=cutoff_date")
        ),
        "record_count": int(len(export_df)),
        "csv_filename": csv_filename,
        "csv_sha256": csv_sha256,
        "prototype_notice": (
            "Training prototype. Production use requires validated infrastructure, "
            "controlled transfers, backup/restore, security controls and formal qualification."
        ),
    }
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_filename, csv_bytes)
        zf.writestr("manifest.json", manifest_bytes)
    package_bytes = buffer.getvalue()
    package_sha256 = hashlib.sha256(package_bytes).hexdigest()

    return {
        "package_id": package_id,
        "package_type": package_type,
        "filename": filename,
        "package_bytes": package_bytes,
        "csv_sha256": csv_sha256,
        "package_sha256": package_sha256,
        "row_count": len(export_df),
        "manifest": manifest,
    }


def build_vinc_package(vinc_df: pd.DataFrame, cutoff_date, generated_by: str):
    """Backward-compatible VINC wrapper retained for existing automation."""
    return build_visit_package(vinc_df, "VINC", cutoff_date, generated_by)
