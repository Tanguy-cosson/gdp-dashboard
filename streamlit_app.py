import io
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from audit import log_audit
from auth import (generate_temp_password, handle_password_reset_flow, hash_password,
                   sidebar_user_identification, validate_password_complexity)
from automation import check_and_send_reminders, preview_automation, send_secure_report
from barcode_utils import (decode_barcode_from_image, generate_barcode_png,
                            generate_sample_label_pdf, zbar_available)
from business_logic import (build_patients_matrix, compute_oor_flag, compute_tat_hours,
                             filter_reviewed_only, validate_ingestion_dataframe)
from cdisc_export import generate_define_xml, generate_dm_domain
from constants import (ALL_ROLES, CRITICAL_FLAG, ESIGNATURE_LEGAL_NOTICE, NORMAL_FLAG,
                        OOR_FLAG, PAGE_ICONS, PAGE_PERMISSIONS, ROLE_LABELS,
                        SIGNATURE_REASONS, STUDYID)
from db import (DB_PATH, add_storage_location, admin_reset_password, count_by_status,
                create_user, find_sample_by_barcode, get_all_current_storage_locations,
                get_connection, get_current_storage_location, get_or_create_patient,
                get_or_create_sample, get_or_create_site, get_or_create_visit,
                get_samples_pending_labels, get_samples_without_storage, get_setting,
                get_usubjids_for_results, insert_lab_result, insert_remark, list_users,
                mark_biological_validation, mark_labels_printed, mark_technical_validation,
                read_audit_trail, read_full_results, read_remarks, set_setting,
                set_user_active, set_user_role, username_exists)
from gdpr import anonymize_patient, export_patient_data, get_all_consent, record_consent
from hl7_import import parse_oru_r01, parse_ref_range
from mailbox import (count_unread, get_inbox, get_message, list_active_usernames,
                      mark_as_read, send_internal_message, send_internal_message_to_many)
from pdf_reports import generate_patient_pdf_report, generate_vinc_pdf_report
from ui import _html, inject_custom_css, kpi_card, render_landing_page, render_top_banner
from workflow_viz import render_pipeline_svg, render_status_stepper, render_storage_map_html

st.set_page_config(page_title="Projet BLOOD", page_icon="🩸", layout="wide")


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
        progress = st.progress(0, text="Importing...")
        n_rows = len(df)
        try:
            for i, (_, row) in enumerate(df.iterrows()):
                site_id = get_or_create_site(conn, row["site_id"])
                patient_id = get_or_create_patient(
                    conn, row["patient_id"], row["usubjid"], site_id,
                    row["subjid"], row["sex"], int(row["birth_year"]))
                visit_id = get_or_create_visit(
                    conn, patient_id, row["visit_code"], row["visit_date"], int(row["visit_num"]))
                sample_type = row["sample_type"] if has_sample_type and pd.notna(row["sample_type"]) else "SERUM"
                collection_dt = row["collection_datetime"] if has_collection and pd.notna(row["collection_datetime"]) else None
                sample_id = get_or_create_sample(conn, patient_id, visit_id, sample_type, collection_dt)

                ref_low = float(row["ref_low"]) if has_ref and pd.notna(row["ref_low"]) else None
                ref_high = float(row["ref_high"]) if has_ref and pd.notna(row["ref_high"]) else None
                crit_low = float(row["critical_low"]) if has_crit and pd.notna(row["critical_low"]) else None
                crit_high = float(row["critical_high"]) if has_crit and pd.notna(row["critical_high"]) else None

                insert_lab_result(
                    conn, visit_id, row["test_code"], row["test_name"],
                    float(row["result_value"]), row["result_unit"], row["result_date"],
                    ref_low, ref_high, sample_id, crit_low, crit_high)
                progress.progress((i + 1) / n_rows, text=f"Importing... {i + 1}/{n_rows}")
        except Exception as e:
            st.error(f"Import stopped due to an unexpected error on row {i + 2}: {e}. "
                      "Rows already inserted before the error remain in the database — "
                      "check the Audit Trail and correct manually if needed.")
            log_audit(conn, "LAB_RESULTS", "INGESTION_CSV_PARTIAL_FAILURE", user_name,
                      comment=str(e))
            return

        log_audit(conn, "LAB_RESULTS", "INGESTION_CSV", user_name, comment=f"{len(df)} lignes")
        st.success(f"{len(df)} lignes importées, statut initial PENDING (à valider techniquement). "
                    "Un code-barres a été assigné automatiquement à chaque nouvel échantillon "
                    "(voir page Sample Labels).")
        st.rerun()


