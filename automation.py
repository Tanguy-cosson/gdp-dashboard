"""Automation engine designed for Streamlit Community Cloud free-tier constraints.

Execution model:
- automatic check on every application load;
- manual "Run automation now" button for deterministic demonstrations;
- optional demo clock so the weekly/monthly rules can be shown without waiting;
- internal mailbox is the default delivery channel and works without SMTP;
- SMTP is optional and disabled unless configured through Streamlit secrets.

This is not a server-side scheduler. Streamlit Community Cloud can hibernate apps,
so production-grade scheduling should use a dedicated scheduler and a persistent
validated database. The included GitHub Action is therefore only a convenience
for a demo/education deployment.
"""
from __future__ import annotations

import smtplib
import uuid
from datetime import date, datetime, timedelta, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import io
import zipfile
import json
import os
import hashlib

import pandas as pd
import streamlit as st

from audit import log_audit
from archive_service import archive_document
from business_logic import (
    compute_oor_flag, select_vinc_for_export, select_reviewed_sdtm_source, compute_lbnrind
)
from cdisc_export import generate_define_xml, generate_dm_domain
from constants import CRITICAL_FLAG, STUDYID
from db import (
    get_setting,
    list_recent_automation_runs,
    list_users,
    mark_export_package_sent,
    now_utc_iso,
    read_audit_trail,
    read_full_results,
    record_automation_run,
    register_export_package,
    set_setting,
)
from export_package import build_visit_package, build_vinc_package
from mailbox import send_internal_message_to_many


def _smtp_configured():
    try:
        return "smtp" in st.secrets
    except Exception:
        return False


def send_email(subject, body, recipients):
    recipients = [r.strip() for r in recipients if r and r.strip()]
    if not recipients or not _smtp_configured():
        return False
    try:
        cfg = st.secrets["smtp"]
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = cfg["sender"]
        msg["To"] = ", ".join(recipients)
        with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=10) as server:
            server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["sender"], recipients, msg.as_string())
        return True
    except Exception:
        return False


def send_secure_report(subject, body, recipients, attachment_bytes, attachment_filename):
    recipients = [r.strip() for r in recipients if r and r.strip()]
    if not recipients or not _smtp_configured():
        return False
    try:
        cfg = st.secrets["smtp"]
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = cfg["sender"]
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(body))
        part = MIMEApplication(attachment_bytes, Name=attachment_filename)
        part["Content-Disposition"] = f'attachment; filename="{attachment_filename}"'
        msg.attach(part)
        with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=15) as server:
            server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["sender"], recipients, msg.as_string())
        return True
    except Exception:
        return False


def _parse_reference_date(conn, reference_date=None):
    if reference_date is not None:
        return reference_date
    demo_mode = get_setting(conn, "automation_demo_mode", "0") == "1"
    if demo_mode:
        try:
            return date.fromisoformat(get_setting(conn, "automation_demo_date", "2026-06-25"))
        except ValueError:
            return date.today()
    return date.today()


