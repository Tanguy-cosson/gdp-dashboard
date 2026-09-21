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

import pandas as pd
import streamlit as st

from audit import log_audit
from business_logic import compute_oor_flag, select_vinc_for_export
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
from export_package import build_vinc_package
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


def _job_monthly_vinc(conn, reference_date, force=False):
    vinc_day = int(get_setting(conn, "vinc_reminder_day", "25"))
    period = _vinc_period(reference_date)
    if not force and reference_date.day < vinc_day:
        return {"job": "monthly_vinc", "triggered": False, "sent": 0, "detail": f"Monthly cut-off day {vinc_day} not reached."}
    if not force and _vinc_sent_in_period(conn, period):
        return {"job": "monthly_vinc", "triggered": False, "sent": 0, "detail": "VINC package already sent for this period."}

    recipients = _configured_usernames(conn, "notify_emails_sponsor")
    eligible = _build_eligible_vinc(conn, reference_date)
    if eligible.empty:
        cro_recipients = _configured_usernames(conn, "notify_emails_critical")
        subject = "Monthly VINC package not ready"
        body = (
            f"No ACTIVE/REVIEWED VINC result is eligible for the monthly sponsor package at the "
            f"reference date {reference_date.isoformat()}. The package has not been sent."
        )
        sent = send_internal_message_to_many(conn, cro_recipients, subject, body,
            sender_username="system", sender_label="BLOOD LIMS Automation") if cro_recipients else 0
        log_audit(conn, "AUTOMATION", "VINC_PACKAGE_BLOCKED", "system", comment=body)
        return {"job": "monthly_vinc", "triggered": True, "sent": sent, "detail": body}

    if not recipients:
        return {"job": "monthly_vinc", "triggered": True, "sent": 0, "detail": "Eligible package exists, but no sponsor recipient is configured."}

    package = build_vinc_package(eligible, reference_date, "system")
    register_export_package(
        conn, package["package_id"], "VINC_MONTHLY_CRO_TO_SPONSOR", "system", reference_date,
        package["row_count"], package["csv_sha256"], package["package_sha256"], package["filename"]
    )
    subject = f"BLOOD Study — Monthly VINC package — {reference_date.isoformat()}"
    body = (
        f"The monthly BLOOD Study VINC package is ready and has been sent to the configured sponsor mailbox.\n\n"
        f"Cut-off: {reference_date.isoformat()}\nRecords: {package['row_count']}\n"
        f"Package SHA-256: {package['package_sha256']}\n"
        "Only ACTIVE / REVIEWED VINC results on or before the cut-off were included."
    )
    sent = send_internal_message_to_many(
        conn, recipients, subject, body,
        sender_username="system", sender_label="BLOOD LIMS Automation",
        attachment_bytes=package["package_bytes"], attachment_name=package["filename"],
        attachment_mimetype="application/zip",
    )
    smtp = False
    if _smtp_configured():
        smtp = send_secure_report(
            f"[{STUDYID} Study] {subject}", body, recipients,
            package["package_bytes"], package["filename"],
        )
    delivery_ref = f"INTERNAL-MAIL-{uuid.uuid4().hex[:10].upper()}"
    if sent or smtp:
        mark_export_package_sent(conn, package["package_id"], "system", delivery_ref)
        set_setting(conn, "last_vinc_sent_period", period)
    log_audit(conn, "EXPORT_PACKAGES", "EXPORT_VINC_CRO", "system", record_ref=package["package_id"],
              comment=f"automated_monthly_send; cutoff={reference_date}; rows={package['row_count']}; internal={sent}; smtp={smtp}")
    return {
        "job": "monthly_vinc", "triggered": True, "sent": sent, "smtp": smtp,
        "package_id": package["package_id"], "detail": body,
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
    eligible = _build_eligible_vinc(conn, ref)
    sent = _vinc_sent_in_period(conn, _vinc_period(ref))
    vinc_day = int(get_setting(conn, "vinc_reminder_day", "25"))
    return [
        {
            "title": "Weekly central laboratory file",
            "would_trigger": days is None or days >= threshold,
            "detail": "No ingestion recorded." if days is None else f"Last ingestion: {days} day(s) ago; threshold={threshold}.",
            "recipients": _configured_usernames(conn, "notify_emails_lab"),
        },
        {
            "title": "Monthly VINC package",
            "would_trigger": ref.day >= vinc_day and not sent and not eligible.empty,
            "detail": f"Eligible reviewed VINC records: {len(eligible)}; day={ref.day}; configured send day={vinc_day}; already sent={sent}.",
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
    return run_id, results


def check_and_send_reminders(conn):
    """Automatic live check called on app load. No demo overrides."""
    if get_setting(conn, "automation_enabled", "1") != "1":
        return []
    _, results = run_automation_now(conn, triggered_by="system", force=False, reference_date=date.today())
    return results


def get_weekly_ingestion_compliance(conn, n_weeks=8):
    audit_df = read_audit_trail(conn)
    ingestion_df = audit_df[audit_df["action"].isin(["INGESTION_CSV", "INGESTION_HL7"])].copy() if not audit_df.empty else audit_df
    if not ingestion_df.empty:
        ingestion_df["event_timestamp"] = pd.to_datetime(ingestion_df["event_timestamp"], errors="coerce", utc=True).dt.tz_localize(None)
        ingestion_df["week"] = ingestion_df["event_timestamp"].dt.to_period("W-SUN")
    today = pd.Timestamp.now(tz="UTC").tz_localize(None)
    weeks = pd.period_range(end=today.to_period("W-SUN"), periods=n_weeks, freq="W-SUN")
    return pd.DataFrame([
        {"Week": f"{wk.start_time.date()} to {wk.end_time.date()}",
         "File received": "✅" if (not ingestion_df.empty and (ingestion_df["week"] == wk).any()) else "❌",
         "Number of ingestions": int((ingestion_df["week"] == wk).sum()) if not ingestion_df.empty else 0}
        for wk in weeks
    ])


def get_monthly_vinc_compliance(conn, n_months=6):
    audit_df = read_audit_trail(conn)
    actions = ["EXPORT_VINC_CRO", "CONSULTATION_VINC_SPONSOR", "EXPORT_PACKAGE_SENT"]
    vinc_df = audit_df[audit_df["action"].isin(actions)].copy() if not audit_df.empty else audit_df
    if not vinc_df.empty:
        vinc_df["event_timestamp"] = pd.to_datetime(vinc_df["event_timestamp"], errors="coerce", utc=True).dt.tz_localize(None)
        vinc_df["month"] = vinc_df["event_timestamp"].dt.to_period("M")
    today = pd.Timestamp.now(tz="UTC").tz_localize(None)
    months = pd.period_range(end=today.to_period("M"), periods=n_months, freq="M")
    return pd.DataFrame([
        {"Month": str(m),
         "Package sent/consulted": "✅" if (not vinc_df.empty and (vinc_df["month"] == m).any()) else "❌",
         "Number of actions": int((vinc_df["month"] == m).sum()) if not vinc_df.empty else 0}
        for m in months
    ])