def page_sample_labels(conn, user_name):
    """Digitalisation / traçabilité physique : émission des étiquettes
    code-barres pour les échantillons reçus."""
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
                      comment=f"{len(selected)} étiquette(s)")
            st.success(f"{len(selected)} label(s) marked as printed.")
            st.rerun()


def page_sample_scan(conn, user_name):
    """Lecture code-barres : recherche instantanée d'un échantillon.
    Fonctionne avec un scanner USB (champ texte = clavier) ou avec une
    photo prise via la caméra du téléphone."""
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
    """Vue d'ensemble visuelle de la chaîne de conservation : où sont
    physiquement rangés les échantillons, et lesquels ne le sont pas
    encore (repose sur la table STORAGE_LOCATIONS, alimentée depuis
    Sample Scan)."""
    st.title("🧊 STORAGE MAP")
    st.caption("Répartition des échantillons par congélateur / rack / boîte, "
                "et détection de ceux jamais rangés physiquement.")

    storage_df = get_all_current_storage_locations(conn)
    st.markdown(render_storage_map_html(storage_df), unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("⚠️ Échantillons jamais rangés")
    missing_df = get_samples_without_storage(conn)
    if missing_df.empty:
        st.success("✅ Tous les échantillons reçus ont une position de stockage enregistrée.")
    else:
        st.warning(f"{len(missing_df)} échantillon(s) reçu(s) mais jamais rangé(s) physiquement "
                    "(assignez une position depuis la page Sample Scan).")
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

    pending_df = compute_oor_flag(pending_df)
    pending_df.insert(0, "Validate", False)
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
                  comment=f"{n} résultats : {selected_ids}")
        st.success(f"{n} result(s) technically validated.")
        st.rerun()


# =====================================================================
# PAGES — BIOLOGIST
# =====================================================================
def page_biological_validation(conn, user_name):
    """Validation biologique avec signature électronique.

    Correction critique par rapport à la version précédente : on ne
    valide QUE les résultats explicitement cochés par le biologiste
    (comme pour la validation technique), jamais 'tout TECHNICAL_OK'
    en une requête aveugle. Le formulaire capture aussi le "meaning of
    signature" (raison) exigé par 21 CFR Part 11 §11.50, en plus du mot
    de passe."""
    st.title("🧪 BIOLOGICAL VALIDATION")
    render_critical_alert_banner(conn)

    df = read_full_results(conn)
    pending_df = df[df["status"] == "TECHNICAL_OK"].copy()
    if pending_df.empty:
        st.info("Aucun résultat en attente de validation biologique.")
        return

    pending_df = compute_oor_flag(pending_df)
    pending_df.insert(0, "Sign", False)
    display_cols = ["Sign", "result_id", "usubjid", "visit_code", "sample_id",
                     "test_code", "test_name", "result_value", "result_unit",
                     "Alerte", "technical_validated_by"]
    edited = st.data_editor(
        pending_df[display_cols], use_container_width=True, hide_index=True,
        disabled=[c for c in display_cols if c != "Sign"], key="bio_editor",
    )
    selected_ids = edited.loc[edited["Sign"], "result_id"].astype(int).tolist()
    st.write(f"**{len(selected_ids)} résultat(s)** sélectionné(s) pour signature.")

    st.markdown("---")
    st.subheader("🖋 Signature Électronique (21 CFR Part 11)")

    st.info(f"ℹ️ {ESIGNATURE_LEGAL_NOTICE}")

    with st.form("form_esignature_bio"):
        reason = st.selectbox("Meaning of this signature", SIGNATURE_REASONS)
        remarks = st.text_area("Remarques / observations",
                                placeholder="Ex: Résultats conformes au protocole. RAS.")
        st.caption("🔒 Conformité Part 11 : ressaisissez votre mot de passe pour valider et "
                    "signer l'acte médical. Seuls les résultats cochés ci-dessus seront signés.")
        password_input = st.text_input("Mot de passe", type="password")
        submit = st.form_submit_button("Signer et valider définitivement")

        if submit:
            if not selected_ids:
                st.error("Aucun résultat sélectionné — cochez au moins une ligne ci-dessus.")
            elif not password_input:
                st.error("Le mot de passe est obligatoire pour signer.")
            else:
                from auth import check_password, get_user_record
                record = get_user_record(conn, user_name)
                if record is None or not check_password(password_input, record["password_hash"]):
                    st.error("Mot de passe incorrect — signature refusée.")
                    log_audit(conn, "LAB_RESULTS", "BIOLOGICAL_SIGNATURE_FAILED", user_name,
                              comment=f"{len(selected_ids)} résultats visés")
                else:
                    n = mark_biological_validation(conn, selected_ids, user_name, reason, remarks)
                    log_audit(conn, "LAB_RESULTS", "BIOLOGICAL_SIGNATURE", user_name,
                              record_ref=str(selected_ids),
                              comment=f"{n} résultats signés. Reason={reason}. Remarks={remarks}")
                    st.success(f"✅ {n} résultat(s) validé(s) et signé(s) biologiquement avec succès !")

                    _maybe_auto_send_reports(conn, selected_ids, user_name)
                    st.rerun()


