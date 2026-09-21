"""Build traceable VINC export packages with a manifest and hashes."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

import pandas as pd

from constants import STUDYID


def build_vinc_package(vinc_df: pd.DataFrame, cutoff_date, generated_by: str):
    """Build a ZIP containing the official reviewed VINC CSV and a manifest.

    The ZIP hash is deliberately external to the manifest to avoid a circular hash dependency.
    """
    export_columns = [
        "usubjid", "site_id", "visit_code", "visit_date", "test_code", "test_name",
        "result_value", "result_unit", "result_date", "status",
    ]
    missing = [c for c in export_columns if c not in vinc_df.columns]
    if missing:
        raise ValueError(f"Missing VINC export columns: {', '.join(missing)}")

    export_df = vinc_df[export_columns].copy().sort_values(
        ["usubjid", "visit_date", "test_code"], kind="stable"
    )
    csv_bytes = export_df.to_csv(index=False, lineterminator="\n").encode("utf-8")
    csv_sha256 = hashlib.sha256(csv_bytes).hexdigest()
    cutoff = str(cutoff_date)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    package_id = f"EXP-{hashlib.sha256((csv_sha256 + cutoff).encode('utf-8')).hexdigest()[:12].upper()}"
    csv_filename = f"BLOOD_VINC_{cutoff}.csv"
    filename = f"BLOOD_VINC_{cutoff}.zip"
    manifest = {
        "package_id": package_id,
        "study_id": STUDYID,
        "package_type": "VINC_MONTHLY_CRO_TO_SPONSOR",
        "generated_at_utc": generated_at,
        "generated_by": generated_by,
        "cutoff_date": cutoff,
        "eligibility_rule": "visit_code=VINC AND status=REVIEWED AND record_status=ACTIVE AND visit_date<=cutoff_date",
        "record_count": int(len(export_df)),
        "csv_filename": csv_filename,
        "csv_sha256": csv_sha256,
        "prototype_notice": "Training prototype. Production use requires validated infrastructure, controlled transfers, backup/restore, security controls and formal qualification.",
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
        "filename": filename,
        "package_bytes": package_bytes,
        "csv_sha256": csv_sha256,
        "package_sha256": package_sha256,
        "row_count": len(export_df),
        "manifest": manifest,
    }
