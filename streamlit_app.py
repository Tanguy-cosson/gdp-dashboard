import hashlib
import io
import sqlite3
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from audit import log_audit
from auth import (generate_temp_password, handle_password_reset_flow, hash_password,
                   sidebar_user_identification, validate_password_complexity)
from automation import (check_and_send_reminders, get_monthly_vinc_compliance,
                        get_weekly_ingestion_compliance, preview_automation, run_automation_now,
                        send_secure_report)
from barcode_utils import (decode_barcode_from_image, generate_barcode_png,
                            generate_sample_label_pdf, zbar_available)
from business_logic import (build_patients_matrix, compute_lbnrind, compute_oor_flag,
                             compute_tat_hours, select_reviewed_sdtm_source, select_vinc_for_export,
                             validate_ingestion_dataframe)
from cdisc_export import generate_define_xml, generate_dm_domain
from constants import (ALL_ROLES, CRITICAL_FLAG, ESIGNATURE_LEGAL_NOTICE, NORMAL_FLAG,
                        OOR_FLAG, PAGE_ICONS, PAGE_PERMISSIONS, ROLE_LABELS,
                        SIGNATURE_REASONS, STUDYID)
from db import (DB_PATH, add_storage_location, admin_reset_password, count_by_status,
                create_user, find_sample_by_barcode, get_all_current_storage_locations,
                get_connection, get_current_storage_location, get_or_create_patient,
                get_or_create_sample, get_or_create_site, get_or_create_visit,
                get_samples_pending_labels, get_samples_without_storage, get_setting,
                get_usubjids_for_results, import_lab_results_batch, insert_lab_result, insert_remark, list_users,
                mark_biological_validation, mark_labels_printed, mark_technical_validation,
                create_result_correction, read_audit_trail, read_full_results, read_remarks,
                read_result_history, set_setting, set_user_active, set_user_role, void_result,
                username_exists)
from export_package import build_vinc_package
from gdpr import anonymize_patient, export_patient_data, get_all_consent, record_consent
from hl7_import import parse_oru_r01, parse_ref_range
from mailbox import (count_unread, get_inbox, get_message, list_active_usernames,
                      mark_as_read, send_internal_message, send_internal_message_to_many)
from pdf_reports import generate_patient_pdf_report, generate_vinc_pdf_report
from ui import _html, inject_custom_css, kpi_card, render_landing_page, render_top_banner
from workflow_viz import render_pipeline_svg, render_status_stepper, render_storage_map_html

st.set_page_config(page_title="BLOOD Study LIMS", page_icon="🩸", layout="wide")


