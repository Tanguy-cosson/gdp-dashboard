import io
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from audit import log_audit
from auth import sidebar_user_identification
from automation import check_and_send_reminders
from barcode_utils import (decode_barcode_from_image, generate_barcode_png,
                            generate_sample_label_pdf, zbar_available)
from business_logic import (build_patients_matrix, compute_oor_flag, compute_tat_hours,
                             validate_ingestion_dataframe)
from constants import (CRITICAL_FLAG, NORMAL_FLAG, OOR_FLAG, PAGE_PERMISSIONS,
                        ROLE_LABELS, SIGNATURE_REASONS, STUDYID)
from db import (add_storage_location, count_by_status, find_sample_by_barcode,
                get_connection, get_current_storage_location, get_or_create_patient,
                get_or_create_sample, get_or_create_site, get_or_create_visit,
                get_samples_pending_labels, get_setting, insert_lab_result, insert_remark,
                mark_biological_validation, mark_labels_printed, mark_technical_validation,
                read_audit_trail, read_full_results, read_remarks, set_setting)
from pdf_reports import generate_patient_pdf_report, generate_vinc_pdf_report
from ui import _html, inject_custom_css, kpi_card, render_landing_page, render_top_banner

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
                    st.rerun()


# =====================================================================
# PAGES — CRO / DASHBOARD
# =====================================================================
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


def page_export_sdtm(conn, user_name):
    st.title("CDISC SDTM EXPORT – LB Domain")
    df = read_full_results(conn)
    if df.empty:
        st.info("No data to export.")
        return

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
    if st.download_button("Download the LB domain (CSV)", csv_buffer.getvalue(),
                            file_name=f"SDTM_LB_BLOOD_{date.today()}.csv"):
        log_audit(conn, "LAB_RESULTS", "EXPORT_SDTM_LB", user_name, comment=f"{len(sdtm)} lignes")


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


def page_automation(conn, user_name):
    st.title("AUTOMATION")
    st.caption("Automatic e-mail reminders. Requires SMTP credentials configured in "
                ".streamlit/secrets.toml (see README) — without them, this page still "
                "lets you configure recipients and thresholds, but no e-mail will be sent.")

    enabled = get_setting(conn, "automation_enabled", "1") == "1"
    new_enabled = st.toggle("Enable automated reminders", value=enabled)

    threshold = int(get_setting(conn, "reminder_ingestion_days", "7"))
    new_threshold = st.number_input("Days before an 'ingestion overdue' reminder is sent",
                                      min_value=1, max_value=60, value=threshold)

    st.subheader("Recipients")
    lab_emails = st.text_input("Lab ingestion reminders (comma-separated)",
                                 value=get_setting(conn, "notify_emails_lab", "") or "")
    sponsor_emails = st.text_input("Sponsor VINC reminders (comma-separated)",
                                     value=get_setting(conn, "notify_emails_sponsor", "") or "")
    critical_emails = st.text_input("Critical value alerts (comma-separated)",
                                      value=get_setting(conn, "notify_emails_critical", "") or "")

    if st.button("Save automation settings"):
        set_setting(conn, "automation_enabled", "1" if new_enabled else "0")
        set_setting(conn, "reminder_ingestion_days", str(new_threshold))
        set_setting(conn, "notify_emails_lab", lab_emails)
        set_setting(conn, "notify_emails_sponsor", sponsor_emails)
        set_setting(conn, "notify_emails_critical", critical_emails)
        log_audit(conn, "SETTINGS", "UPDATE_AUTOMATION", user_name)
        st.success("Automation settings saved.")
        st.rerun()

    st.markdown("---")
    st.caption("Known limitation: reminders are checked once per page load (no server-side "
                "cron on the free Streamlit Community Cloud tier) — see README for how to add "
                "a true scheduled trigger via GitHub Actions.")


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
    vinc_df = df[df["visit_code"] == "VINC"]
    if vinc_df.empty:
        st.info("No VINC results are available at present.")
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
    page = st.sidebar.radio("Navigation", allowed_pages)
    st.sidebar.markdown("---")
    st.sidebar.caption("Access in accordance with the principle of least privilege "
                        "(21 CFR Part 11 / Annex 11 §12). "
                        f"Session expires after 30 min of inactivity.")

    if page == "Data Ingestion":
        page_ingestion(conn, username)
    elif page == "Sample Labels":
        page_sample_labels(conn, username)
    elif page == "Sample Scan":
        page_sample_scan(conn, username)
    elif page == "Technical Validation":
        page_technical_validation(conn, username)
    elif page == "Biological Validation":
        page_biological_validation(conn, username)
    elif page == "Dashboard":
        page_dashboard(conn)
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
    elif page == "Settings":
        page_settings(conn, username)
    elif page == "Automation":
        page_automation(conn, username)
    elif page == "Audit Trail":
        page_audit_trail(conn)


if __name__ == "__main__":
    main()
