"""Document archive service for the BLOOD LIMS prototype.

The archive is automatic at the moment a laboratory file is received or an
outbound artefact is created. The UI exposes two logical taxonomies without
adding a new page:

* CRO working archive: ISO year -> ISO week -> stakeholder -> document type
* Sponsor archive: calendar year -> quarter -> month -> stakeholder -> document type

The physical prototype stores artefacts in SQLite. A production implementation
should use validated persistent object/document storage with approved retention.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timezone

import pandas as pd


def _period(dt=None):
    dt = dt or datetime.now(timezone.utc)
    iso = dt.isocalendar()
    return int(iso.year), int(iso.week), f"{iso.year}-W{iso.week:02d}"


def _calendar_period(dt=None):
    dt = dt or datetime.now(timezone.utc)
    year = dt.year
    month = dt.month
    quarter = ((month - 1) // 3) + 1
    return year, quarter, month


def _safe_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("_") or "UNKNOWN"


def logical_archive_path(archive_date, stakeholder, direction, document_type, filename):
    """Return the visible logical folder path used by archive ZIPs."""
    try:
        dt = datetime.fromisoformat(str(archive_date))
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    year, week, week_key = _period(dt)
    cal_year, quarter, month = _calendar_period(dt)
    sh = _safe_part(stakeholder)
    dtype = _safe_part(document_type)
    fn = _safe_part(filename)

    # Sponsor-facing and sponsor-received artefacts are organized around the
    # monthly contractual delivery cycle. This check comes first so that the
    # sponsor receipt copy (LPH_Sponsor / INBOUND) stays in the sponsor tree.
    if stakeholder in {"LPH_Sponsor", "Clinical_Services"} or direction == "OUTBOUND":
        return f"{cal_year}/Q{quarter}/{cal_year}-{month:02d}/{sh}/{dtype}/{fn}"

    # Central Laboratory inbound material is organized around the weekly
    # operating cycle.
    if stakeholder == "Central_Lab_Results" or direction == "INBOUND":
        return f"{year}/W{week:02d}/{sh}/{dtype}/{fn}"

    return f"{year}/W{week:02d}/{sh}/{dtype}/{fn}"


def archive_document(
    conn, content: bytes, filename: str, stakeholder: str, direction: str,
    document_type: str, created_by: str, related_id=None, description=None,
    archive_datetime=None, mimetype=None,
):
    """Archive an artefact automatically and deduplicate identical files."""
    when = archive_datetime or datetime.now(timezone.utc)
    year, week, week_key = _period(when)
    data = bytes(content or b"")
    sha = hashlib.sha256(data).hexdigest()
    row = conn.execute(
        "SELECT archive_id FROM ARCHIVE_ENTRIES "
        "WHERE week_key=? AND stakeholder=? AND filename=? AND sha256=? LIMIT 1",
        (week_key, stakeholder, filename, sha),
    ).fetchone()
    if row:
        return int(row[0])

    cur = conn.execute(
        "INSERT INTO ARCHIVE_ENTRIES "
        "(archive_date, archive_year, iso_week, week_key, stakeholder, direction, "
        "document_type, filename, sha256, content, mimetype, related_id, created_by, created_at, description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            when.date().isoformat(), year, week, week_key, stakeholder, direction,
            document_type, filename, sha, data,
            mimetype or "application/octet-stream", related_id, created_by,
            when.isoformat(timespec="seconds"), description,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_archive(conn, year=None, iso_week=None, stakeholder=None):
    where, params = [], []
    if year is not None:
        where.append("archive_year=?"); params.append(int(year))
    if iso_week is not None:
        where.append("iso_week=?"); params.append(int(iso_week))
    if stakeholder and stakeholder != "All":
        where.append("stakeholder=?"); params.append(stakeholder)
    sql = (
        "SELECT archive_id, archive_date, archive_year, iso_week, week_key, stakeholder, "
        "direction, document_type, filename, sha256, related_id, created_by, created_at, description "
        "FROM ARCHIVE_ENTRIES"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY archive_date DESC, archive_id DESC"
    return pd.read_sql_query(sql, conn, params=params)


def available_archive_years(conn):
    return [int(r[0]) for r in conn.execute("SELECT DISTINCT archive_year FROM ARCHIVE_ENTRIES ORDER BY archive_year DESC").fetchall()]


def available_archive_weeks(conn, year):
    return [int(r[0]) for r in conn.execute(
        "SELECT DISTINCT iso_week FROM ARCHIVE_ENTRIES WHERE archive_year=? ORDER BY iso_week DESC",
        (int(year),),
    ).fetchall()]


def list_sponsor_archive(conn, year=None, quarter=None, month=None, stakeholder=None):
    """List sponsor-facing artefacts using calendar year/quarter/month filters."""
    df = pd.read_sql_query(
        "SELECT archive_id, archive_date, stakeholder, direction, document_type, filename, "
        "sha256, related_id, created_by, created_at, description FROM ARCHIVE_ENTRIES "
        "ORDER BY archive_date DESC, archive_id DESC",
        conn,
    )
    if df.empty:
        return df.assign(quarter=pd.Series(dtype="Int64"), month=pd.Series(dtype="Int64"), logical_path=pd.Series(dtype=str))
    dates = pd.to_datetime(df["archive_date"], errors="coerce")
    df["calendar_year"] = dates.dt.year
    df["quarter"] = ((dates.dt.month - 1) // 3) + 1
    df["month"] = dates.dt.month
    df["logical_path"] = [
        logical_archive_path(d, s, direction, dtype, fn)
        for d, s, direction, dtype, fn in zip(
            df["archive_date"], df["stakeholder"], df["direction"], df["document_type"], df["filename"]
        )
    ]
    # Sponsor view focuses on outbound packages and sponsor receipt copies.
    df = df[df["stakeholder"].isin(["Clinical_Services", "LPH_Sponsor"])]
    if year is not None:
        df = df[df["calendar_year"] == int(year)]
    if quarter is not None:
        df = df[df["quarter"] == int(quarter)]
    if month is not None:
        df = df[df["month"] == int(month)]
    if stakeholder and stakeholder != "All":
        df = df[df["stakeholder"] == stakeholder]
    return df.reset_index(drop=True)


def available_sponsor_years(conn):
    df = list_sponsor_archive(conn)
    return sorted(df["calendar_year"].dropna().astype(int).unique().tolist(), reverse=True) if not df.empty else []


def available_sponsor_quarters(conn, year):
    df = list_sponsor_archive(conn, year=year)
    return sorted(df["quarter"].dropna().astype(int).unique().tolist()) if not df.empty else []


def available_sponsor_months(conn, year, quarter):
    df = list_sponsor_archive(conn, year=year, quarter=quarter)
    return sorted(df["month"].dropna().astype(int).unique().tolist()) if not df.empty else []


def build_archive_bundle(conn, year: int, iso_week: int):
    rows = conn.execute(
        "SELECT archive_date, week_key, stakeholder, direction, document_type, filename, content, "
        "sha256, created_by, created_at, description FROM ARCHIVE_ENTRIES "
        "WHERE archive_year=? AND iso_week=? ORDER BY stakeholder, created_at, archive_id",
        (int(year), int(iso_week)),
    ).fetchall()
    if not rows:
        return None

    root = f"{year}/W{iso_week:02d}"
    buf = io.BytesIO()
    manifest = []
    used = set()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for archive_date, week_key, stakeholder, direction, document_type, filename, content, sha, created_by, created_at, description in rows:
            path = logical_archive_path(archive_date, stakeholder, direction, document_type, filename)
            if path in used:
                path = path.rsplit("/", 1)[0] + f"/{archive_date}_{_safe_part(filename)}"
            used.add(path)
            zf.writestr(path, content or b"")
            manifest.append({
                "path": path, "archive_date": archive_date, "week_key": week_key,
                "stakeholder": stakeholder, "direction": direction,
                "document_type": document_type, "filename": filename, "sha256": sha,
                "created_by": created_by, "created_at": created_at, "description": description,
            })
        zf.writestr(
            f"{root}/manifest.json",
            json.dumps({
                "study": "BLOOD", "archive_root": root, "record_count": len(manifest),
                "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "taxonomy": "ISO_YEAR/ISO_WEEK/stakeholder/document_type",
                "entries": manifest,
                "prototype_notice": "Training prototype archive; production requires validated persistent storage and approved retention controls.",
            }, indent=2, sort_keys=True).encode(),
        )
    return {
        "filename": f"BLOOD_ARCHIVE_{year}_W{iso_week:02d}.zip",
        "bytes": buf.getvalue(), "record_count": len(rows), "root": root,
    }


def build_sponsor_archive_bundle(conn, year: int, quarter: int, month: int | None = None):
    """Build a sponsor-facing archive ZIP using YYYY/Qn/YYYY-MM folders."""
    df = list_sponsor_archive(conn, year=year, quarter=quarter, month=month)
    if df.empty:
        return None

    months = sorted(df["month"].unique().tolist())
    root = f"{int(year)}/Q{int(quarter)}"
    if month is not None:
        root = f"{root}/{int(year)}-{int(month):02d}"
    buf = io.BytesIO()
    manifest = []
    used = set()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for _, row in df.iterrows():
            raw = conn.execute(
                "SELECT content FROM ARCHIVE_ENTRIES WHERE archive_id=?", (int(row["archive_id"]),)
            ).fetchone()
            path = str(row["logical_path"])
            if path in used:
                path = path.rsplit("/", 1)[0] + f"/{row['archive_date']}_{_safe_part(row['filename'])}"
            used.add(path)
            zf.writestr(path, raw[0] if raw else b"")
            manifest.append({
                "path": path, "archive_date": row["archive_date"],
                "stakeholder": row["stakeholder"], "direction": row["direction"],
                "document_type": row["document_type"], "filename": row["filename"],
                "sha256": row["sha256"], "created_by": row["created_by"],
                "created_at": row["created_at"], "description": row["description"],
            })
        zf.writestr(
            f"{root}/manifest.json",
            json.dumps({
                "study": "BLOOD", "archive_root": root, "record_count": len(manifest),
                "months_in_scope": [int(m) for m in months],
                "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "taxonomy": "CALENDAR_YEAR/QUARTER/MONTH/stakeholder/document_type",
                "entries": manifest,
                "prototype_notice": "Training prototype archive; production requires validated persistent storage and approved retention controls.",
            }, indent=2, sort_keys=True).encode(),
        )
    month_label = f"_{int(month):02d}" if month is not None else ""
    return {
        "filename": f"BLOOD_SPONSOR_ARCHIVE_{int(year)}_Q{int(quarter)}{month_label}.zip",
        "bytes": buf.getvalue(), "record_count": len(df), "root": root,
    }