# =====================================================================
# RAPPELS (bannières visuelles — distinctes des e-mails automatiques
# gérés par automation.py)
# =====================================================================
def days_since_last_action(conn, actions):
    placeholders = ",".join("?" for _ in actions)
    cur = conn.execute(
        f"SELECT MAX(event_timestamp) FROM AUDIT_TRAIL WHERE action IN ({placeholders})",
        actions,
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    from datetime import datetime, timezone
    last_dt = datetime.fromisoformat(row[0])
    now = datetime.now(timezone.utc) if last_dt.tzinfo else datetime.now()
    return (now - last_dt).days


def render_central_lab_reminder(conn):
    days = days_since_last_action(conn, ["INGESTION_CSV"])
    if days is None:
        st.warning("⏰ No filing has yet been made. The protocol provides for a filing "
                   "every week — please submit the first file.")
    elif days >= 7:
        st.warning(f"⏰ Reminder: the last submission was {days} days ago. "
                   "Please upload the new weekly file.")
    else:
        st.success(f"✅ Last update {days} days ago. You are up to date.")


def render_sponsor_reminder(conn):
    cur = conn.execute(
        "SELECT MAX(event_timestamp) FROM AUDIT_TRAIL "
        "WHERE action IN ('EXPORT_VINC_CRO','CONSULTATION_VINC_SPONSOR') "
        "AND strftime('%Y-%m', event_timestamp) = strftime('%Y-%m', 'now')"
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        st.warning("⏰ This month's VINC extract has not yet been downloaded "
                   "nor has it been consulted. Please remember to forward it.")
    else:
        st.success("✅ VINC extract already sent/viewed this month.")


def render_critical_alert_banner(conn):
    df = read_full_results(conn)
    if df.empty:
        return
    df = compute_oor_flag(df)
    unresolved_critical = df[(df["Alerte"] == CRITICAL_FLAG) & (df["status"] != "REVIEWED")]
    if not unresolved_critical.empty:
        n = len(unresolved_critical)
        patients = unresolved_critical["usubjid"].nunique()
        st.markdown(_html(f"""
        <div class="critical-banner">
            🔴 {n} CRITICAL value(s) pending validation across {patients} patient(s) —
            immediate attention required.
        </div>
        """), unsafe_allow_html=True)


# =====================================================================
# PAGES — LABORATORY TECHNICIAN
# =====================================================================
def page_ingestion(conn, user_name):
    st.title("DATA INGESTION")
    render_central_lab_reminder(conn)
    st.caption("This module only allows you to ADD results. Optional CSV columns: "
               "ref_low / ref_high (normal range), critical_low / critical_high (panic "
               "values), sample_type (WHOLE_BLOOD/SERUM/PLASMA, default SERUM), "
               "collection_datetime.")

    uploaded_file = st.file_uploader("CSV file to import", type=["csv"])
    if uploaded_file is None:
        return

    try:
        df = pd.read_csv(uploaded_file, sep=None, engine="python", encoding="utf-8-sig")
    except Exception as e:
        st.error(f"Unable to read this file as CSV: {e}")
        return

    st.dataframe(df.head(10), use_container_width=True)

    is_valid, errors = validate_ingestion_dataframe(df)
    if not is_valid:
        st.error(f"❌ {len(errors)} error(s) found — nothing has been imported. "
                  "Fix the file and re-upload it (all-or-nothing import, to avoid "
                  "inconsistent partial data in a clinical database).")
        with st.expander("Show validation errors", expanded=True):
            for err in errors[:200]:
                st.write(f"- {err}")
            if len(errors) > 200:
                st.write(f"... and {len(errors) - 200} more.")
        return

    st.success(f"✅ {len(df)} line(s) passed validation and are ready to import.")

    has_ref = "ref_low" in df.columns and "ref_high" in df.columns
    has_crit = "critical_low" in df.columns and "critical_high" in df.columns
    has_sample_type = "sample_type" in df.columns
    has_collection = "collection_datetime" in df.columns

    if st.button("Import into the database", disabled=not user_name):
        progress = st.progress(0, text="Preparing import...")
        raw_bytes = uploaded_file.getvalue()
        source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        progress.progress(10, text="File hashed — starting atomic transaction...")
        try:
            batch_id, inserted, skipped = import_lab_results_batch(
                conn, df, user_name, uploaded_file.name or "uploaded.csv", source_sha256
            )
            progress.progress(100, text="Import completed")
        except Exception as e:
            st.error(
                f"❌ Import rejected — no partial business data was committed. "
                f"Batch preserved in the audit database. Error: {e}"
            )
            return

        st.success(
            f"✅ Batch {batch_id}: {inserted} line(s) imported, {skipped} identical duplicate(s) skipped. "
            "Initial status is PENDING (technical validation required)."
        )
        st.rerun()


def page_sample_labels(conn, user_name):
    """Physical sample traceability: barcode label generation for received samples."""
    st.title("SAMPLE LABELS (barcode printing)")
    st.caption("Generate Code128 barcode labels (50×25mm) for physical sample tubes. "
                "Each barcode encodes the sample_id — scan it later from 'Sample Scan' "
                "to instantly pull up the chain of custody and results.")

    df = get_samples_pending_labels(conn)
    if df.empty:
        st.info("No samples in the database yet.")
        return

    only_unprinted = st.checkbox("Show only samples without a printed label yet", value=True)
    display_df = df[df["label_printed"] == 0] if only_unprinted else df

    if display_df.empty:
        st.success("✅ All samples already have a printed label.")
        return

    display_df = display_df.copy()
    display_df.insert(0, "Select", False)
    edited = st.data_editor(
        display_df[["Select", "sample_id", "barcode_value", "usubjid", "visit_code",
                    "sample_type", "site_name", "label_printed"]],
        use_container_width=True, hide_index=True,
        disabled=[c for c in display_df.columns if c != "Select"],
        key="labels_editor",
    )
    selected = edited.loc[edited["Select"]]

    col_a, col_b = st.columns(2)
    with col_a:
        if not selected.empty:
            preview_sample = selected.iloc[0]
            st.caption("Preview of the first selected label:")
            png = generate_barcode_png(preview_sample["barcode_value"] or preview_sample["sample_id"])
            st.image(png, width=260)

    with col_b:
        if st.button(f"Generate PDF for {len(selected)} label(s)", disabled=selected.empty):
            samples_payload = selected.to_dict("records")
            pdf_bytes = generate_sample_label_pdf(samples_payload)
            st.download_button(
                "📄 Download label sheet (PDF)", pdf_bytes,
                file_name=f"labels_{date.today()}.pdf", mime="application/pdf",
                key="dl_labels",
            )
            mark_labels_printed(conn, selected["sample_id"].tolist(), user_name)
            log_audit(conn, "SAMPLES", "LABELS_PRINTED", user_name,
                      comment=f"{len(selected)} label(s)")
            st.success(f"{len(selected)} label(s) marked as printed.")
            st.rerun()


def page_sample_scan(conn, user_name):
    """Barcode scanning: instant sample lookup. A USB scanner behaves like a keyboard; a phone camera can also be used."""
    st.title("SAMPLE SCAN")

    st.markdown('<div class="scan-box">📷 Scan with a USB scanner or type the sample ID below</div>',
                unsafe_allow_html=True)
    barcode_text = st.text_input("Sample barcode / ID", key="scan_text_input",
                                  placeholder="Scan here or type e.g. S-A1B2C3D4E5")

    if zbar_available():
        with st.expander("Or scan using your camera"):
            photo = st.camera_input("Take a photo of the barcode")
            if photo is not None:
                decoded = decode_barcode_from_image(photo.getvalue())
                if decoded:
                    st.success(f"Barcode decoded: {decoded}")
                    barcode_text = decoded
                else:
                    st.warning("Could not read a barcode in this photo. Try again with "
                                "better lighting/focus, or type the ID manually above.")

    if not barcode_text:
        st.info("Waiting for a scan or manual entry...")
        return

    sample_id = find_sample_by_barcode(conn, barcode_text)
    if sample_id is None:
        st.error(f"No sample found for '{barcode_text}'.")
        return

    df = read_full_results(conn)
    sample_df = df[df["sample_id"] == sample_id]
    if sample_df.empty:
        st.warning("Sample exists but has no lab results linked yet.")
        return

    sample_df = compute_oor_flag(sample_df)
    first = sample_df.iloc[0]
    st.success(f"✅ Sample {sample_id} — Patient {first['usubjid']} — Visit {first['visit_code']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("Sample type", first.get("sample_type", "n/a"))
    col2.metric("Status", first.get("sample_status", "n/a"))
    location = get_current_storage_location(conn, sample_id)
    col3.metric("Storage", f"{location['box_id']} / {location['position_well']}" if location else "Not assigned")

    st.subheader("Results for this sample")
    st.dataframe(sample_df, use_container_width=True)

    with st.expander("Assign / update storage location"):
        with st.form("storage_form"):
            rack_id = st.text_input("Rack ID")
            box_id = st.text_input("Box ID")
            well = st.text_input("Position (e.g. B4)")
            volume = st.number_input("Remaining volume (µL)", min_value=0.0, step=10.0)
            submitted = st.form_submit_button("Save location")
            if submitted:
                if not (rack_id and box_id and well):
                    st.error("Rack, box and position are required.")
                else:
                    add_storage_location(conn, sample_id, rack_id, box_id, well,
                                          volume_ul=volume, user_name=user_name)
                    log_audit(conn, "SAMPLES", "STORAGE_UPDATED", user_name, record_ref=sample_id)
                    st.success("Storage location saved.")
                    st.rerun()

    log_audit(conn, "SAMPLES", "SAMPLE_SCANNED", user_name, record_ref=sample_id)


def page_storage_map(conn):
    """Visual chain-of-custody overview: physical storage locations and samples that still require storage."""
    st.title("🧊 STORAGE MAP")
    st.caption("Distribution of samples by freezer / rack / box, "
                "and detection of samples that have never been physically stored.")

    storage_df = get_all_current_storage_locations(conn)
    st.markdown(render_storage_map_html(storage_df), unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("⚠️ Samples never stored")
    missing_df = get_samples_without_storage(conn)
    if missing_df.empty:
        st.success("✅ All received samples have a recorded storage position.")
    else:
        st.warning(f"{len(missing_df)} sample(s) received but never physically stored "
                    "(assign a storage position from the Sample Scan page).")
        st.dataframe(missing_df, use_container_width=True)


def page_technical_validation(conn, user_name):
    st.title("TECHNICAL VALIDATION")
    st.caption("Results awaiting technical validation (status PENDING). "
                "Tick the ones you have checked, then confirm to move them to TECHNICAL_OK.")

    df = read_full_results(conn)
    pending_df = df[df["status"] == "PENDING"].copy()
    if pending_df.empty:
        st.success("✅ No results pending technical validation.")
        return

    visit_scope = st.selectbox(
        "Validation scope",
        ["All pending results", "VINC only", "V1 only", "V2 only"],
        key="tech_scope",
    )
    if visit_scope != "All pending results":
        pending_df = pending_df[pending_df["visit_code"] == visit_scope.split()[0]].copy()
    if pending_df.empty:
        st.info("No pending results match the selected scope.")
        return

    pending_df = compute_oor_flag(pending_df)
    select_all = st.checkbox("Select all displayed results", key="tech_select_all")
    pending_df.insert(0, "Validate", bool(select_all))
    display_cols = ["Validate", "result_id", "usubjid", "sample_id", "barcode_value",
                     "visit_code", "test_code", "test_name", "result_value", "result_unit", "Alerte"]
    edited = st.data_editor(
        pending_df[display_cols], use_container_width=True, hide_index=True,
        disabled=[c for c in display_cols if c != "Validate"], key="tech_editor",
    )
    selected_ids = edited.loc[edited["Validate"], "result_id"].astype(int).tolist()

    if st.button(f"Validate {len(selected_ids)} result(s) technically", disabled=len(selected_ids) == 0):
        n = mark_technical_validation(conn, selected_ids, user_name)
        log_audit(conn, "LAB_RESULTS", "TECHNICAL_VALIDATION", user_name,
                  comment=f"{n} results : {selected_ids}")
        st.success(f"{n} result(s) technically validated.")
        st.rerun()


# =====================================================================
# PAGES — BIOLOGIST
# =====================================================================
def page_biological_validation(conn, user_name):
    """Biological validation with electronic signature. Only explicitly selected TECHNICAL_OK results can be signed.
    The form captures the signature meaning and requires password re-authentication."""
    st.title("🧪 BIOLOGICAL VALIDATION")
    render_critical_alert_banner(conn)

    df = read_full_results(conn)
    pending_df = df[df["status"] == "TECHNICAL_OK"].copy()
    if pending_df.empty:
        st.info("No results are awaiting biological validation.")
        return

    review_scope = st.selectbox(
        "Review scope",
        ["All TECHNICAL_OK results", "VINC only", "Critical results only"],
        key="bio_scope",
    )
    if review_scope == "VINC only":
        pending_df = pending_df[pending_df["visit_code"] == "VINC"].copy()
    elif review_scope == "Critical results only":
        pending_df = compute_oor_flag(pending_df)
        pending_df = pending_df[pending_df["Alerte"] == CRITICAL_FLAG].copy()
    if pending_df.empty:
        st.info("No results match the selected review scope.")
        return

    pending_df = compute_oor_flag(pending_df)
    select_all = st.checkbox("Select all displayed results", key="bio_select_all")
    pending_df.insert(0, "Sign", bool(select_all))
    display_cols = ["Sign", "result_id", "usubjid", "visit_code", "sample_id",
                     "test_code", "test_name", "result_value", "result_unit",
                     "Alerte", "technical_validated_by"]
    edited = st.data_editor(
        pending_df[display_cols], use_container_width=True, hide_index=True,
        disabled=[c for c in display_cols if c != "Sign"], key="bio_editor",
    )
    selected_ids = edited.loc[edited["Sign"], "result_id"].astype(int).tolist()
    st.write(f"**{len(selected_ids)} result(s)** selected for signature.")

    st.markdown("---")
    st.subheader("🖋 Electronic Signature (21 CFR Part 11)")

    st.info(f"ℹ️ {ESIGNATURE_LEGAL_NOTICE}")

    with st.form("form_esignature_bio"):
        reason = st.selectbox("Meaning of this signature", SIGNATURE_REASONS)
        remarks = st.text_area("Remarks / observations",
                                placeholder="e.g. Results reviewed against the study procedure. No additional comment.")
        st.caption("🔒 Part 11 control: re-enter your password to validate and "
                    "sign the review. Only the results checked above will be signed.")
        password_input = st.text_input("Password", type="password")
        submit = st.form_submit_button("Sign and finalize validation")

        if submit:
            if not selected_ids:
                st.error("No result selected — check at least one row above.")
            elif not password_input:
                st.error("Password is required to sign.")
            else:
                from auth import check_password, get_user_record
                record = get_user_record(conn, user_name)
                if record is None or not check_password(password_input, record["password_hash"]):
                    st.error("Incorrect password — signature rejected.")
                    log_audit(conn, "LAB_RESULTS", "BIOLOGICAL_SIGNATURE_FAILED", user_name,
                              comment=f"{len(selected_ids)} targeted results")
                else:
                    n = mark_biological_validation(conn, selected_ids, user_name, reason, remarks)
                    log_audit(conn, "LAB_RESULTS", "BIOLOGICAL_SIGNATURE", user_name,
                              record_ref=str(selected_ids),
                              comment=f"{n} signed results. Reason={reason}. Remarks={remarks}")
                    st.success(f"✅ {n} result(s) biologically validated and electronically signed.")

                    _maybe_auto_send_reports(conn, selected_ids, user_name)
                    st.rerun()


def _maybe_auto_send_reports(conn, result_ids, user_name):
    """When enabled in Settings/Automation, generates and delivers a patient PDF report to configured internal-mailbox recipients.
    Optional SMTP delivery is added when configured. Delivery failure is logged and never reverses an already completed validation."""
    if get_setting(conn, "auto_send_reports_enabled", "0") != "1":
        return
    recipients = [r.strip() for r in (get_setting(conn, "notify_emails_physician", "") or "").split(",") if r.strip()]
    if not recipients:
        return

    report_password = get_setting(conn, "report_pdf_password")
    usubjids = get_usubjids_for_results(conn, result_ids)
    for usubjid in usubjids:
        subject = f"Biological report — {usubjid}"
        body = f"The biological report for {usubjid} has been validated and signed. See the attachment."
        filename = f"{usubjid}_report_{date.today()}.pdf"

        # Livraison interne (toujours disponible, PDF non chiffré car déjà
        # protégé par l'authentification de l'application)
        pdf_bytes_internal = generate_patient_pdf_report(conn, usubjid, user_name)
        n_delivered = 0
        if pdf_bytes_internal is not None:
            n_delivered = send_internal_message_to_many(
                conn, recipients, subject, body,
                sender_username=user_name, sender_label="BLOOD LIMS Automation",
                attachment_bytes=pdf_bytes_internal, attachment_name=filename,
                attachment_mimetype="application/pdf",
            )

        # E-mail SMTP réel EN PLUS, si un mot de passe de chiffrement est configuré
        also_emailed = False
        if report_password and pdf_bytes_internal is not None:
            pdf_bytes_encrypted = generate_patient_pdf_report(conn, usubjid, user_name, password=report_password)
            also_emailed = send_secure_report(
                subject=f"[BLOOD Study] {subject}",
                body=body + " (password-protected PDF; password communicated separately.)",
                recipients=recipients, attachment_bytes=pdf_bytes_encrypted,
                attachment_filename=filename,
            )

        log_audit(conn, "LAB_RESULTS",
                  "REPORT_AUTO_SENT" if (n_delivered or also_emailed) else "REPORT_AUTO_SEND_FAILED",
                  user_name, record_ref=usubjid,
                  comment=f"{n_delivered} message(s) interne(s){' + e-mail' if also_emailed else ''}")


# =====================================================================
# PAGES — CRO / DASHBOARD
# =====================================================================
def page_process_flow(conn):
    """Workflow visualization: live counters plus a Kanban-style view of samples at each workflow stage."""
    st.title("PROCESS FLOW")
    render_critical_alert_banner(conn)

    st.caption("Live workflow diagram: counters reflect the current database state.")
    svg = render_pipeline_svg(conn)
    st.markdown(f'<div style="max-width:760px;margin:0 auto;">{svg}</div>', unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("Samples by workflow stage")
    df = read_full_results(conn)
    if df.empty:
        st.info("No data available.")
        return

    col_pending, col_tech, col_reviewed = st.columns(3)
    stage_cols = {
        "PENDING": (col_pending, "⏳ Awaiting technical validation"),
        "TECHNICAL_OK": (col_tech, "🔬 Awaiting biological validation"),
        "REVIEWED": (col_reviewed, "✅ Reviewed"),
    }
    for status, (col, title) in stage_cols.items():
        with col:
            st.markdown(f"**{title}**")
            stage_df = df[df["status"] == status]
            if stage_df.empty:
                st.caption("Nothing here.")
            else:
                summary = stage_df.groupby("usubjid").size().reset_index(name="n_resultats")
                for _, row in summary.head(15).iterrows():
                    st.markdown(_html(f"""
                    <div class="search-result-card" style="padding:0.5rem 0.8rem; margin-bottom:0.5rem;">
                        <strong>{row['usubjid']}</strong><br/>
                        <span style="font-size:0.8rem;color:#7B8794;">{row['n_resultats']} result(s)</span>
                    </div>
                    """), unsafe_allow_html=True)
                if len(summary) > 15:
                    st.caption(f"... and {len(summary) - 15} more.")


def page_mon_compte(conn, user_name, role, full_name):
    """Personal account area: profile, job title, e-mail and password change. Username and role are read-only here and remain CRO-controlled."""
    st.title("🪪 MY ACCOUNT")

    cur = conn.execute(
        "SELECT full_name, email, job_title FROM USERS WHERE username = ?", (user_name,)
    )
    row = cur.fetchone()
    current_full_name, current_email, current_job_title = row if row else (full_name, "", "")

    col_info, col_badge = st.columns([2, 1])
    with col_info:
        with st.form("mon_compte_form"):
            new_full_name = st.text_input("Full name", value=current_full_name or "")
            new_job_title = st.text_input("Job title", value=current_job_title or "",
                                            placeholder="e.g. Laboratory Technician, Site FR-001")
            new_email = st.text_input("E-mail", value=current_email or "")
            submitted = st.form_submit_button("Save")
            if submitted:
                conn.execute(
                    "UPDATE USERS SET full_name = ?, job_title = ?, email = ? WHERE username = ?",
                    (new_full_name, new_job_title, new_email, user_name),
                )
                conn.commit()
                log_audit(conn, "USERS", "PROFILE_UPDATED", user_name)
                st.session_state.auth_full_name = new_full_name
                st.success("Profile updated.")
                st.rerun()

    with col_badge:
        st.markdown(_html(f"""
        <div class="lims-panel">
            <h4>{current_full_name}</h4>
            <p style="color:#7B8794;font-size:0.85rem;margin:0.2rem 0;">@{user_name}</p>
            <span class="role-badge" style="background:#2E86C1;color:white;border:none;">{ROLE_LABELS.get(role, role)}</span>
        </div>
        """), unsafe_allow_html=True)

    st.caption("ℹ️ Username and role cannot be changed here — a role change "
                "affects application access rights and remains restricted to the "
                "CRO (User Management page), following separation of duties.")

    st.markdown("---")
    st.subheader("🔒 Change my password")
    with st.form("mon_compte_password_form"):
        new_pwd = st.text_input("New password", type="password")
        confirm_pwd = st.text_input("Confirm new password", type="password")
        pwd_submitted = st.form_submit_button("Update password")
        if pwd_submitted:
            if new_pwd != confirm_pwd:
                st.error("Passwords do not match.")
            else:
                ok, msg = validate_password_complexity(new_pwd)
                if not ok:
                    st.error(msg)
                else:
                    from auth import update_password
                    update_password(conn, user_name, new_pwd)
                    log_audit(conn, "USERS", "PASSWORD_CHANGED", user_name)
                    st.success("Password updated.")


def page_messagerie(conn, user_name):
    """Internal mailbox: receives automation messages and allows users to message one another. It is an application mailbox, not an external e-mail provider."""
    st.title("📧 MAILBOX")

    tab_inbox, tab_compose = st.tabs(["📥 Inbox", "✉️ New message"])

    with tab_inbox:
        inbox = get_inbox(conn, user_name)
        if inbox.empty:
            st.info("No messages yet.")
        else:
            for _, row in inbox.iterrows():
                unread = not bool(row["is_read"])
                label = f"{'🔵 ' if unread else ''}{row['subject']} — {row['sender_label']} · {row['created_at'][:16].replace('T', ' ')}"
                with st.expander(label):
                    msg = get_message(conn, int(row["message_id"]), user_name)
                    if not row["is_read"]:
                        mark_as_read(conn, int(row["message_id"]), user_name)
                    st.write(msg["body"])
                    if msg["attachment_name"]:
                        st.download_button(
                            f"⬇️ {msg['attachment_name']}", msg["attachment_data"],
                            file_name=msg["attachment_name"],
                            mime=msg["attachment_mimetype"] or "application/octet-stream",
                            key=f"dl_msg_{msg['message_id']}",
                        )

    with tab_compose:
        recipients = list_active_usernames(conn, exclude_username=user_name)
        if not recipients:
            st.info("No other active user is available to message.")
        else:
            with st.form("compose_form"):
                options = {f"{full_name} (@{uname}) — {ROLE_LABELS.get(role, role)}": uname
                           for uname, full_name, role in recipients}
                choice = st.selectbox("Recipient", list(options.keys()))
                subject = st.text_input("Subject")
                body = st.text_area("Message")
                submitted = st.form_submit_button("Send")
                if submitted:
                    if not subject or not body:
                        st.error("Subject and message are required.")
                    else:
                        sender_row = conn.execute(
                            "SELECT full_name FROM USERS WHERE username = ?", (user_name,)
                        ).fetchone()
                        sender_label = sender_row[0] if sender_row else user_name
                        send_internal_message(conn, options[choice], subject, body,
                                               sender_username=user_name, sender_label=sender_label)
                        log_audit(conn, "MESSAGES", "MESSAGE_SENT", user_name,
                                  record_ref=options[choice])
                        st.success("Message sent.")
                        st.rerun()


def page_guide(role):
    st.title("❓ GUIDE")
    st.caption("Role-specific in-app guidance. The full SOP and demonstration guide are provided with the project package.")

    st.subheader("General principle")
    st.write(
        "Every result follows the same path: **Ingestion** (CSV or HL7) → **Sample + barcode** → "
        "**Technical validation** → **Biological validation (electronic signature)** → **Distribution** "
        "(patient record, sponsor package, CDISC-oriented export). Each step supports controlled data quality, "
        "traceability and separation of responsibilities."
    )

    role_help = {
        "LAB_TECH": [
            ("📥 Data Ingestion", "Import the weekly CSV file from the central laboratory. Everything is validated before database write; an invalid file is rejected as a batch."),
            ("🔌 HL7 Import", "Import a previously received HL7 v2 ORU^R01 message and map it explicitly to VINC, V1 or V2."),
            ("🏷️ Sample Labels", "Generate barcode labels for newly received samples."),
            ("📷 Sample Scan", "Scan or type a barcode to retrieve sample custody and result information."),
            ("🧪 Technical Validation", "Select the results you have checked and move them from PENDING to TECHNICAL_OK."),
        ],
        "BIOLOGIST": [
            ("🧬 Biological Validation", "Select TECHNICAL_OK results, choose the signature meaning, re-enter your password and electronically sign the selected results."),
            ("👤 Patient Records", "Review patient history and biomarker trends."),
            ("📝 Notes", "Add a time-stamped observation to a result."),
        ],
        "PHYSICIAN": [
            ("👤 Patient Records", "Review the patient record, validated results and trends."),
            ("📝 Notes", "Document a dated observation linked to a laboratory result."),
            ("📧 Mailbox", "Read reports and alerts delivered by the application."),
        ],
        "CRO": [
            ("📊 Dashboard", "Monitor global KPIs and critical results."),
            ("🧭 Process Flow", "View the live laboratory workflow."),
            ("📦 VINC extraction", "Prepare the official VINC sponsor package using a documented cut-off and reviewed-only eligibility."),
            ("🤖 Automation", "Preview, execute and audit weekly/monthly automation jobs; use the controlled demo mode for the presentation."),
            ("✏️ Data Correction / Void", "Create a controlled superseding version or void a record with a mandatory reason; the original remains traceable."),
            ("🕵️ Audit Trail", "Review the append-only event history, including old/new values for controlled changes."),
            ("📦 Export CDISC SDTM", "Generate the LB/DM starter exports and define.xml for the training scope."),
            ("🔒 Data Privacy", "Use the application-level privacy and consent controls for the training dataset."),
        ],
        "SPONSOR": [
            ("📤 VINC extraction", "View and download the reviewed VINC sponsor package available at the documented cut-off."),
            ("📧 Mailbox", "Receive the monthly sponsor package automatically in the internal mailbox."),
        ],
    }
    for title, explanation in role_help.get(role, []):
        with st.container(border=True):
            st.markdown(f"**{title}**")
            st.caption(explanation)


def page_dashboard(conn):
    st.title("DASHBOARD")
    render_critical_alert_banner(conn)

    with st.container(border=True):
        render_central_lab_reminder(conn)

    df = read_full_results(conn)
    if df.empty:
        st.info("No data available. Ask the laboratory technician to import a file.")
        return

    df = compute_oor_flag(df)
    n_oor = int((df["Alerte"] == OOR_FLAG).sum())
    n_critical = int((df["Alerte"] == CRITICAL_FLAG).sum())
    n_pending = int((df["status"] == "PENDING").sum())
    n_tech_ok = int((df["status"] == "TECHNICAL_OK").sum())
    n_reviewed = int((df["status"] == "REVIEWED").sum())
    review_pct = round((n_reviewed / len(df)) * 100) if len(df) else 0

    kpi_cards = "".join([
        kpi_card("Results", len(df), "🧪", "#2E86C1"),
        kpi_card("Patients", df["usubjid"].nunique(), "🧍", "#1B4F72"),
        kpi_card("Sites", df["site_id"].nunique(), "🏥", "#3498DB"),
        kpi_card("Outliers", n_oor, "⚠️", "#B03A2E"),
        kpi_card("Critical", n_critical, "🔴", "#7B241C"),
    ])
    st.markdown(_html(f'<div class="kpi-grid">{kpi_cards}</div>'), unsafe_allow_html=True)

    tat1, tat2 = compute_tat_hours(df)
    tat1_label = f"{tat1:.1f} h" if tat1 is not None else "n/a"
    tat2_label = f"{tat2:.1f} h" if tat2 is not None else "n/a"

    st.markdown(_html(f"""
    <div class="lims-panel">
        <h4>Validation queue &amp; turnaround time</h4>
        <div class="funnel-row">
            <div class="funnel-stage"><div class="funnel-count">{n_pending}</div><div class="funnel-label">Awaiting technician</div></div>
            <div class="funnel-stage"><div class="funnel-count">{n_tech_ok}</div><div class="funnel-label">Awaiting biologist</div></div>
            <div class="funnel-stage"><div class="funnel-count">{n_reviewed}</div><div class="funnel-label">Validated</div></div>
            <div class="funnel-stage"><div class="funnel-count">{tat1_label}</div><div class="funnel-label">Avg. TAT receipt→technical</div></div>
            <div class="funnel-stage"><div class="funnel-count">{tat2_label}</div><div class="funnel-label">Avg. TAT technical→biologist</div></div>
        </div>
    </div>
    """), unsafe_allow_html=True)

    col_donut, col_activity = st.columns([1, 1.4])
    with col_donut:
        st.markdown(_html(f"""
        <div class="lims-panel">
            <h4>Overall validation progress</h4>
            <div class="donut-wrap">
                <div class="donut" style="--pct:{review_pct}%;">
                    <div class="donut-hole">
                        <div class="donut-pct">{review_pct}%</div>
                        <div class="donut-caption">reviewed</div>
                    </div>
                </div>
                <div class="donut-legend">
                    <div class="legend-item"><span class="legend-dot" style="background:#2E86C1;"></span>Reviewed ({n_reviewed})</div>
                    <div class="legend-item"><span class="legend-dot" style="background:#E9EEF3;"></span>In progress ({n_pending + n_tech_ok})</div>
                </div>
            </div>
        </div>
        """), unsafe_allow_html=True)

    with col_activity:
        audit_df = read_audit_trail(conn)
        recent = audit_df.head(6) if not audit_df.empty else audit_df
        if recent is None or recent.empty:
            items_html = '<li class="activity-item"><span class="activity-meta">No activity recorded yet.</span></li>'
        else:
            items_html = "".join(
                f'<li class="activity-item"><span class="activity-action">{r["action"]}</span>'
                f'<span class="activity-meta">{r["user_name"]} · {r["event_timestamp"][:16].replace("T", " ")}</span></li>'
                for _, r in recent.iterrows()
            )
        st.markdown(_html(f"""
        <div class="lims-panel"><h4>Recent system events</h4><ul class="activity-list">{items_html}</ul></div>
        """), unsafe_allow_html=True)

    col_left, col_right = st.columns(2)
    with col_left:
        visit_data = df.groupby("visit_code").size().reset_index(name="count")
        fig_visit = px.bar(visit_data, x="visit_code", y="count", title="<b>Results per visit</b>",
                            text_auto=True, color_discrete_sequence=["#2E86C1"])
        fig_visit.update_layout(xaxis_title=None, yaxis_title="Count", xaxis_tickangle=0,
                                 plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                 margin=dict(l=20, r=20, t=40, b=20), height=340)
        fig_visit.update_traces(textposition="outside")
        st.plotly_chart(fig_visit, use_container_width=True)

    with col_right:
        oor_by_site = df.groupby("site_name").apply(
            lambda g: (g["Alerte"] == OOR_FLAG).sum() / len(g) * 100
        ).reset_index(name="outlier_pct")
        fig_site = px.bar(oor_by_site, x="site_name", y="outlier_pct",
                           title="<b>Outlier rate by site (%)</b>", text_auto=".1f",
                           color_discrete_sequence=["#1B4F72"])
        fig_site.update_layout(xaxis_title=None, yaxis_title="Rate (%)", xaxis_tickangle=0,
                                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                margin=dict(l=20, r=20, t=40, b=20), height=340)
        fig_site.update_traces(textposition="outside")
        st.plotly_chart(fig_site, use_container_width=True)

    st.subheader("Detailed data")
    st.dataframe(df, use_container_width=True)


def page_patient_search(conn):
    st.title("PATIENT SEARCH")
    st.caption("Global search across all patients, visits, results and notes — "
                "or scan a sample barcode directly.")
    query = st.text_input("Search", placeholder="e.g. BLOOD-FR-001, HBA1C, FR-002, or scan a sample barcode...")
    df = read_full_results(conn)
    if df.empty:
        st.info("No data available yet.")
        return
    if not query:
        st.info("Start typing above to filter across the whole dataset.")
        return

    q = query.strip().lower()
    mask = (
        df["usubjid"].str.lower().str.contains(q, na=False)
        | df["site_id"].str.lower().str.contains(q, na=False)
        | df["site_name"].str.lower().str.contains(q, na=False)
        | df["test_code"].str.lower().str.contains(q, na=False)
        | df["test_name"].str.lower().str.contains(q, na=False)
        | df["sample_id"].fillna("").str.lower().str.contains(q, na=False)
        | df["barcode_value"].fillna("").str.lower().str.contains(q, na=False)
    )
    filtered = df[mask]
    if filtered.empty:
        st.warning(f"No match found for '{query}'.")
        return

    filtered = compute_oor_flag(filtered)
    st.success(f"{len(filtered)} result(s) found across {filtered['usubjid'].nunique()} patient(s).")
    st.subheader("Matching results")
    st.dataframe(filtered, use_container_width=True)
    st.subheader("Visit completeness for matching patients")
    st.dataframe(build_patients_matrix(filtered), use_container_width=True, hide_index=True)


def page_patients(conn):
    st.title("PATIENT FOLLOW-UP")
    df = read_full_results(conn)
    matrix = build_patients_matrix(df)
    if matrix.empty:
        st.info("There are currently no patients on the register.")
        return
    st.caption("Overview of documented visits by patient.")
    st.dataframe(matrix, use_container_width=True, hide_index=True)


def page_patient_records(conn, user_name, role):
    st.title("PATIENT RECORDS")
    render_critical_alert_banner(conn)

    df = read_full_results(conn)
    if df.empty:
        st.info("No data available yet.")
        return

    patients = sorted(df["usubjid"].unique().tolist())
    selected = st.selectbox("Select a patient", patients)
    patient_df = compute_oor_flag(df[df["usubjid"] == selected].copy())
    patient_df = patient_df.sort_values(["visit_num", "test_code"])

    col_info, col_pdf = st.columns([3, 1])
    with col_info:
        first_row = patient_df.iloc[0]
        st.write(f"**Site:** {first_row['site_name']} ({first_row['country']}) | "
                  f"**Sex:** {first_row['sex']} | **Birth year:** {int(first_row['birth_year'])}")
    with col_pdf:
        pdf_bytes = generate_patient_pdf_report(conn, selected, user_name)
        if pdf_bytes and st.download_button("📄 Download PDF report", pdf_bytes,
                                              file_name=f"{selected}_report_{date.today()}.pdf",
                                              mime="application/pdf"):
            log_audit(conn, "LAB_RESULTS", "EXPORT_PDF_PATIENT", user_name, record_ref=selected)

    st.subheader("Results")
    st.caption("Current validation status for each visit:")
    for visit_code_iter in patient_df["visit_code"].unique():
        visit_status_series = patient_df[patient_df["visit_code"] == visit_code_iter]["status"]
        # The least advanced visit status is used : une visit n'est
        # affichée comme validée que si TOUS ses results le sont.
        order = {"PENDING": 0, "TECHNICAL_OK": 1, "REVIEWED": 2}
        least_advanced = min(visit_status_series, key=lambda s: order.get(s, 0))
        st.markdown(f"**{visit_code_iter}**")
        st.markdown(render_status_stepper(least_advanced), unsafe_allow_html=True)
    st.dataframe(patient_df, use_container_width=True)

    st.subheader("Biomarker trend across visits")
    test_options = sorted(patient_df["test_code"].unique().tolist())
    if test_options:
        chosen_test = st.selectbox("Biomarker", test_options)
        trend = patient_df[patient_df["test_code"] == chosen_test].set_index("visit_code")["result_value"]
        trend = trend.groupby(level=0).last()
        trend = trend.reindex(["VINC", "V1", "V2"]).dropna()
        if len(trend) >= 1:
            st.line_chart(trend)
        else:
            st.info("Not enough data points to plot a trend for this biomarker.")

    if role in ("BIOLOGIST", "PHYSICIAN", "CRO"):
        with st.container(border=True):
            st.subheader("Add a note")
            options = patient_df.apply(
                lambda r: f"{r['result_id']} — {r['test_code']} ({r['visit_code']})", axis=1
            ).tolist()
            if options:
                choice = st.selectbox("Result concerned", options, key="patient_remark_choice")
                remark_text = st.text_area("Note", key="patient_remark_text")
                if st.button("Save note", disabled=not remark_text):
                    result_id = int(choice.split(" — ")[0])
                    insert_remark(conn, result_id, remark_text, user_name)
                    log_audit(conn, "REMARKS", "ADD_REMARK", user_name, record_ref=str(result_id))
                    st.success("Note saved.")
                    st.rerun()

    remarks_df = read_remarks(conn)
    patient_remarks = remarks_df[remarks_df["usubjid"] == selected] if not remarks_df.empty else remarks_df
    st.subheader("Existing notes")
    if patient_remarks is None or patient_remarks.empty:
        st.info("No notes recorded for this patient.")
    else:
        st.dataframe(patient_remarks, use_container_width=True)


def page_hl7_import(conn, user_name):
    """Import of results via an HL7 v2 message (ORU^R01). The parser does not provide a real-time MLLP/RS-232 listener."""
    st.title("HL7 IMPORT")
    st.caption("Imports an HL7 v2.x message (ORU^R01 segment) exported from an analyzer or "
                "an existing LIS. Mapping to the study visits (VINC/V1/V2) is "
                "selected below because HL7 does not know the study visit plan.")

    uploaded_file = st.file_uploader("HL7 file (.hl7 or .txt)", type=["hl7", "txt"])
    if uploaded_file is None:
        return

    raw_text = uploaded_file.getvalue().decode("utf-8", errors="replace")
    parsed = parse_oru_r01(raw_text)

    if parsed.warnings:
        with st.expander(f"⚠️ {len(parsed.warnings)} read warning(s)", expanded=True):
            for w in parsed.warnings:
                st.write(f"- {w}")

    if not parsed.patient_identifier or not parsed.observations:
        st.error("Unreadable or incomplete message — import cannot continue.")
        return

    st.success(f"Patient detected: {parsed.patient_identifier} "
               f"(sex {parsed.patient_sex or '?'}, birth year {parsed.patient_birth_year or '?'})")
    st.subheader(f"{len(parsed.observations)} result(s) detected")
    preview_rows = []
    for obs in parsed.observations:
        low, high = parse_ref_range(obs.ref_range)
        preview_rows.append({
            "test_code": obs.test_code, "test_name": obs.test_name, "value": obs.value,
            "unit": obs.unit, "ref_low": low, "ref_high": high, "abnormal_flag": obs.abnormal_flag,
        })
    st.dataframe(pd.DataFrame(preview_rows), use_container_width=True)

    st.subheader("Clinical trial mapping")
    with st.form("hl7_mapping_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            site_id = st.text_input("Site ID", placeholder="e.g. FR-001")
        with col2:
            visit_code = st.selectbox("Visit", ["VINC", "V1", "V2"])
        with col3:
            visit_num = st.number_input("Visit number", min_value=1, value=1, step=1)
        visit_date = st.date_input("Visit date")
        sample_type = st.selectbox("Sample type", ["SERUM", "WHOLE_BLOOD", "PLASMA"])
        submitted = st.form_submit_button("Import these results")

        if submitted:
            if not site_id:
                st.error("Site ID is required.")
            else:
                usubjid = f"BLOOD-{site_id}-{parsed.patient_identifier}"
                get_or_create_site(conn, site_id)
                get_or_create_patient(conn, parsed.patient_identifier, usubjid, site_id,
                                       parsed.patient_identifier, parsed.patient_sex or "M",
                                       parsed.patient_birth_year or 1970)
                visit_id = get_or_create_visit(conn, parsed.patient_identifier, visit_code,
                                                str(visit_date), int(visit_num))
                sample_id = get_or_create_sample(conn, parsed.patient_identifier, visit_id, sample_type)

                for obs in parsed.observations:
                    low, high = parse_ref_range(obs.ref_range)
                    try:
                        value = float(obs.value)
                    except ValueError:
                        st.warning(f"Non-numeric value ignored for {obs.test_code} : '{obs.value}'")
                        continue
                    insert_lab_result(conn, visit_id, obs.test_code, obs.test_name, value,
                                       obs.unit, str(visit_date), low, high, sample_id)

                log_audit(conn, "LAB_RESULTS", "INGESTION_HL7", user_name,
                          record_ref=usubjid, comment=f"{len(parsed.observations)} results")
                st.success(f"{len(parsed.observations)} result(s) imported for {usubjid}, "
                           "status PENDING (technical validation required).")
                st.rerun()


def page_data_privacy(conn, user_name):
    """Privacy controls (data export, pseudonymisation and consent logging). These application controls do not replace compliant hosting for real patient health data."""
    st.title("DATA PRIVACY (RGPD)")

    tab_export, tab_anon, tab_consent = st.tabs(
        ["Access right (export)", "Pseudonymisation", "Consent log"])

    with tab_export:
        st.caption("Generates a complete export of data held for a patient (GDPR Article 15).")
        df = read_full_results(conn)
        if df.empty:
            st.info("No data available.")
        else:
            selected = st.selectbox("Patient", sorted(df["usubjid"].unique().tolist()), key="export_patient")
            if st.button("Generate JSON export"):
                import json
                data = export_patient_data(conn, selected)
                json_bytes = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
                log_audit(conn, "PATIENTS", "GDPR_EXPORT", user_name, record_ref=selected)
                st.download_button("⬇️ Download export", json_bytes,
                                    file_name=f"{selected}_gdpr_export_{date.today()}.json",
                                    mime="application/json")

    with tab_anon:
        st.caption("Pseudonymises a patient: generalises the birth year to a "
                    "5 -year band and removes the local identifier. Clinical data (results, "
                    "validations) are retained — clinical trial retention requirements apply. "
                    "This action is irreversible and recorded in the Audit Trail.")
        df = read_full_results(conn)
        if not df.empty:
            selected = st.selectbox("Patient", sorted(df["usubjid"].unique().tolist()), key="anon_patient")
            confirm = st.checkbox(f"I confirm that I want to pseudonymise {selected}")
            if st.button("Pseudonymise", disabled=not confirm):
                ok = anonymize_patient(conn, selected, user_name)
                if ok:
                    st.success(f"{selected} was pseudonymised.")
                    st.rerun()

    with tab_consent:
        st.caption("Consent status log (the signed document itself is "
                    "NOT stored here; only a reference is stored).")
        df = read_full_results(conn)
        if not df.empty:
            with st.form("consent_form"):
                selected = st.selectbox("Patient", sorted(df["usubjid"].unique().tolist()), key="consent_patient")
                patient_id = df[df["usubjid"] == selected].iloc[0]["patient_id"]
                status = st.selectbox("Status", ["GRANTED", "WITHDRAWN", "AMENDED"])
                doc_ref = st.text_input("Document reference (e.g. file name, eCRF ID)")
                submitted = st.form_submit_button("Save")
                if submitted:
                    record_consent(conn, patient_id, selected, status, doc_ref, user_name)
                    st.success("Consent record saved.")
                    st.rerun()

        st.subheader("History")
        consent_df = get_all_consent(conn)
        if consent_df.empty:
            st.info("No consent records found.")
        else:
            st.dataframe(consent_df, use_container_width=True)


def page_export_sdtm(conn, user_name):
    st.title("CDISC SDTM EXPORT")
    st.caption("LB (Laboratory) and DM (Demographics) domains, plus a starter define.xml. "
                "See the Define-XML file itself for the disclaimer on what still needs to be "
                "completed before a real regulatory submission.")

    tab_lb, tab_dm, tab_define = st.tabs(["LB domain", "DM domain", "define.xml"])

    with tab_lb:
        df = select_reviewed_sdtm_source(read_full_results(conn))
        if df.empty:
            st.info("No REVIEWED data available for the final SDTM LB export.")
        else:
            df = df.sort_values(["usubjid", "visit_num", "test_code"]).copy()
            df["LBSEQ"] = df.groupby("usubjid").cumcount() + 1
            sdtm = pd.DataFrame({
                "STUDYID": STUDYID, "DOMAIN": "LB", "USUBJID": df["usubjid"], "LBSEQ": df["LBSEQ"],
                "LBTESTCD": df["test_code"], "LBTEST": df["test_name"],
                "LBORRES": df["result_value"].astype(str), "LBORRESU": df["result_unit"],
                "LBSTRESN": df["result_value"], "LBSTRESU": df["result_unit"],
                "LBNRIND": [compute_lbnrind(v, lo, hi) for v, lo, hi in
                            zip(df["result_value"], df["ref_low"], df["ref_high"])],
                "LBBLFL": df["visit_code"].apply(lambda v: "Y" if v == "VINC" else ""),
                "VISITNUM": df["visit_num"], "VISIT": df["visit_code"], "LBDTC": df["result_date"],
            })
            st.dataframe(sdtm, use_container_width=True)
            csv_buffer = io.StringIO()
            sdtm.to_csv(csv_buffer, index=False)
            if st.download_button("Download LB domain (CSV)", csv_buffer.getvalue(),
                                    file_name=f"SDTM_LB_BLOOD_{date.today()}.csv", key="dl_lb"):
                log_audit(conn, "LAB_RESULTS", "EXPORT_SDTM_LB", user_name, comment=f"{len(sdtm)} lignes")

    with tab_dm:
        dm = generate_dm_domain(conn)
        if dm.empty:
            st.info("No data to export.")
        else:
            st.dataframe(dm, use_container_width=True)
            csv_buffer = io.StringIO()
            dm.to_csv(csv_buffer, index=False)
            if st.download_button("Download DM domain (CSV)", csv_buffer.getvalue(),
                                    file_name=f"SDTM_DM_BLOOD_{date.today()}.csv", key="dl_dm"):
                log_audit(conn, "PATIENTS", "EXPORT_SDTM_DM", user_name, comment=f"{len(dm)} lignes")

    with tab_define:
        st.caption("Starter Define-XML v2.0 covering LB and DM as implemented in this app. "
                    "Complete CodeLists/MethodDefs/Comments and validate with Pinnacle 21 "
                    "(or equivalent) before any real regulatory submission.")
        define_bytes = generate_define_xml()
        if st.download_button("Download define.xml", define_bytes,
                                file_name="define.xml", mime="application/xml", key="dl_define"):
            log_audit(conn, "SETTINGS", "EXPORT_DEFINE_XML", user_name)


def page_user_management(conn, user_name):
    """CRO-only: create, deactivate, change role,
    reset passwords. Without this page, the only way
    to add a user would be to edit schema.sql manually."""
    st.title("USER MANAGEMENT")
    st.caption("Manage accounts for all roles. Passwords are never shown except once, "
                "right after creation or reset — write it down or share it securely, "
                "it cannot be retrieved again.")

    users_df = list_users(conn)
    st.subheader("Existing accounts")
    display_df = users_df.copy()
    display_df["status"] = display_df.apply(
        lambda r: ("🔒 Locked" if r["locked_until"] else
                   ("⛔ Inactive" if not r["active"] else "✅ Active")), axis=1)
    st.dataframe(
        display_df[["username", "full_name", "role", "email", "status", "must_change_password", "created_at"]],
        use_container_width=True, hide_index=True,
    )

    st.markdown("---")
    col_create, col_manage = st.columns(2)

    with col_create:
        st.subheader("Create a new account")
        with st.form("create_user_form"):
            new_username = st.text_input("Username")
            new_full_name = st.text_input("Full name")
            new_role = st.selectbox("Role", ALL_ROLES, format_func=lambda r: ROLE_LABELS.get(r, r))
            new_email = st.text_input("E-mail (required for password reset / alerts)")
            submitted = st.form_submit_button("Create account")
            if submitted:
                if not new_username or not new_full_name:
                    st.error("Username and full name are required.")
                elif username_exists(conn, new_username):
                    st.error(f"Username '{new_username}' already exists.")
                else:
                    temp_password = generate_temp_password()
                    create_user(conn, new_username, new_full_name, new_role, new_email,
                                hash_password(temp_password))
                    log_audit(conn, "USERS", "USER_CREATED", user_name, record_ref=new_username,
                              comment=f"role={new_role}")
                    st.success(f"Account '{new_username}' created. Temporary password "
                               f"(shown once — share it securely, the user must change it "
                               f"at first login):")
                    st.code(temp_password)

    with col_manage:
        st.subheader("Manage an existing account")
        if users_df.empty:
            st.info("No users yet.")
        else:
            target = st.selectbox("Account", users_df["username"].tolist(), key="manage_target")
            target_row = users_df[users_df["username"] == target].iloc[0]

            new_role_for_target = st.selectbox(
                "Change role", ALL_ROLES,
                index=ALL_ROLES.index(target_row["role"]) if target_row["role"] in ALL_ROLES else 0,
                format_func=lambda r: ROLE_LABELS.get(r, r), key="role_select",
            )
            if st.button("Apply role change", key="apply_role"):
                set_user_role(conn, target, new_role_for_target)
                log_audit(conn, "USERS", "ROLE_CHANGED", user_name, record_ref=target,
                          comment=f"new_role={new_role_for_target}")
                st.success(f"Role updated for {target}.")
                st.rerun()

            col_a, col_b = st.columns(2)
            with col_a:
                if bool(target_row["active"]):
                    if st.button("Deactivate account", key="deactivate"):
                        set_user_active(conn, target, False)
                        log_audit(conn, "USERS", "USER_DEACTIVATED", user_name, record_ref=target)
                        st.success(f"{target} deactivated.")
                        st.rerun()
                else:
                    if st.button("Reactivate account", key="reactivate"):
                        set_user_active(conn, target, True)
                        log_audit(conn, "USERS", "USER_REACTIVATED", user_name, record_ref=target)
                        st.success(f"{target} reactivated.")
                        st.rerun()
            with col_b:
                if st.button("Reset password", key="reset_pwd"):
                    temp_password = generate_temp_password()
                    admin_reset_password(conn, target, hash_password(temp_password))
                    log_audit(conn, "USERS", "PASSWORD_RESET_BY_ADMIN", user_name, record_ref=target)
                    st.success(f"Password reset for {target} (must change at next login):")
                    st.code(temp_password)



def page_data_correction(conn, user_name):
    """CRO-only controlled correction/void page.

    The original record is never overwritten. A correction creates a new
    PENDING version linked through supersedes_result_id; a void marks the
    active record VOID. Both actions require a documented reason and are
    recorded in the immutable audit trail by the database layer.
    """
    st.title("DATA CORRECTION / VOID")
    st.caption(
        "Controlled post-entry changes. The original record is preserved; "
        "corrections create a new PENDING version and voids make the active "
        "record ineligible for official exports. A documented reason is mandatory."
    )

    active_df = read_full_results(conn)
    if active_df.empty:
        st.info("No active laboratory results are available.")
        return

    # Only active records are actionable. Historical versions remain visible
    # below after an operation for traceability.
    st.subheader("Select an active result")
    active_df = active_df.copy()
    active_df["display_label"] = active_df.apply(
        lambda r: (
            f"#{int(r['result_id'])} — {r['usubjid']} — "
            f"{r['visit_code']} — {r['test_code']} — {r['result_value']} {r['result_unit'] or ''}"
        ), axis=1,
    )
    labels = active_df["display_label"].tolist()
    choice = st.selectbox("Result", labels, key="correction_result_select")
    result_id = int(choice.split(" — ")[0].lstrip("#"))
    selected = active_df[active_df["result_id"] == result_id].iloc[0]

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Subject", selected["usubjid"])
        c2.metric("Visit", selected["visit_code"])
        c3.metric("Test", selected["test_code"])
        c4.metric("Current value", f"{selected['result_value']} {selected['result_unit'] or ''}")
        st.write(
            f"**Status:** {selected['status']} · **Record status:** {selected['record_status']} · "
            f"**Result date:** {selected['result_date']}"
        )

    st.markdown("---")
    col_correct, col_void = st.columns(2)

    with col_correct:
        st.subheader("Create a corrected version")
        try:
            current_value = float(selected["result_value"])
        except (TypeError, ValueError):
            current_value = 0.0
        new_value = st.number_input(
            "Corrected result value",
            value=current_value,
            format="%.6g",
            key="correction_new_value",
        )
        correction_reason = st.text_area(
            "Documented reason (mandatory)",
            placeholder=(
                "Example: Source laboratory correction received; the original result "
                "was transcribed incorrectly."
            ),
            key="correction_reason",
        )
        if st.button("Create corrected version", type="primary", key="btn_create_correction"):
            if not correction_reason.strip():
                st.error("A documented change reason is required.")
            elif float(new_value) == current_value:
                st.error("Enter a corrected value different from the current value.")
            else:
                try:
                    new_id = create_result_correction(
                        conn, result_id, float(new_value), correction_reason, user_name
                    )
                    st.success(
                        f"Correction created as result #{new_id}. The original result #{result_id} "
                        "is preserved as SUPERSEDED and the new version is PENDING."
                    )
                    st.rerun()
                except (ValueError, sqlite3.Error) as exc:
                    st.error(f"Correction could not be created: {exc}")

    with col_void:
        st.subheader("Void the active result")
        void_reason = st.text_area(
            "Void reason (mandatory)",
            placeholder=(
                "Example: Result invalidated by the source laboratory; replacement "
                "result will be transmitted in a subsequent file."
            ),
            key="void_reason",
        )
        if st.button("Void active result", key="btn_void_result"):
            if not void_reason.strip():
                st.error("A documented void reason is required.")
            else:
                try:
                    void_result(conn, result_id, void_reason, user_name)
                    st.success(
                        f"Result #{result_id} has been voided. It remains traceable but is "
                        "excluded from official exports."
                    )
                    st.rerun()
                except (ValueError, sqlite3.Error) as exc:
                    st.error(f"Result could not be voided: {exc}")

    st.markdown("---")
    st.subheader("Version history")
    history = read_result_history(conn, result_id)
    if history.empty:
        st.info("No history found for the selected result.")
    else:
        cols = [
            c for c in [
                "result_id", "result_value", "result_unit", "status", "record_status",
                "supersedes_result_id", "change_reason", "technical_validated_by",
                "technical_validated_at", "biologist_validated_by", "biologist_validated_at",
            ] if c in history.columns
        ]
        st.dataframe(history[cols], use_container_width=True, hide_index=True)

def page_settings(conn, user_name):
    st.title("SETTINGS")
    st.caption("Customise the look and feel of the app for your business.")
    current_name = get_setting(conn, "company_name") or "Clinical Services"
    current_logo = get_setting(conn, "logo_base64")

    with st.container(border=True):
        st.subheader("Company identity")
        new_name = st.text_input("Company name displayed in the header", value=current_name)
        if current_logo:
            st.write("Current logo :")
            st.markdown(f'<img src="data:image/png;base64,{current_logo}" style="height:60px;">',
                        unsafe_allow_html=True)
        uploaded_logo = st.file_uploader("New logo (PNG or JPG)", type=["png", "jpg", "jpeg"])
        if st.button("Save settings"):
            set_setting(conn, "company_name", new_name)
            if uploaded_logo is not None:
                import base64
                logo_bytes = uploaded_logo.read()
                logo_b64 = base64.b64encode(logo_bytes).decode("utf-8")
                set_setting(conn, "logo_base64", logo_b64)
            log_audit(conn, "SETTINGS", "UPDATE_BRANDING", user_name, comment=f"company_name={new_name}")
            st.success("Settings saved.")
            st.rerun()

    with st.container(border=True):
        st.subheader("Backup")
        st.caption("Download a full snapshot of the database file (SQLite). "
                    "Recommended policy: at least weekly, stored somewhere other than "
                    "this app's own filesystem (SQLite on Streamlit Cloud has no built-in "
                    "backup/retention).")
        try:
            with open(DB_PATH, "rb") as f:
                db_bytes = f.read()
            if st.download_button("⬇️ Download database snapshot (.db)", db_bytes,
                                    file_name=f"blood_study_backup_{date.today()}.db"):
                log_audit(conn, "SETTINGS", "DB_BACKUP_DOWNLOADED", user_name)
        except FileNotFoundError:
            st.info("Database file not found yet.")


def page_automation(conn, user_name):
    st.title("AUTOMATION")
    st.caption(
        "The free Streamlit deployment does not provide a persistent server-side scheduler. "
        "This application therefore evaluates automation rules on page load and provides a controlled "
        "manual/demo runner. All sends are recorded in AUTOMATION_RUNS and the Audit Trail."
    )

    enabled = get_setting(conn, "automation_enabled", "1") == "1"
    new_enabled = st.toggle("Enable automatic checks on application load", value=enabled)

    threshold = int(get_setting(conn, "reminder_ingestion_days", "7"))
    new_threshold = st.number_input("Days before an overdue weekly-ingestion reminder", min_value=1, max_value=60, value=threshold)

    vinc_day_setting = int(get_setting(conn, "vinc_reminder_day", "25"))
    new_vinc_day = st.number_input(
        "Monthly sponsor package due day", min_value=1, max_value=28, value=vinc_day_setting,
        help="After this day, the monthly VINC package is due unless it has already been sent."
    )

    st.subheader("Recipients")
    account_rows = list_active_usernames(conn)
    account_options = {f"{fname} (@{uname})": uname for uname, fname, role in account_rows}
    label_by_username = {uname: label for label, uname in account_options.items()}

    def _preselect(setting_key):
        raw = (get_setting(conn, setting_key, "") or "").split(",")
        return [label_by_username[u.strip()] for u in raw if u.strip() in label_by_username]

    lab_labels = st.multiselect("Weekly central laboratory overdue reminder", list(account_options.keys()), default=_preselect("notify_emails_lab"))
    sponsor_labels = st.multiselect("Monthly VINC package recipient(s)", list(account_options.keys()), default=_preselect("notify_emails_sponsor"))
    critical_labels = st.multiselect("Critical result alert recipient(s)", list(account_options.keys()), default=_preselect("notify_emails_critical"))

    st.subheader("Automatic patient reports")
    st.caption("After biological signature, selected recipients can receive a PDF report through the internal mailbox. SMTP is optional.")
    auto_send_enabled = get_setting(conn, "auto_send_reports_enabled", "0") == "1"
    new_auto_send = st.toggle("Automatically send a patient PDF report after biological signature", value=auto_send_enabled)
    physician_labels = st.multiselect("Patient report recipients", list(account_options.keys()), default=_preselect("notify_emails_physician"))

    if st.button("Save automation settings"):
        set_setting(conn, "automation_enabled", "1" if new_enabled else "0")
        set_setting(conn, "reminder_ingestion_days", str(new_threshold))
        set_setting(conn, "vinc_reminder_day", str(new_vinc_day))
        set_setting(conn, "notify_emails_lab", ",".join(account_options[l] for l in lab_labels))
        set_setting(conn, "notify_emails_sponsor", ",".join(account_options[l] for l in sponsor_labels))
        set_setting(conn, "notify_emails_critical", ",".join(account_options[l] for l in critical_labels))
        set_setting(conn, "auto_send_reports_enabled", "1" if new_auto_send else "0")
        set_setting(conn, "notify_emails_physician", ",".join(account_options[l] for l in physician_labels))
        log_audit(conn, "SETTINGS", "UPDATE_AUTOMATION", user_name)
        st.success("Automation settings saved.")
        st.rerun()

    st.markdown("---")
    st.subheader("🎬 Guided demonstration mode")
    st.caption("Use this mode during the presentation. It uses a demo clock, so you do not need to wait until the 25th of a real month.")
    if st.button("🪄 Apply recommended demo configuration"):
        set_setting(conn, "automation_enabled", "1")
        set_setting(conn, "automation_demo_mode", "1")
        set_setting(conn, "automation_demo_date", "2026-06-25")
        set_setting(conn, "automation_demo_last_ingestion_date", "2026-06-18")
        set_setting(conn, "reminder_ingestion_days", "7")
        set_setting(conn, "vinc_reminder_day", "25")
        set_setting(conn, "notify_emails_lab", "lab_tech1")
        set_setting(conn, "notify_emails_sponsor", "sponsor_lph")
        set_setting(conn, "notify_emails_critical", "biologist1")
        log_audit(conn, "SETTINGS", "APPLY_DEMO_AUTOMATION_CONFIG", user_name,
                  comment="Recommended classroom automation configuration applied.")
        st.success("Recommended automation demo configuration applied.")
        st.rerun()
    demo_mode = get_setting(conn, "automation_demo_mode", "0") == "1"
    demo_mode_new = st.toggle("Enable demo clock", value=demo_mode)
    demo_date_raw = get_setting(conn, "automation_demo_date", "2026-06-25")
    try:
        demo_default = date.fromisoformat(demo_date_raw)
    except ValueError:
        demo_default = date(2026, 6, 25)
    demo_date_new = st.date_input("Demo reference date", value=demo_default, key="automation_demo_date_picker")
    demo_ing_raw = get_setting(conn, "automation_demo_last_ingestion_date", "2026-06-18")
    try:
        demo_ing_default = date.fromisoformat(demo_ing_raw)
    except ValueError:
        demo_ing_default = date(2026, 6, 18)
    demo_ing_new = st.date_input("Demo last-ingestion date", value=demo_ing_default, key="automation_demo_ingestion_picker")
    if st.button("Save demo clock"):
        set_setting(conn, "automation_demo_mode", "1" if demo_mode_new else "0")
        set_setting(conn, "automation_demo_date", demo_date_new.isoformat())
        set_setting(conn, "automation_demo_last_ingestion_date", demo_ing_new.isoformat())
        log_audit(conn, "SETTINGS", "UPDATE_AUTOMATION_DEMO_CLOCK", user_name,
                  comment=f"demo_mode={demo_mode_new}; reference={demo_date_new}; last_ingestion={demo_ing_new}")
        st.success("Demo clock saved.")
        st.rerun()

    st.info(
        "Recommended presentation setup: Demo clock ON, reference date 2026-06-25, last ingestion date 2026-06-18, "
        "sponsor recipient = sponsor_lph. Then preview, run the automation, and open the sponsor Mailbox to show the ZIP attachment."
    )

    st.markdown("---")
    st.subheader("🔍 Preview")
    preview = preview_automation(conn)
    for item in preview:
        icon = "🔔" if item["would_trigger"] else "✅"
        with st.container(border=True):
            st.markdown(f"{icon} **{item['title']}**")
            st.caption(item["detail"])
            recipients = [r.strip() for r in item["recipients"] if r.strip()]
            if recipients:
                st.write("Recipients:", ", ".join(recipients))

    col_run, col_normal = st.columns(2)
    with col_run:
        if st.button("▶ Run automation now", type="primary"):
            run_id, results = run_automation_now(conn, triggered_by=user_name, force=False)
            st.success(f"Automation run {run_id} completed.")
            for r in results:
                st.write(f"**{r['job']}** — triggered={r.get('triggered')} — sent={r.get('sent',0)} — {r.get('detail','')}")
    with col_normal:
        if st.button("🎬 Run controlled demo (force monthly send)"):
            run_id, results = run_automation_now(conn, triggered_by=user_name, force=True)
            st.success(f"Controlled demo run {run_id} completed.")
            for r in results:
                st.write(f"**{r['job']}** — triggered={r.get('triggered')} — sent={r.get('sent',0)}")
            st.warning("Force mode is for the classroom demonstration only. Data eligibility rules are never bypassed.")

    st.markdown("---")
    st.subheader("Recent automation runs")
    from db import list_recent_automation_runs
    runs_df = list_recent_automation_runs(conn)
    if runs_df.empty:
        st.info("No automation run has been recorded yet.")
    else:
        st.dataframe(runs_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("📅 Contractual cadence monitoring")
    st.caption("The laboratory sends files weekly to the CRO; the CRO sends the VINC package monthly to the sponsor. The tables below are based on recorded audit events.")
    col_week, col_month = st.columns(2)
    with col_week:
        st.markdown("**Weekly cadence — Central Laboratory → CRO**")
        weekly_df = get_weekly_ingestion_compliance(conn)
        st.dataframe(weekly_df, use_container_width=True, hide_index=True)
    with col_month:
        st.markdown("**Monthly cadence — CRO → Sponsor**")
        monthly_df = get_monthly_vinc_compliance(conn)
        st.dataframe(monthly_df, use_container_width=True, hide_index=True)


def page_audit_trail(conn):
    st.title("AUDIT TRAIL")
    st.caption("Timestamping in UTC. This log can only be added to: it cannot be edited, "
                "deleted, nor disabled — enforced at the database level "
                "(triggers trg_audit_no_update / trg_audit_no_delete), not merely by "
                "convention in the application.")
    df = read_audit_trail(conn)
    if df.empty:
        st.info("No actions have been logged.")
        return
    st.dataframe(df, use_container_width=True)


def page_extraction_vinc(conn, user_name, role):
    st.title("VINC VISIT RESULTS")
    render_sponsor_reminder(conn)
    df = read_full_results(conn)
    all_vinc = df[df["visit_code"] == "VINC"].copy()
    if all_vinc.empty:
        st.info("No VINC results are available at present.")
        return

    all_vinc = compute_oor_flag(all_vinc)
    st.subheader("VINC results — operational view")
    st.dataframe(all_vinc, use_container_width=True)

    cutoff_date = st.date_input(
        "Explicit monthly cut-off date", value=date.today(), key="vinc_cutoff_date",
        help="Only VINC results dated on/before the cut-off and in status REVIEWED are eligible for the official export."
    )
    eligible_vinc = select_vinc_for_export(all_vinc, cutoff_date)
    pending = all_vinc[(pd.to_datetime(all_vinc["visit_date"], errors="coerce").dt.date <= cutoff_date) & (all_vinc["status"] != "REVIEWED")]

    col1, col2 = st.columns(2)
    col1.metric("VINC records in scope", len(eligible_vinc))
    col2.metric("VINC records blocked", len(pending))
    if not pending.empty:
        st.warning(
            f"{len(pending)} VINC result(s) are excluded from the official package because they are not REVIEWED. "
            "The operational view remains visible for follow-up."
        )

    if eligible_vinc.empty:
        st.info("No REVIEWED VINC result is eligible for export at this cut-off.")
        return

    package = build_vinc_package(eligible_vinc, cutoff_date, user_name)
    st.success(
        f"Export package ready: {package['row_count']} reviewed VINC record(s). "
        f"Package SHA-256: {package['package_sha256']}"
    )

    # Keep the package ledger in the database only when the user is allowed to generate it.
    package_registered = conn.execute(
        "SELECT 1 FROM EXPORT_PACKAGES WHERE package_id = ?", (package["package_id"],)
    ).fetchone()
    if package_registered is None:
        from db import register_export_package
        register_export_package(
            conn, package["package_id"], "VINC_MONTHLY_CRO_TO_SPONSOR", user_name,
            cutoff_date, package["row_count"], package["csv_sha256"], package["package_sha256"],
            package["filename"]
        )

    if st.download_button(
        "⬇️ Download official VINC package (ZIP)", package["package_bytes"],
        file_name=package["filename"], mime="application/zip", key="dl_vinc_package"
    ):
        from db import mark_export_package_downloaded
        mark_export_package_downloaded(conn, package["package_id"], user_name)
        action = "CONSULTATION_VINC_SPONSOR" if role == "SPONSOR" else "EXPORT_VINC_CRO"
        log_audit(
            conn, "EXPORT_PACKAGES", action, user_name, record_ref=package["package_id"],
            comment=f"cutoff={cutoff_date}; rows={package['row_count']}; sha256={package['package_sha256']}"
        )

    pdf_bytes = generate_vinc_pdf_report(conn, eligible_vinc, user_name)
    if st.download_button(
        "📄 Download reviewed VINC PDF", pdf_bytes,
        file_name=f"BLOOD_VINC_{cutoff_date}.pdf", mime="application/pdf", key="dl_vinc_pdf"
    ):
        log_audit(
            conn, "EXPORT_PACKAGES", "EXPORT_PDF_VINC_SPONSOR" if role == "SPONSOR" else "EXPORT_PDF_VINC_CRO",
            user_name, record_ref=package["package_id"], comment=f"rows={package['row_count']}"
        )


def page_remarks(conn, user_name, role):
    st.title("RESULT COMMENTS")
    df = read_full_results(conn)

    if role in ("BIOLOGIST", "PHYSICIAN", "CRO"):
        if df.empty:
            st.info("No results available to add a comment.")
        else:
            with st.container(border=True):
                st.subheader("Add a comment")
                options = df.apply(
                    lambda r: f"{r['result_id']} — {r['usubjid']} — {r['test_code']} ({r['visit_code']})",
                    axis=1,
                ).tolist()
                choice = st.selectbox("Relevant result", options)
                result_id = int(choice.split(" — ")[0])
                remark_text = st.text_area("Your comment")
                if st.button("Save the comment", disabled=not remark_text):
                    insert_remark(conn, result_id, remark_text, user_name)
                    log_audit(conn, "REMARKS", "ADD_REMARK", user_name, record_ref=str(result_id))
                    st.success("Comment noted.")
                    st.rerun()

    st.subheader("Existing comments")
    remarks_df = read_remarks(conn)
    if remarks_df.empty:
        st.info("No comments have been recorded so far.")
    else:
        st.dataframe(remarks_df, use_container_width=True)


# =====================================================================
# ENTRY POINT
# =====================================================================
def main():
    inject_custom_css()
    conn = get_connection()

    # Si l'URL contient ?reset_token=..., on affiche le formulaire de
    # réinitialisation et on s'arrête là (rien d'autre ne doit se
    # rendre tant que ce n'est pas résolu).
    if handle_password_reset_flow(conn):
        return

    # The automation engine runs before authentication so a simple HTTP wake-up
    # from GitHub Actions can trigger the deterministic automation jobs even when
    # the Streamlit Community Cloud app has been sleeping. No user data is shown.
    check_and_send_reminders(conn)

    username, role, full_name = sidebar_user_identification(conn)
    if not username or role is None:
        render_landing_page(conn)
        return

    notif_count = None
    if role == "LAB_TECH":
        notif_count = count_by_status(conn, "PENDING")
    elif role == "BIOLOGIST":
        notif_count = count_by_status(conn, "TECHNICAL_OK")
    elif role == "CRO":
        notif_count = count_by_status(conn, "PENDING") + count_by_status(conn, "TECHNICAL_OK")

    render_top_banner(conn, f"{full_name} · {ROLE_LABELS.get(role, role)}", notif_count)

    allowed_pages = PAGE_PERMISSIONS.get(role, [])
    unread = count_unread(conn, username)

    def _format_page_label(p):
        label = f"{PAGE_ICONS.get(p, '')} {p}"
        if p == "Mailbox" and unread:
            label += f" ({unread})"
        return label

    page = st.sidebar.radio("Navigation", allowed_pages, format_func=_format_page_label)
    st.sidebar.markdown("---")
    st.sidebar.caption("Access in accordance with the principle of least privilege "
                        "(21 CFR Part 11 / Annex 11 §12). "
                        f"Session expires after 30 min of inactivity.")

    if page == "Data Ingestion":
        page_ingestion(conn, username)
    elif page == "HL7 Import":
        page_hl7_import(conn, username)
    elif page == "Sample Labels":
        page_sample_labels(conn, username)
    elif page == "Sample Scan":
        page_sample_scan(conn, username)
    elif page == "Storage Map":
        page_storage_map(conn)
    elif page == "Technical Validation":
        page_technical_validation(conn, username)
    elif page == "Biological Validation":
        page_biological_validation(conn, username)
    elif page == "Dashboard":
        page_dashboard(conn)
    elif page == "Process Flow":
        page_process_flow(conn)
    elif page == "Patient Search":
        page_patient_search(conn)
    elif page == "Patient follow-up":
        page_patients(conn)
    elif page == "Patient Records":
        page_patient_records(conn, username, role)
    elif page == "VINC extraction":
        page_extraction_vinc(conn, username, role)
    elif page == "Notes":
        page_remarks(conn, username, role)
    elif page == "Export CDISC SDTM":
        page_export_sdtm(conn, username)
    elif page == "Data Privacy":
        page_data_privacy(conn, username)
    elif page == "Settings":
        page_settings(conn, username)
    elif page == "Automation":
        page_automation(conn, username)
    elif page == "User Management":
        page_user_management(conn, username)
    elif page == "Data Correction / Void":
        page_data_correction(conn, username)
    elif page == "Audit Trail":
        page_audit_trail(conn)
    elif page == "Guide":
        page_guide(role)
    elif page == "Mailbox":
        page_messagerie(conn, username)
    elif page == "My Account":
        page_mon_compte(conn, username, role, full_name)


if __name__ == "__main__":
    main()