def _maybe_auto_send_reports(conn, result_ids, user_name):
    """Si activé dans Settings/Automation, génère et livre automatiquement
    le compte rendu des patients concernés par cette signature biologique
    dans la Messagerie interne des destinataires configurés (fonctionne
    toujours, sans SMTP). Un e-mail SMTP réel chiffré par mot de passe
    est envoyé EN PLUS si un mot de passe de chiffrement est configuré.
    Échoue silencieusement (log en audit trail) — ne doit jamais bloquer
    la validation elle-même, qui est déjà actée en base."""
    if get_setting(conn, "auto_send_reports_enabled", "0") != "1":
        return
    recipients = [r.strip() for r in (get_setting(conn, "notify_emails_physician", "") or "").split(",") if r.strip()]
    if not recipients:
        return

    report_password = get_setting(conn, "report_pdf_password")
    usubjids = get_usubjids_for_results(conn, result_ids)
    for usubjid in usubjids:
        subject = f"Compte rendu biologique — {usubjid}"
        body = f"Le compte rendu biologique de {usubjid} vient d'être validé et signé. Voir la pièce jointe."
        filename = f"{usubjid}_report_{date.today()}.pdf"

        # Livraison interne (toujours disponible, PDF non chiffré car déjà
        # protégé par l'authentification de l'application)
        pdf_bytes_internal = generate_patient_pdf_report(conn, usubjid, user_name)
        n_delivered = 0
        if pdf_bytes_internal is not None:
            n_delivered = send_internal_message_to_many(
                conn, recipients, subject, body,
                sender_username=user_name, sender_label="Automatisation LIMS",
                attachment_bytes=pdf_bytes_internal, attachment_name=filename,
                attachment_mimetype="application/pdf",
            )

        # E-mail SMTP réel EN PLUS, si un mot de passe de chiffrement est configuré
        also_emailed = False
        if report_password and pdf_bytes_internal is not None:
            pdf_bytes_encrypted = generate_patient_pdf_report(conn, usubjid, user_name, password=report_password)
            also_emailed = send_secure_report(
                subject=f"[BLOOD Study] {subject}",
                body=body + " (PDF protégé par mot de passe, communiqué séparément.)",
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
    """Vue visuelle du pipeline : le schéma d'ensemble avec les
    compteurs en direct, puis un tableau façon Kanban listant les
    échantillons à chaque étape — pour voir d'un coup d'œil où en est
    l'étude sans lire une ligne de log."""
    st.title("PROCESS FLOW")
    render_critical_alert_banner(conn)

    st.caption("Schéma vivant du pipeline : les compteurs reflètent l'état actuel de la base.")
    svg = render_pipeline_svg(conn)
    st.markdown(f'<div style="max-width:760px;margin:0 auto;">{svg}</div>', unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("Échantillons par étape")
    df = read_full_results(conn)
    if df.empty:
        st.info("Aucune donnée disponible.")
        return

    col_pending, col_tech, col_reviewed = st.columns(3)
    stage_cols = {
        "PENDING": (col_pending, "⏳ En attente technicien"),
        "TECHNICAL_OK": (col_tech, "🔬 En attente biologiste"),
        "REVIEWED": (col_reviewed, "✅ Validés"),
    }
    for status, (col, title) in stage_cols.items():
        with col:
            st.markdown(f"**{title}**")
            stage_df = df[df["status"] == status]
            if stage_df.empty:
                st.caption("Rien ici.")
            else:
                summary = stage_df.groupby("usubjid").size().reset_index(name="n_resultats")
                for _, row in summary.head(15).iterrows():
                    st.markdown(_html(f"""
                    <div class="search-result-card" style="padding:0.5rem 0.8rem; margin-bottom:0.5rem;">
                        <strong>{row['usubjid']}</strong><br/>
                        <span style="font-size:0.8rem;color:#7B8794;">{row['n_resultats']} résultat(s)</span>
                    </div>
                    """), unsafe_allow_html=True)
                if len(summary) > 15:
                    st.caption(f"... et {len(summary) - 15} de plus.")


def page_mon_compte(conn, user_name, role, full_name):
    """Espace personnel : informations de profil, poste, e-mail, et
    changement de mot de passe. Le nom d'utilisateur et le rôle restent
    en lecture seule ici — les modifier engage le contrôle d'accès de
    toute l'application, c'est pour ça que c'est réservé au CRO via
    User Management (voir la note affichée plus bas)."""
    st.title("🪪 MON COMPTE")

    cur = conn.execute(
        "SELECT full_name, email, job_title FROM USERS WHERE username = ?", (user_name,)
    )
    row = cur.fetchone()
    current_full_name, current_email, current_job_title = row if row else (full_name, "", "")

    col_info, col_badge = st.columns([2, 1])
    with col_info:
        with st.form("mon_compte_form"):
            new_full_name = st.text_input("Nom complet", value=current_full_name or "")
            new_job_title = st.text_input("Poste / fonction", value=current_job_title or "",
                                            placeholder="ex: Technicienne de laboratoire, Site FR-001")
            new_email = st.text_input("E-mail", value=current_email or "")
            submitted = st.form_submit_button("Enregistrer")
            if submitted:
                conn.execute(
                    "UPDATE USERS SET full_name = ?, job_title = ?, email = ? WHERE username = ?",
                    (new_full_name, new_job_title, new_email, user_name),
                )
                conn.commit()
                log_audit(conn, "USERS", "PROFILE_UPDATED", user_name)
                st.session_state.auth_full_name = new_full_name
                st.success("Profil mis à jour.")
                st.rerun()

    with col_badge:
        st.markdown(_html(f"""
        <div class="lims-panel">
            <h4>{current_full_name}</h4>
            <p style="color:#7B8794;font-size:0.85rem;margin:0.2rem 0;">@{user_name}</p>
            <span class="role-badge" style="background:#2E86C1;color:white;border:none;">{ROLE_LABELS.get(role, role)}</span>
        </div>
        """), unsafe_allow_html=True)

    st.caption("ℹ️ Le nom d'utilisateur et le rôle ne sont pas modifiables ici — un changement "
                "de rôle affecte les droits d'accès à toute l'application et reste réservé au "
                "CRO (page User Management), par principe de séparation des responsabilités.")

    st.markdown("---")
    st.subheader("🔒 Changer mon mot de passe")
    with st.form("mon_compte_password_form"):
        new_pwd = st.text_input("Nouveau mot de passe", type="password")
        confirm_pwd = st.text_input("Confirmer le nouveau mot de passe", type="password")
        pwd_submitted = st.form_submit_button("Mettre à jour le mot de passe")
        if pwd_submitted:
            if new_pwd != confirm_pwd:
                st.error("Les mots de passe ne correspondent pas.")
            else:
                ok, msg = validate_password_complexity(new_pwd)
                if not ok:
                    st.error(msg)
                else:
                    from auth import update_password
                    update_password(conn, user_name, new_pwd)
                    log_audit(conn, "USERS", "PASSWORD_CHANGED", user_name)
                    st.success("Mot de passe mis à jour.")


def page_messagerie(conn, user_name):
    """Messagerie interne : reçoit réellement les relances de
    l'automatisation (voir automation.py / mailbox.py) et permet
    d'envoyer un message à un autre utilisateur de l'application —
    même principe qu'une messagerie universitaire (compte du site,
    pas un vrai fournisseur e-mail externe)."""
    st.title("📧 MESSAGERIE")

    tab_inbox, tab_compose = st.tabs(["📥 Boîte de réception", "✉️ Nouveau message"])

    with tab_inbox:
        inbox = get_inbox(conn, user_name)
        if inbox.empty:
            st.info("Aucun message pour l'instant.")
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
            st.info("Aucun autre utilisateur actif à qui écrire.")
        else:
            with st.form("compose_form"):
                options = {f"{full_name} (@{uname}) — {ROLE_LABELS.get(role, role)}": uname
                           for uname, full_name, role in recipients}
                choice = st.selectbox("Destinataire", list(options.keys()))
                subject = st.text_input("Objet")
                body = st.text_area("Message")
                submitted = st.form_submit_button("Envoyer")
                if submitted:
                    if not subject or not body:
                        st.error("Objet et message sont obligatoires.")
                    else:
                        sender_row = conn.execute(
                            "SELECT full_name FROM USERS WHERE username = ?", (user_name,)
                        ).fetchone()
                        sender_label = sender_row[0] if sender_row else user_name
                        send_internal_message(conn, options[choice], subject, body,
                                               sender_username=user_name, sender_label=sender_label)
                        log_audit(conn, "MESSAGES", "MESSAGE_SENT", user_name,
                                  record_ref=options[choice])
                        st.success("Message envoyé.")
                        st.rerun()


def page_guide(role):
    """Aide contextuelle intégrée : condensé du guide complet, adapté à
    ce que CE rôle voit réellement. Objectif : qu'un nouvel utilisateur
    (ou un visiteur en démo) comprenne le 'pourquoi' sans quitter l'appli."""
    st.title("❓ GUIDE")
    st.caption("Un guide complet et détaillé (avec glossaire et scénario de démonstration) "
                "est disponible séparément — demandez-le si besoin.")

    st.subheader("Le principe général")
    st.write(
        "Chaque résultat suit un même trajet : **Ingestion** (CSV ou HL7) → "
        "**Échantillon + code-barres** → **Validation technique** → "
        "**Validation biologique (signature électronique)** → **Diffusion** "
        "(dossier patient, export sponsor, export réglementaire CDISC). "
        "Chaque étape existe pour une raison réglementaire précise : la double "
        "validation (technicien puis biologiste) est l'exigence classique de "
        "vérification indépendante en biologie médicale et en essai clinique."
    )

    role_help = {
        "LAB_TECH": [
            ("📥 Data Ingestion", "Importez le fichier CSV hebdomadaire du laboratoire central. "
             "Tout est vérifié AVANT d'être écrit en base — si une ligne est invalide, rien n'est importé."),
            ("🔌 HL7 Import", "Alternative au CSV si votre labo envoie directement un message HL7 v2."),
            ("🏷️ Sample Labels", "Imprimez les étiquettes code-barres des nouveaux échantillons reçus."),
            ("📷 Sample Scan", "Scannez (ou tapez) un code-barres pour retrouver instantanément un échantillon."),
            ("🧪 Technical Validation", "Premier contrôle qualité : cochez les résultats plausibles pour les faire avancer."),
        ],
        "BIOLOGIST": [
            ("🧬 Biological Validation", "Validation médicale finale. Cochez les résultats à signer, choisissez le motif "
             "de signature, ressaisissez votre mot de passe — cette signature a la même valeur qu'une signature manuscrite."),
            ("👤 Patient Records", "Consultez le dossier complet d'un patient et la tendance de ses biomarqueurs."),
        ],
        "PHYSICIAN": [
            ("👤 Patient Records", "Suivez vos patients inclus dans l'essai : résultats validés, tendance dans le temps, rapport PDF."),
            ("📝 Notes", "Ajoutez une observation clinique horodatée sur un résultat."),
        ],
        "CRO": [
            ("📊 Dashboard", "KPI globaux : nombre de résultats, patients, valeurs critiques, délai moyen de traitement."),
            ("🧭 Process Flow", "Le pipeline en un coup d'œil, avec les compteurs en direct."),
            ("👥 User Management", "Créez ou désactivez des comptes, réinitialisez un mot de passe."),
            ("📦 Export CDISC SDTM", "Export réglementaire (domaines LB, DM) + define.xml, prêt pour un dépôt."),
            ("🔒 Data Privacy", "Droits RGPD : export des données d'un patient, pseudonymisation, consentement."),
            ("🕵️ Audit Trail", "Le journal de TOUT ce qui s'est passé — non modifiable, non supprimable."),
        ],
        "SPONSOR": [
            ("📤 VINC extraction", "Votre extrait mensuel des résultats de la visite d'inclusion (VINC)."),
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
    st.caption("Où en est chaque visite dans le workflow de validation :")
    for visit_code_iter in patient_df["visit_code"].unique():
        visit_status_series = patient_df[patient_df["visit_code"] == visit_code_iter]["status"]
        # Le statut le "moins avancé" de la visite prime : une visite n'est
        # affichée comme validée que si TOUS ses résultats le sont.
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
    """Import de résultats via message HL7 v2 (ORU^R01) — voir
    hl7_import.py pour ce que ce parseur fait et ne fait pas
    (notamment : pas d'écoute réseau temps réel MLLP/RS-232)."""
    st.title("HL7 IMPORT")
    st.caption("Importe un message HL7 v2.x (segment ORU^R01) exporté depuis un automate ou "
                "un LIS existant. La correspondance avec vos visites d'essai (VINC/V1/V2) est "
                "choisie ci-dessous, car HL7 ne connaît pas votre plan de visites.")

    uploaded_file = st.file_uploader("Fichier HL7 (.hl7 ou .txt)", type=["hl7", "txt"])
    if uploaded_file is None:
        return

    raw_text = uploaded_file.getvalue().decode("utf-8", errors="replace")
    parsed = parse_oru_r01(raw_text)

    if parsed.warnings:
        with st.expander(f"⚠️ {len(parsed.warnings)} avertissement(s) de lecture", expanded=True):
            for w in parsed.warnings:
                st.write(f"- {w}")

    if not parsed.patient_identifier or not parsed.observations:
        st.error("Message illisible ou incomplet — impossible de continuer l'import.")
        return

    st.success(f"Patient détecté : {parsed.patient_identifier} "
               f"(sexe {parsed.patient_sex or '?'}, naissance {parsed.patient_birth_year or '?'})")
    st.subheader(f"{len(parsed.observations)} résultat(s) détecté(s)")
    preview_rows = []
    for obs in parsed.observations:
        low, high = parse_ref_range(obs.ref_range)
        preview_rows.append({
            "test_code": obs.test_code, "test_name": obs.test_name, "value": obs.value,
            "unit": obs.unit, "ref_low": low, "ref_high": high, "abnormal_flag": obs.abnormal_flag,
        })
    st.dataframe(pd.DataFrame(preview_rows), use_container_width=True)

    st.subheader("Rattachement à l'essai")
    with st.form("hl7_mapping_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            site_id = st.text_input("Site ID", placeholder="ex: FR-001")
        with col2:
            visit_code = st.selectbox("Visite", ["VINC", "V1", "V2"])
        with col3:
            visit_num = st.number_input("N° de visite", min_value=1, value=1, step=1)
        visit_date = st.date_input("Date de visite")
        sample_type = st.selectbox("Type d'échantillon", ["SERUM", "WHOLE_BLOOD", "PLASMA"])
        submitted = st.form_submit_button("Importer ces résultats")

        if submitted:
            if not site_id:
                st.error("Le site ID est obligatoire.")
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
                        st.warning(f"Valeur non numérique ignorée pour {obs.test_code} : '{obs.value}'")
                        continue
                    insert_lab_result(conn, visit_id, obs.test_code, obs.test_name, value,
                                       obs.unit, str(visit_date), low, high, sample_id)

                log_audit(conn, "LAB_RESULTS", "INGESTION_HL7", user_name,
                          record_ref=usubjid, comment=f"{len(parsed.observations)} résultats")
                st.success(f"{len(parsed.observations)} résultat(s) importé(s) pour {usubjid}, "
                           "statut PENDING (à valider techniquement).")
                st.rerun()


def page_data_privacy(conn, user_name):
    """Droits RGPD (export, pseudonymisation) et journal de consentement.
    Voir gdpr.py — cette page rapproche l'appli de la conformité RGPD
    mais ne remplace pas un hébergement certifié HDS pour de vraies
    données patients (voir README)."""
    st.title("DATA PRIVACY (RGPD)")

    tab_export, tab_anon, tab_consent = st.tabs(
        ["Droit d'accès (export)", "Pseudonymisation", "Journal de consentement"])

    with tab_export:
        st.caption("Génère un export complet des données détenues sur un patient (art. 15 RGPD).")
        df = read_full_results(conn)
        if df.empty:
            st.info("Aucune donnée disponible.")
        else:
            selected = st.selectbox("Patient", sorted(df["usubjid"].unique().tolist()), key="export_patient")
            if st.button("Générer l'export JSON"):
                import json
                data = export_patient_data(conn, selected)
                json_bytes = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode("utf-8")
                log_audit(conn, "PATIENTS", "GDPR_EXPORT", user_name, record_ref=selected)
                st.download_button("⬇️ Télécharger l'export", json_bytes,
                                    file_name=f"{selected}_gdpr_export_{date.today()}.json",
                                    mime="application/json")

    with tab_anon:
        st.caption("Pseudonymise un patient : généralise l'année de naissance par tranche de "
                    "5 ans et retire l'identifiant local. Les données cliniques (résultats, "
                    "validations) sont conservées — obligation légale de conservation des "
                    "données d'essai clinique. Action irréversible, enregistrée dans l'audit trail.")
        df = read_full_results(conn)
        if not df.empty:
            selected = st.selectbox("Patient", sorted(df["usubjid"].unique().tolist()), key="anon_patient")
            confirm = st.checkbox(f"Je confirme vouloir pseudonymiser {selected}")
            if st.button("Pseudonymiser", disabled=not confirm):
                ok = anonymize_patient(conn, selected, user_name)
                if ok:
                    st.success(f"{selected} a été pseudonymisé.")
                    st.rerun()

    with tab_consent:
        st.caption("Journal des statuts de consentement (le document signé lui-même n'est "
                    "PAS stocké ici, seule une référence).")
        df = read_full_results(conn)
        if not df.empty:
            with st.form("consent_form"):
                selected = st.selectbox("Patient", sorted(df["usubjid"].unique().tolist()), key="consent_patient")
                patient_id = df[df["usubjid"] == selected].iloc[0]["patient_id"]
                status = st.selectbox("Statut", ["GRANTED", "WITHDRAWN", "AMENDED"])
                doc_ref = st.text_input("Référence du document (ex: nom de fichier, ID e-CRF)")
                submitted = st.form_submit_button("Enregistrer")
                if submitted:
                    record_consent(conn, patient_id, selected, status, doc_ref, user_name)
                    st.success("Consentement enregistré.")
                    st.rerun()

        st.subheader("Historique")
        consent_df = get_all_consent(conn)
        if consent_df.empty:
            st.info("Aucun consentement enregistré.")
        else:
            st.dataframe(consent_df, use_container_width=True)


def page_export_sdtm(conn, user_name):
    st.title("CDISC SDTM EXPORT")
    st.caption("LB (Laboratory) and DM (Demographics) domains, plus a starter define.xml. "
                "See the Define-XML file itself for the disclaimer on what still needs to be "
                "completed before a real regulatory submission.")

    tab_lb, tab_dm, tab_define = st.tabs(["LB domain", "DM domain", "define.xml"])

    with tab_lb:
        df = read_full_results(conn)
        if df.empty:
            st.info("No data to export.")
        else:
            df = df.sort_values(["usubjid", "visit_num", "test_code"]).copy()
            df["LBSEQ"] = df.groupby("usubjid").cumcount() + 1
            sdtm = pd.DataFrame({
                "STUDYID": STUDYID, "DOMAIN": "LB", "USUBJID": df["usubjid"], "LBSEQ": df["LBSEQ"],
                "LBTESTCD": df["test_code"], "LBTEST": df["test_name"],
                "LBORRES": df["result_value"].astype(str), "LBORRESU": df["result_unit"],
                "LBSTRESN": df["result_value"], "LBSTRESU": df["result_unit"],
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
    """Réservée au CRO : création, désactivation, changement de rôle,
    réinitialisation de mot de passe. Sans cette page, la seule façon
    d'ajouter un utilisateur était d'éditer schema.sql à la main."""
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
    st.caption("Les relances sont livrées dans la Messagerie interne des destinataires "
                "choisis ci-dessous — ça fonctionne réellement, sans avoir besoin d'un "
                "vrai serveur e-mail. Un envoi SMTP réel est fait EN PLUS si vous configurez "
                "un jour de vrais identifiants dans .streamlit/secrets.toml.")

    enabled = get_setting(conn, "automation_enabled", "1") == "1"
    new_enabled = st.toggle("Enable automated reminders", value=enabled)

    threshold = int(get_setting(conn, "reminder_ingestion_days", "7"))
    new_threshold = st.number_input("Days before an 'ingestion overdue' reminder is sent",
                                      min_value=1, max_value=60, value=threshold)

    st.subheader("Destinataires")
    account_rows = list_active_usernames(conn)
    account_options = {f"{fname} (@{uname})": uname for uname, fname, role in account_rows}
    label_by_username = {uname: label for label, uname in account_options.items()}

    def _preselect(setting_key):
        raw = (get_setting(conn, setting_key, "") or "").split(",")
        return [label_by_username[u.strip()] for u in raw if u.strip() in label_by_username]

    lab_labels = st.multiselect("Relance import hebdomadaire en retard",
                                  list(account_options.keys()), default=_preselect("notify_emails_lab"))
    sponsor_labels = st.multiselect("Relance extrait VINC mensuel",
                                      list(account_options.keys()), default=_preselect("notify_emails_sponsor"))
    critical_labels = st.multiselect("Alerte valeurs critiques",
                                       list(account_options.keys()), default=_preselect("notify_emails_critical"))

    st.markdown("---")
    st.subheader("Diffusion automatique des comptes rendus")
    st.caption("Livré dans la Messagerie interne du destinataire, avec le PDF en pièce jointe. "
                "⚠️ Ce n'est pas une messagerie de santé sécurisée MSSanté — voir README.")
    auto_send_enabled = get_setting(conn, "auto_send_reports_enabled", "0") == "1"
    new_auto_send = st.toggle("Envoyer automatiquement le compte rendu après signature biologique",
                                value=auto_send_enabled)
    physician_labels = st.multiselect("Destinataires des comptes rendus",
                                        list(account_options.keys()), default=_preselect("notify_emails_physician"))

    if st.button("Save automation settings"):
        set_setting(conn, "automation_enabled", "1" if new_enabled else "0")
        set_setting(conn, "reminder_ingestion_days", str(new_threshold))
        set_setting(conn, "notify_emails_lab", ",".join(account_options[l] for l in lab_labels))
        set_setting(conn, "notify_emails_sponsor", ",".join(account_options[l] for l in sponsor_labels))
        set_setting(conn, "notify_emails_critical", ",".join(account_options[l] for l in critical_labels))
        set_setting(conn, "auto_send_reports_enabled", "1" if new_auto_send else "0")
        set_setting(conn, "notify_emails_physician", ",".join(account_options[l] for l in physician_labels))
        log_audit(conn, "SETTINGS", "UPDATE_AUTOMATION", user_name)
        st.success("Automation settings saved.")
        st.rerun()

    st.markdown("---")
    st.subheader("🔍 Aperçu (sans rien envoyer)")
    st.caption("Montre ce qui se déclencherait MAINTENANT, avec vos données actuelles.")
    if st.button("Générer l'aperçu"):
        preview = preview_automation(conn)
        for item in preview:
            icon = "🔔" if item["would_trigger"] else "✅"
            with st.container(border=True):
                st.markdown(f"{icon} **{item['title']}**")
                st.caption(item["detail"])
                if item["would_trigger"]:
                    recipients = [r.strip() for r in item["recipients"] if r.strip()]
                    if recipients:
                        st.write(f"→ Serait livré dans la messagerie de : {', '.join(recipients)}")
                    else:
                        st.write("→ Se déclencherait, mais aucun destinataire n'est configuré ci-dessus.")

    st.markdown("---")
    st.caption("Limite connue : les relances sont vérifiées à chaque chargement de page (pas de "
                "vrai cron serveur sur le tier gratuit Streamlit) — mais la livraison en "
                "messagerie interne, elle, fonctionne réellement dès qu'une page est ouverte.")


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
    st.title("RESULTS OF THE VINC VISIT")
    render_sponsor_reminder(conn)
    df = read_full_results(conn)
    vinc_df_all = df[df["visit_code"] == "VINC"]
    if vinc_df_all.empty:
        st.info("No VINC results are available at present.")
        return

    vinc_df = filter_reviewed_only(vinc_df_all)
    n_excluded = len(vinc_df_all) - len(vinc_df)
    if n_excluded > 0:
        st.warning(f"⚠️ {n_excluded} VINC result(s) are not yet biologically validated "
                   "(status PENDING or TECHNICAL_OK) and have been excluded from this "
                   "extract. Contact the biologist to finalize validation before the "
                   "monthly export — see risk #1 of the risk analysis.")
    if vinc_df.empty:
        st.info("No biologically validated (REVIEWED) VINC results are available yet.")
        return

    vinc_df = compute_oor_flag(vinc_df)
    st.dataframe(vinc_df, use_container_width=True)

    export_df = vinc_df[["usubjid", "site_id", "visit_code", "visit_date",
                          "test_code", "test_name", "result_value", "result_unit", "result_date"]]
    csv_buffer = io.StringIO()
    export_df.to_csv(csv_buffer, index=False)
    filename = f"extraction_VINC_{date.today()}.csv"

    col_csv, col_pdf = st.columns(2)
    with col_csv:
        if st.download_button("⬇️ Download the VINC extract (CSV)", csv_buffer.getvalue(), file_name=filename):
            action = "CONSULTATION_VINC_SPONSOR" if role == "SPONSOR" else "EXPORT_VINC_CRO"
            log_audit(conn, "LAB_RESULTS", action, user_name, comment=f"{len(export_df)} lignes")
    with col_pdf:
        pdf_bytes = generate_vinc_pdf_report(conn, vinc_df, user_name)
        pdf_filename = f"VINC_report_{date.today()}.pdf"
        if st.download_button("📄 Download the VINC report (PDF)", pdf_bytes,
                                file_name=pdf_filename, mime="application/pdf"):
            action = "EXPORT_PDF_VINC_SPONSOR" if role == "SPONSOR" else "EXPORT_PDF_VINC_CRO"
            log_audit(conn, "LAB_RESULTS", action, user_name, comment=f"{len(vinc_df)} lignes, {pdf_filename}")


def page_remarks(conn, user_name, role):
    st.title("COMMENT ON THE RESULTS")
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

    username, role, full_name = sidebar_user_identification(conn)
    if not username or role is None:
        render_landing_page(conn)
        return

    check_and_send_reminders(conn)

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
        if p == "Messagerie" and unread:
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
    elif page == "Audit Trail":
        page_audit_trail(conn)
    elif page == "Guide":
        page_guide(role)
    elif page == "Messagerie":
        page_messagerie(conn, username)
    elif page == "Mon Compte":
        page_mon_compte(conn, username, role, full_name)


if __name__ == "__main__":
    main()