def _parse_audit_datetime(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _days_since_last_ingestion(conn, reference_date):
    audit_df = read_audit_trail(conn)
    if get_setting(conn, "automation_demo_mode", "0") == "1":
        demo_last = get_setting(conn, "automation_demo_last_ingestion_date", "2026-06-18")
        try:
            last_dt = datetime.combine(date.fromisoformat(demo_last), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError:
            last_dt = None
        if last_dt is not None:
            ref = datetime.combine(reference_date, datetime.min.time(), tzinfo=timezone.utc)
            return max(0, (ref - last_dt).days)
    if audit_df.empty:
        return None
    rows = audit_df[audit_df["action"].isin(["INGESTION_CSV", "INGESTION_HL7"])]
    if rows.empty:
        return None
    last_dt = _parse_audit_datetime(rows.iloc[0]["event_timestamp"])
    ref = datetime.combine(reference_date, datetime.min.time(), tzinfo=timezone.utc)
    return max(0, (ref - last_dt).days)


def _vinc_period(reference_date):
    return reference_date.strftime("%Y-%m")


def _vinc_sent_in_period(conn, period):
    row = conn.execute(
        "SELECT 1 FROM EXPORT_PACKAGES WHERE package_type='VINC_MONTHLY_CRO_TO_SPONSOR' "
        "AND cutoff_date LIKE ? AND status='SENT' LIMIT 1",
        (f"{period}%",),
    ).fetchone()
    if row:
        return True
    audit_df = read_audit_trail(conn)
    if audit_df.empty:
        return False
    sent = audit_df[
        (audit_df["action"] == "EXPORT_PACKAGE_SENT")
        & audit_df["event_timestamp"].astype(str).str.startswith(period)
    ]
    return not sent.empty


def _configured_usernames(conn, setting_key):
    raw = (get_setting(conn, setting_key, "") or "").split(",")
    return [u.strip() for u in raw if u.strip()]


def _job_weekly_ingestion(conn, reference_date, force=False):
    threshold = int(get_setting(conn, "reminder_ingestion_days", "7"))
    days = _days_since_last_ingestion(conn, reference_date)
    due = days is None or days >= threshold
    recipients = _configured_usernames(conn, "notify_emails_lab")
    current_period = reference_date.strftime("%Y-W%W")
    already_sent = get_setting(conn, "last_weekly_reminder_period", "") == current_period
    if not due:
        return {"job": "weekly_ingestion", "triggered": False, "sent": 0, "detail": f"Last ingestion: {days} day(s) ago."}
    if already_sent:
        return {"job": "weekly_ingestion", "triggered": False, "sent": 0, "detail": "Weekly reminder already sent for this period."}
    if not recipients:
        return {"job": "weekly_ingestion", "triggered": True, "sent": 0, "detail": "Due, but no recipients are configured."}
    subject = "Weekly central laboratory file overdue"
    body = (
        f"The BLOOD Study has not received a central laboratory file within the configured "
        f"{threshold}-day interval. Reference date: {reference_date.isoformat()}. "
        "Please upload the weekly laboratory file and document the action in the Audit Trail."
    )
    sent = send_internal_message_to_many(conn, recipients, subject, body,
        sender_username="system", sender_label="BLOOD LIMS Automation")
    smtp = send_email(f"[{STUDYID} Study] {subject}", body, recipients)
    if sent or smtp:
        set_setting(conn, "last_weekly_reminder_period", current_period)
    log_audit(conn, "AUTOMATION", "REMINDER_SENT", "system",
              comment=f"weekly_ingestion; recipients={recipients}; internal={sent}; smtp={smtp}")
    return {"job": "weekly_ingestion", "triggered": True, "sent": sent, "smtp": smtp, "detail": body}


def _build_eligible_vinc(conn, cutoff_date):
    df = read_full_results(conn)
    if df.empty:
        return df
    all_vinc = df[df["visit_code"] == "VINC"].copy()
    if all_vinc.empty:
        return all_vinc
    all_vinc = compute_oor_flag(all_vinc)
    return select_vinc_for_export(all_vinc, cutoff_date)


def _month_start(reference_date):
    return date(reference_date.year, reference_date.month, 1)


def _build_eligible_monthly_visit(conn, visit_code, period_start, cutoff_date):
    """Select active/reviewed results that actually occurred in the delivery month."""
    df = read_full_results(conn)
    if df.empty:
        return df
    visit_code = str(visit_code).upper().strip()
    df = df[df["visit_code"].astype(str).str.upper() == visit_code].copy()
    if df.empty:
        return df
    dates = pd.to_datetime(df["visit_date"], errors="coerce").dt.date
    return df[
        (df["status"] == "REVIEWED")
        & (df["record_status"] == "ACTIVE")
        & dates.notna()
        & (dates >= period_start)
        & (dates <= cutoff_date)
    ].copy()


def _build_monthly_sdtm_package(conn, period_start, cutoff_date, generated_by="system"):
    """Build a month-scoped sponsor SDTM package (LB + DM + define.xml)."""
    source = select_reviewed_sdtm_source(read_full_results(conn))
    if source.empty:
        return None
    dates = pd.to_datetime(source["visit_date"], errors="coerce").dt.date
    source = source[
        dates.notna()
        & (dates >= period_start)
        & (dates <= cutoff_date)
        & source["record_status"].eq("ACTIVE")
        & source["status"].eq("REVIEWED")
    ].copy()
    if source.empty:
        return None

    source = source.sort_values(["usubjid", "visit_num", "test_code"], kind="stable")
    source["LBSEQ"] = source.groupby("usubjid").cumcount() + 1
    sdtm_lb = pd.DataFrame({
        "STUDYID": STUDYID, "DOMAIN": "LB", "USUBJID": source["usubjid"],
        "LBSEQ": source["LBSEQ"], "LBTESTCD": source["test_code"],
        "LBTEST": source["test_name"], "LBORRES": source["result_value"].astype(str),
        "LBORRESU": source["result_unit"], "LBSTRESN": source["result_value"],
        "LBSTRESU": source["result_unit"],
        "LBNRIND": [compute_lbnrind(v, lo, hi) for v, lo, hi in zip(source["result_value"], source["ref_low"], source["ref_high"])],
        "LBBLFL": source["visit_code"].apply(lambda v: "Y" if v == "VINC" else ""),
        "VISITNUM": source["visit_num"], "VISIT": source["visit_code"], "LBDTC": source["result_date"],
    })
    sdtm_dm = generate_dm_domain(conn)
    if not sdtm_dm.empty:
        wanted = set(source["usubjid"].astype(str))
        sdtm_dm = sdtm_dm[sdtm_dm["USUBJID"].astype(str).isin(wanted)].copy()

    define_bytes = generate_define_xml()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"SDTM_LB_BLOOD_{cutoff_date}.csv", sdtm_lb.to_csv(index=False, lineterminator="\n"))
        if not sdtm_dm.empty:
            zf.writestr(f"SDTM_DM_BLOOD_{cutoff_date}.csv", sdtm_dm.to_csv(index=False, lineterminator="\n"))
        zf.writestr("define.xml", define_bytes)
        zf.writestr("delivery_scope.txt", f"Study: {STUDYID}\nPeriod start: {period_start}\nCut-off: {cutoff_date}\nDomains: LB, DM, define.xml\n")
    package_bytes = buf.getvalue()
    # The package content hash is recorded as the authoritative integrity value.
    import hashlib
    content_sha = hashlib.sha256(package_bytes).hexdigest()
    package_id = f"SDTM-{content_sha[:12].upper()}"
    filename = f"BLOOD_CDIC_SDTM_{cutoff_date}.zip"
    return {
        "package_id": package_id,
        "package_type": "CDISC_SDTM_MONTHLY_CRO_TO_SPONSOR",
        "filename": filename,
        "package_bytes": package_bytes,
        "package_sha256": content_sha,
        "csv_sha256": content_sha,
        "row_count": int(len(sdtm_lb)),
        "lb_count": int(len(sdtm_lb)),
        "dm_count": int(len(sdtm_dm)),
    }


def _job_monthly_vinc(conn, reference_date, force=False):
    """Monthly CRO→Sponsor delivery: VINC + V1 + V2 + month-scoped CDISC SDTM."""
    vinc_day = int(get_setting(conn, "vinc_reminder_day", "25"))
    period = _vinc_period(reference_date)
    if not force and reference_date.day < vinc_day:
        return {"job": "monthly_sponsor_delivery", "triggered": False, "sent": 0,
                "detail": f"Monthly cut-off day {vinc_day} not reached."}
    if not force and _vinc_sent_in_period(conn, period):
        return {"job": "monthly_sponsor_delivery", "triggered": False, "sent": 0,
                "detail": "Monthly sponsor delivery already sent for this period."}

    recipients = _configured_usernames(conn, "notify_emails_sponsor")
    if not recipients:
        return {"job": "monthly_sponsor_delivery", "triggered": True, "sent": 0,
                "detail": "Sponsor delivery is due, but no sponsor recipient is configured."}

    period_start = _month_start(reference_date)
    packages = []
    visit_counts = {}

    for visit_code in ("VINC", "V1", "V2"):
        eligible = _build_eligible_monthly_visit(conn, visit_code, period_start, reference_date)
        visit_counts[visit_code] = len(eligible)
        if eligible.empty:
            continue
        package = build_visit_package(eligible, visit_code, reference_date, "system", period_start=period_start)
        archive_document(
            conn, package["package_bytes"], package["filename"],
            stakeholder="Clinical_Services", direction="OUTBOUND", document_type=f"{visit_code}_PACKAGE",
            created_by="system", related_id=package["package_id"],
            description=f"Monthly {visit_code} sponsor delivery for {period}.",
            archive_datetime=datetime.combine(reference_date, datetime.min.time(), tzinfo=timezone.utc),
            mimetype="application/zip",
        )
        register_export_package(
            conn, package["package_id"], f"{visit_code}_MONTHLY_CRO_TO_SPONSOR", "system", reference_date,
            package["row_count"], package["csv_sha256"], package["package_sha256"], package["filename"]
        )
        packages.append((visit_code, package))

    sdtm_package = _build_monthly_sdtm_package(conn, period_start, reference_date, "system")
    if sdtm_package:
        archive_document(
            conn, sdtm_package["package_bytes"], sdtm_package["filename"],
            stakeholder="Clinical_Services", direction="OUTBOUND", document_type="CDISC_SDTM",
            created_by="system", related_id=sdtm_package["package_id"],
            description=f"Monthly CDISC SDTM sponsor delivery for {period} (LB, DM, define.xml).",
            archive_datetime=datetime.combine(reference_date, datetime.min.time(), tzinfo=timezone.utc),
            mimetype="application/zip",
        )
        register_export_package(
            conn, sdtm_package["package_id"], "CDISC_SDTM_MONTHLY_CRO_TO_SPONSOR", "system", reference_date,
            sdtm_package["row_count"], sdtm_package["csv_sha256"], sdtm_package["package_sha256"], sdtm_package["filename"]
        )
        packages.append(("CDISC_SDTM", sdtm_package))

    if not packages:
        cro_recipients = _configured_usernames(conn, "notify_emails_critical")
        body = (
            f"No ACTIVE/REVIEWED VINC, V1, V2 or SDTM data is eligible for the monthly sponsor delivery "
            f"for {period} at cut-off {reference_date.isoformat()}. No sponsor package was sent."
        )
        sent = send_internal_message_to_many(
            conn, cro_recipients, "Monthly sponsor delivery not ready", body,
            sender_username="system", sender_label="BLOOD LIMS Automation"
        ) if cro_recipients else 0
        log_audit(conn, "AUTOMATION", "SPONSOR_DELIVERY_BLOCKED", "system", comment=body)
        return {"job": "monthly_sponsor_delivery", "triggered": True, "sent": sent, "detail": body}

    # Build one mailbox bundle from the component packages so the sponsor receives
    # the complete monthly delivery in a single controlled message.
    mailbox_buf = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(mailbox_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for label, package in packages:
            zf.writestr(f"{label}/{package['filename']}", package["package_bytes"])
        manifest = {
            "study": STUDYID,
            "delivery_period": period,
            "cutoff_date": reference_date.isoformat(),
            "components": [label for label, _ in packages],
            "visit_record_counts": visit_counts,
            "sdtm_included": bool(sdtm_package),
        }
        zf.writestr("delivery_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    mailbox_bytes = mailbox_buf.getvalue()
    mailbox_name = f"BLOOD_SPONSOR_DELIVERY_{period}.zip"

    counts_text = ", ".join(f"{k}={v}" for k, v in visit_counts.items())
    body = (
        f"The monthly BLOOD Study sponsor delivery for {period} has been sent to the sponsor mailbox.\n\n"
        f"Cut-off: {reference_date.isoformat()}\n"
        f"Components: {', '.join(label for label, _ in packages)}\n"
        f"Visit results in period: {counts_text}\n"
        f"SDTM included: {'YES' if sdtm_package else 'NO'}\n"
        "Only ACTIVE / REVIEWED records occurring within the month and on/before the cut-off are included."
    )

    sent = send_internal_message_to_many(
        conn, recipients, f"BLOOD Study — Monthly Sponsor Delivery — {period}", body,
        sender_username="system", sender_label="BLOOD LIMS Automation",
        attachment_bytes=mailbox_bytes, attachment_name=mailbox_name,
        attachment_mimetype="application/zip",
    )
    smtp = False
    if _smtp_configured():
        smtp = send_secure_report(
            f"[{STUDYID} Study] Monthly Sponsor Delivery — {period}", body, recipients,
            mailbox_bytes, mailbox_name,
        )

    delivery_ref = f"INTERNAL-MAIL-{uuid.uuid4().hex[:10].upper()}"
    if sent or smtp:
        for _, package in packages:
            mark_export_package_sent(conn, package["package_id"], "system", delivery_ref)
        set_setting(conn, "last_vinc_sent_period", period)

        # Sponsor-side receipt remains stored internally but is not exposed by the
        # sponsor allow-list; sponsor users see the actual outbound deliverables only.
        archive_document(
            conn, mailbox_bytes, mailbox_name,
            stakeholder="LPH_Sponsor", direction="INBOUND", document_type="SPONSOR_RECEIPT",
            created_by="system", related_id=delivery_ref,
            description="Sponsor-side receipt copy for the complete monthly delivery.",
            archive_datetime=datetime.combine(reference_date, datetime.min.time(), tzinfo=timezone.utc),
            mimetype="application/zip",
        )

    log_audit(
        conn, "EXPORT_PACKAGES", "EXPORT_MONTHLY_SPONSOR", "system", record_ref=delivery_ref,
        comment=f"period={period}; components={[label for label, _ in packages]}; counts={visit_counts}; internal={sent}; smtp={smtp}"
    )
    return {
        "job": "monthly_sponsor_delivery", "triggered": True, "sent": sent, "smtp": smtp,
        "package_id": delivery_ref, "detail": body,
    }

def _job_critical_pending(conn, reference_date, force=False):
    df = read_full_results(conn)
    if df.empty:
        return {"job": "critical_pending", "triggered": False, "sent": 0, "detail": "No results."}
    flagged = compute_oor_flag(df)
    unresolved = flagged[(flagged["Alerte"] == CRITICAL_FLAG) & (flagged["status"] != "REVIEWED")]
    if unresolved.empty:
        return {"job": "critical_pending", "triggered": False, "sent": 0, "detail": "No unresolved critical results."}
    signature_source = "|".join(f"{int(rid)}:{status}" for rid, status in unresolved[["result_id", "status"]].sort_values("result_id").itertuples(index=False, name=None))
    alert_signature = uuid.uuid5(uuid.NAMESPACE_URL, signature_source).hex
    if get_setting(conn, "last_critical_alert_signature", "") == alert_signature:
        return {"job": "critical_pending", "triggered": False, "sent": 0, "detail": "This unresolved critical-result set was already alerted."}
    recipients = _configured_usernames(conn, "notify_emails_critical")
    n = len(unresolved)
    patients = unresolved["usubjid"].nunique()
    subject = f"CRITICAL laboratory values pending review ({n})"
    body = (
        f"{n} critical laboratory result(s) across {patients} patient(s) remain unresolved in the BLOOD Study. "
        "Immediate biological review is required according to the study procedure."
    )
    sent = send_internal_message_to_many(conn, recipients, subject, body,
        sender_username="system", sender_label="BLOOD LIMS Automation") if recipients else 0
    archive_document(conn, body.encode("utf-8"), f"critical_alert_{reference_date.isoformat()}.txt",
                     stakeholder="Clinical_Services", direction="INTERNAL",
                     document_type="AUTOMATION_MESSAGE", created_by="system",
                     description="Critical result alert generated by automation.", mimetype="text/plain")
    smtp = send_email(f"[{STUDYID} Study] {subject}", body, recipients)
    if sent or smtp:
        set_setting(conn, "last_critical_alert_signature", alert_signature)
    log_audit(conn, "AUTOMATION", "CRITICAL_ALERT_SENT", "system",
              comment=f"count={n}; patients={patients}; internal={sent}; smtp={smtp}")
    return {"job": "critical_pending", "triggered": True, "sent": sent, "smtp": smtp, "detail": body}


def preview_automation(conn, reference_date=None):
    ref = _parse_reference_date(conn, reference_date)
    days = _days_since_last_ingestion(conn, ref)
    threshold = int(get_setting(conn, "reminder_ingestion_days", "7"))
    df = read_full_results(conn)
    critical_count = 0
    critical_patients = 0
    if not df.empty:
        flagged = compute_oor_flag(df)
        unresolved = flagged[(flagged["Alerte"] == CRITICAL_FLAG) & (flagged["status"] != "REVIEWED")]
        critical_count = len(unresolved)
        critical_patients = unresolved["usubjid"].nunique()
    period = _vinc_period(ref)
    sent = _vinc_sent_in_period(conn, period)
    vinc_day = int(get_setting(conn, "vinc_reminder_day", "25"))
    start = _month_start(ref)
    monthly_counts = {vc: len(_build_eligible_monthly_visit(conn, vc, start, ref)) for vc in ("VINC", "V1", "V2")}
    monthly_sdtm = _build_monthly_sdtm_package(conn, start, ref) is not None
    monthly_ready = any(monthly_counts.values()) or monthly_sdtm
    return [
        {
            "title": "Weekly central laboratory file",
            "would_trigger": days is None or days >= threshold,
            "detail": "No ingestion recorded." if days is None else f"Last ingestion: {days} day(s) ago; threshold={threshold}.",
            "recipients": _configured_usernames(conn, "notify_emails_lab"),
        },
        {
            "title": "Monthly sponsor delivery",
            "would_trigger": ref.day >= vinc_day and not sent and monthly_ready,
            "detail": f"Period={period}; VINC={monthly_counts['VINC']}; V1={monthly_counts['V1']}; V2={monthly_counts['V2']}; SDTM={monthly_sdtm}; already sent={sent}.",
            "recipients": _configured_usernames(conn, "notify_emails_sponsor"),
        },
        {
            "title": "Critical result alert",
            "would_trigger": critical_count > 0,
            "detail": f"{critical_count} critical result(s) across {critical_patients} patient(s).",
            "recipients": _configured_usernames(conn, "notify_emails_critical"),
        },
    ]


def run_automation_now(conn, triggered_by="manual", force=False, reference_date=None):
    """Execute all jobs once and record an automation run.

    `force=True` is intended for controlled demonstrations only: it bypasses the
    monthly/day and daily throttle checks, but it never bypasses data eligibility rules.
    """
    ref = _parse_reference_date(conn, reference_date)
    run_id = f"RUN-{uuid.uuid4().hex[:12].upper()}"
    started = now_utc_iso()
    mode = "DEMO_FORCED" if force else ("DEMO" if get_setting(conn, "automation_demo_mode", "0") == "1" else "LIVE")
    results = [
        _job_weekly_ingestion(conn, ref, force=force),
        _job_monthly_vinc(conn, ref, force=force),
        _job_critical_pending(conn, ref, force=force),
    ]
    outcome = "COMPLETED"
    if any(r.get("triggered") and r.get("sent", 0) == 0 and not r.get("detail", "").endswith("No results.") for r in results):
        outcome = "COMPLETED_WITH_WARNINGS"
    details = "\n".join(f"{r['job']}: triggered={r.get('triggered')}; sent={r.get('sent',0)}; detail={r.get('detail','')}" for r in results)
    record_automation_run(
        conn, run_id, triggered_by, ref, mode, outcome, details=details,
        started_at=started, finished_at=now_utc_iso(),
    )
    set_setting(conn, "automation_last_run_id", run_id)
    log_audit(conn, "AUTOMATION_RUNS", "AUTOMATION_RUN_COMPLETED", triggered_by, record_ref=run_id,
              comment=details)
    report = (f"BLOOD Study Automation Run\nRun ID: {run_id}\nReference date: {ref.isoformat()}\n"
              f"Triggered by: {triggered_by}\nMode: {mode}\nOutcome: {outcome}\n\n{details}\n")
    archive_document(conn, report.encode("utf-8"), f"{run_id}.txt",
                     stakeholder="BLOOD_LIMS_System", direction="INTERNAL",
                     document_type="AUTOMATION_RUN_REPORT", created_by=triggered_by, related_id=run_id,
                     description="Automation execution report retained in the weekly logical archive.", mimetype="text/plain")
    return run_id, results


def check_and_send_reminders(conn):
    """Automatic live check called on app load. No demo overrides."""
    if get_setting(conn, "automation_enabled", "1") != "1":
        return []
    _, results = run_automation_now(conn, triggered_by="system", force=False, reference_date=date.today())
    return results


def get_weekly_ingestion_compliance(conn, n_weeks=8):
    """Show weekly receipt compliance using the business receipt timestamp.

    For the training/demo data this timestamp is carried by IMPORT_BATCHES.received_at
    from the CSV ``source_received_datetime``. This keeps historical weeks aligned
    with the actual simulated laboratory reception week instead of the audit-row
    creation time.
    """
    rows = conn.execute(
        "SELECT received_at FROM IMPORT_BATCHES "
        "WHERE status = 'ACCEPTED' AND received_at IS NOT NULL "
        "ORDER BY received_at"
    ).fetchall()
    receipt_dates = pd.to_datetime(
        [r[0] for r in rows], errors="coerce", utc=True
    ).tz_localize(None) if rows else pd.DatetimeIndex([])

    today = pd.Timestamp.now(tz="UTC").tz_localize(None)
    weeks = pd.period_range(end=today.to_period("W-SUN"), periods=n_weeks, freq="W-SUN")

    return pd.DataFrame([
        {
            "Week": f"{wk.start_time.date()} to {wk.end_time.date()}",
            "File received": "✅" if any(receipt_dates.to_period("W-SUN") == wk) else "❌",
            "Number of ingestions": int((receipt_dates.to_period("W-SUN") == wk).sum()),
        }
        for wk in weeks
    ])


def get_monthly_vinc_compliance(conn, n_months=6):
    """Show monthly CRO→Sponsor compliance using the package cut-off month.

    SENT VINC_MONTHLY_CRO_TO_SPONSOR packages are matched on EXPORT_PACKAGES.cutoff_date,
    which is the contractual business period represented by the package. This lets
    historical/demo packages remain green in their actual month even when the
    database row was created later.
    """
    rows = conn.execute(
        "SELECT cutoff_date FROM EXPORT_PACKAGES "
        "WHERE package_type='VINC_MONTHLY_CRO_TO_SPONSOR' "
        "AND status='SENT' AND cutoff_date IS NOT NULL "
        "ORDER BY cutoff_date"
    ).fetchall()
    cutoff_dates = pd.to_datetime(
        [r[0] for r in rows], errors="coerce"
    ) if rows else pd.DatetimeIndex([])

    today = pd.Timestamp.now(tz="UTC").tz_localize(None)
    months = pd.period_range(end=today.to_period("M"), periods=n_months, freq="M")

    return pd.DataFrame([
        {
            "Month": str(m),
            "Package sent/consulted": "✅" if any(cutoff_dates.to_period("M") == m) else "❌",
            "Number of actions": int((cutoff_dates.to_period("M") == m).sum()),
        }
        for m in months
    ])
