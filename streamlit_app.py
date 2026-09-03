import base64
import io
import os
import sqlite3
from datetime import datetime, date

import pandas as pd
import streamlit as st

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blood_study.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
STUDYID = "BLOOD"

ROLE_LABELS = {
    "CENTRAL_LAB": "Laboratoire centralisé (saisie uniquement)",
    "CRO": "CRO Clinical Services (lecture + revue)",
    "SPONSOR": "Promoteur LPH (lecture seule)",
}

PAGE_PERMISSIONS = {
    "CENTRAL_LAB": ["Ingestion"],
    "CRO": ["Tableau de bord", "Patients", "File de revue", "Extraction VINC",
            "Remarques", "Export CDISC SDTM", "Paramètres", "Audit Trail"],
    "SPONSOR": ["Extraction VINC", "Remarques"],
}

st.set_page_config(page_title="Projet BLOOD", page_icon="🩸", layout="wide")


# ---------------------------------------------------------------------
# DESIGN
# ---------------------------------------------------------------------

def inject_custom_css():
    st.markdown("""
    <style>
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #EAF3FB 0%, #FFFFFF 100%);
        border-right: 1px solid #D6E4F0;
    }
    .stButton>button, .stDownloadButton>button {
        border-radius: 8px;
        border: 1.5px solid #2E86C1;
        color: #2E86C1;
        background-color: #FFFFFF;
        font-weight: 600;
        transition: all 0.15s ease;
    }
    .stButton>button:hover, .stDownloadButton>button:hover {
        background-color: #2E86C1;
        color: #FFFFFF;
    }
    [data-testid="stMetricValue"] {
        color: #1B4F72;
        font-weight: 700;
    }
    h1, h2, h3 { letter-spacing: -0.3px; }
    .blood-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: linear-gradient(90deg, #1B4F72 0%, #2E86C1 100%);
        padding: 1.1rem 1.6rem;
        border-radius: 10px;
        margin-bottom: 1.6rem;
        color: white;
    }
    .blood-header-left { display: flex; align-items: center; gap: 0.9rem; }
    .blood-header img { height: 42px; border-radius: 6px; background: white; padding: 3px; }
    .blood-title { font-size: 1.5rem; font-weight: 700; margin: 0; color: white; }
    .blood-subtitle { font-size: 0.85rem; opacity: 0.9; }
    .notif-chip {
        background-color: rgba(255,255,255,0.15);
        border: 1px solid rgba(255,255,255,0.4);
        border-radius: 999px;
        padding: 0.35rem 0.9rem;
        font-size: 0.85rem;
        font-weight: 600;
        color: white;
    }
    .role-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
        background-color: #D6EAF8;
        color: #1B4F72;
        border: 1px solid #AED6F1;
        margin-top: 0.4rem;
    }
    .oor-flag {
        color: #B03A2E;
        font-weight: 700;
    }
    </style>
    """, unsafe_allow_html=True)


def render_header(conn, subtitle, notif_count=None):
    company_name = get_setting(conn, "company_name") or "Clinical Services"
    logo_b64 = get_setting(conn, "logo_base64")

    if logo_b64:
        logo_html = f'<img src="data:image/png;base64,{logo_b64}" />'
    else:
        logo_html = '<div style="font-size:2rem;">🩸</div>'

    notif_html = ""
    if notif_count is not None:
        notif_html = f'<div class="notif-chip">🔔 {notif_count} en attente de revue</div>'

    st.markdown(f"""
    <div class="blood-header">
        <div class="blood-header-left">
            {logo_html}
            <div>
                <div class="blood-title">{company_name} · Projet BLOOD</div>
                <div class="blood-subtitle">{subtitle}</div>
            </div>
        </div>
        {notif_html}
    </div>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------
# CONNEXION A LA BASE
# ---------------------------------------------------------------------

@st.cache_resource
def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    return conn


# ---------------------------------------------------------------------
# PARAMETRES (branding)
# ---------------------------------------------------------------------

def get_setting(conn, key):
    cur = conn.execute("SELECT setting_value FROM SETTINGS WHERE setting_key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else None


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO SETTINGS (setting_key, setting_value) VALUES (?, ?) "
        "ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------------
# UTILISATEURS ET ROLES
# ---------------------------------------------------------------------

def get_user_role(conn, username):
    cur = conn.execute("SELECT role, full_name FROM USERS WHERE username = ?", (username,))
    row = cur.fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def sidebar_user_identification(conn):
    st.sidebar.markdown("### 🔐 Identification")
    if "user_name" not in st.session_state:
        st.session_state.user_name = ""
    username = st.sidebar.text_input(
        "Identifiant utilisateur",
        value=st.session_state.user_name,
        placeholder="ex: labcentral / cro_arc / sponsor_lph",
    )
    st.session_state.user_name = username

    if not username:
        st.sidebar.info("Renseignez votre identifiant pour continuer.")
        return None, None, None

    role, full_name = get_user_role(conn, username)
    if role is None:
        st.sidebar.error("Identifiant inconnu. Contactez le Data Manager.")
        return username, None, None

    st.sidebar.success(full_name)
    st.sidebar.markdown(f'<span class="role-badge">{ROLE_LABELS.get(role, role)}</span>',
                        unsafe_allow_html=True)
    return username, role, full_name


# ---------------------------------------------------------------------
# ECRITURE EN BASE
# ---------------------------------------------------------------------

def log_audit(conn, table_name, action, user_name, comment=None):
    conn.execute(
        "INSERT INTO AUDIT_TRAIL (table_name, action, user_name, event_timestamp, comment) VALUES (?, ?, ?, ?, ?)",
        (table_name, action, user_name, datetime.now().isoformat(timespec="seconds"), comment),
    )
    conn.commit()


def get_or_create_site(conn, site_id, site_name=None, country=None):
    cur = conn.execute("SELECT site_id FROM SITES WHERE site_id = ?", (site_id,))
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO SITES (site_id, site_name, country) VALUES (?, ?, ?)",
            (site_id, site_name or site_id, country or "UNK"),
        )
        conn.commit()
    return site_id


def get_or_create_patient(conn, patient_id, usubjid, site_id, subjid, sex, birth_year):
    cur = conn.execute("SELECT patient_id FROM PATIENTS WHERE patient_id = ?", (patient_id,))
    if cur.fetchone() is None:
        conn.execute(
            "INSERT INTO PATIENTS (patient_id, usubjid, site_id, subjid, sex, birth_year) VALUES (?, ?, ?, ?, ?, ?)",
            (patient_id, usubjid, site_id, subjid, sex, birth_year),
        )
        conn.commit()
    return patient_id


def get_or_create_visit(conn, patient_id, visit_code, visit_date, visit_num):
    cur = conn.execute(
        "SELECT visit_id FROM VISITES WHERE patient_id = ? AND visit_code = ?",
        (patient_id, visit_code),
    )
    row = cur.fetchone()
    if row is not None:
        return row[0]
    cur = conn.execute(
        "INSERT INTO VISITES (patient_id, visit_code, visit_date, visit_num) VALUES (?, ?, ?, ?)",
        (patient_id, visit_code, visit_date, visit_num),
    )
    conn.commit()
    return cur.lastrowid


def insert_lab_result(conn, visit_id, test_code, test_name, value, unit, result_date,
                      ref_low=None, ref_high=None):
    conn.execute(
        "INSERT INTO LAB_RESULTS (visit_id, test_code, test_name, result_value, result_unit, "
        "result_date, ref_low, ref_high) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (visit_id, test_code, test_name, value, unit, result_date, ref_low, ref_high),
    )
    conn.commit()


def insert_remark(conn, result_id, remark_text, user_name):
    conn.execute(
        "INSERT INTO REMARKS (result_id, remark_text, user_name) VALUES (?, ?, ?)",
        (result_id, remark_text, user_name),
    )
    conn.commit()


def mark_results_reviewed(conn, result_ids, user_name):
    if not result_ids:
        return 0
    placeholders = ",".join("?" for _ in result_ids)
    conn.execute(
        f"UPDATE LAB_RESULTS SET status = 'REVIEWED' WHERE result_id IN ({placeholders})",
        result_ids,
    )
    conn.commit()
    log_audit(conn, "LAB_RESULTS", "MARK_REVIEWED", user_name,
             f"{len(result_ids)} résultats : {result_ids}")
    return len(result_ids)


# ---------------------------------------------------------------------
# LECTURE
# ---------------------------------------------------------------------

def read_full_results(conn):
    return pd.read_sql_query("SELECT * FROM V_LAB_RESULTS_FULL", conn)


def read_remarks(conn):
    return pd.read_sql_query(
        """
        SELECT r.remark_id, r.result_id, lr.test_code, lr.test_name, p.usubjid,
               r.remark_text, r.user_name, r.created_at
        FROM REMARKS r
        JOIN LAB_RESULTS lr ON r.result_id = lr.result_id
        JOIN VISITES v ON lr.visit_id = v.visit_id
        JOIN PATIENTS p ON v.patient_id = p.patient_id
        ORDER BY r.created_at DESC
        """,
        conn,
    )


def read_audit_trail(conn):
    return pd.read_sql_query(
        "SELECT * FROM AUDIT_TRAIL ORDER BY event_timestamp DESC, audit_id DESC", conn
    )


def count_pending_results(conn):
    cur = conn.execute("SELECT COUNT(*) FROM LAB_RESULTS WHERE status = 'PENDING'")
    return cur.fetchone()[0]


# ---------------------------------------------------------------------
# CALCULS METIER
# ---------------------------------------------------------------------

def compute_oor_flag(df):
    """Ajoute une colonne 'Alerte' : hors norme si ref_low/ref_high sont definis
    et que result_value sort de cet intervalle."""
    df = df.copy()

    def flag(row):
        if pd.isna(row.get("ref_low")) or pd.isna(row.get("ref_high")):
            return ""
        if pd.isna(row.get("result_value")):
            return ""
        if row["result_value"] < row["ref_low"] or row["result_value"] > row["ref_high"]:
            return "⚠️ Hors norme"
        return "✅ Normal"

    df["Alerte"] = df.apply(flag, axis=1)
    return df


def build_patients_matrix(df):
    """Construit un tableau 1 ligne par patient, avec une colonne par visite
    indiquant si des resultats existent (Oui/Non)."""
    if df.empty:
        return pd.DataFrame(columns=["usubjid", "site_name", "VINC", "V1", "V2"])

    pivot = df.pivot_table(
        index=["usubjid", "site_name"], columns="visit_code",
        values="result_id", aggfunc="count", fill_value=0,
    ).reset_index()

    for col in ["VINC", "V1", "V2"]:
        if col not in pivot.columns:
            pivot[col] = 0
        pivot[col] = pivot[col].apply(lambda x: "✅" if x > 0 else "—")

    return pivot[["usubjid", "site_name", "VINC", "V1", "V2"]]


# ---------------------------------------------------------------------
# RAPPELS
# ---------------------------------------------------------------------

def days_since_last_action(conn, actions):
    placeholders = ",".join("?" for _ in actions)
    cur = conn.execute(
        f"SELECT MAX(event_timestamp) FROM AUDIT_TRAIL WHERE action IN ({placeholders})",
        actions,
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    last_dt = datetime.fromisoformat(row[0])
    return (datetime.now() - last_dt).days


def render_central_lab_reminder(conn):
    days = days_since_last_action(conn, ["INGESTION_CSV"])
    if days is None:
        st.warning("⏰ Aucun dépôt n'a encore été effectué. Le protocole prévoit un dépôt "
                   "chaque semaine — merci de déposer le premier fichier.")
    elif days >= 7:
        st.warning(f"⏰ Rappel : le dernier dépôt date de {days} jour(s). "
                   "Merci de déposer le nouveau fichier hebdomadaire (cf. SOP §4).")
    else:
        st.success(f"✅ Dernier dépôt il y a {days} jour(s). Vous êtes à jour.")


def render_sponsor_reminder(conn):
    cur = conn.execute(
        "SELECT MAX(event_timestamp) FROM AUDIT_TRAIL "
        "WHERE action IN ('EXPORT_VINC_CRO','CONSULTATION_VINC_SPONSOR') "
        "AND strftime('%Y-%m', event_timestamp) = strftime('%Y-%m', 'now')"
    )
    row = cur.fetchone()
    if row is None or row[0] is None:
        st.warning("⏰ L'extraction VINC de ce mois-ci n'a pas encore été téléchargée "
                   "ni consultée (cf. SOP §5). Pensez à la transmettre.")
    else:
        st.success("✅ Extraction VINC déjà transmise/consultée ce mois-ci.")


# ---------------------------------------------------------------------
# PAGES - CENTRAL LAB
# ---------------------------------------------------------------------

def page_ingestion(conn, user_name):
    st.title("📥 Ingestion des données")
    render_central_lab_reminder(conn)
    st.caption("Ce module permet uniquement d'AJOUTER des résultats. Aucune modification "
               "ni suppression n'est possible depuis cet écran. Colonnes optionnelles "
               "ref_low / ref_high : bornes de normalité pour la détection automatique "
               "des valeurs hors norme.")

    uploaded_file = st.file_uploader("Fichier CSV à importer", type=["csv"])

    if uploaded_file is not None:
        df = pd.read_csv(uploaded_file, sep=None, engine="python", encoding="utf-8-sig")
        st.dataframe(df.head(10), use_container_width=True)

        has_ref = "ref_low" in df.columns and "ref_high" in df.columns

        if st.button("Importer en base", disabled=not user_name):
            for _, row in df.iterrows():
                site_id = get_or_create_site(conn, row["site_id"])
                patient_id = get_or_create_patient(
                    conn, row["patient_id"], row["usubjid"], site_id,
                    row["subjid"], row["sex"], int(row["birth_year"]))
                visit_id = get_or_create_visit(
                    conn, patient_id, row["visit_code"], row["visit_date"], int(row["visit_num"]))
                ref_low = float(row["ref_low"]) if has_ref and pd.notna(row["ref_low"]) else None
                ref_high = float(row["ref_high"]) if has_ref and pd.notna(row["ref_high"]) else None
                insert_lab_result(
                    conn, visit_id, row["test_code"], row["test_name"],
                    float(row["result_value"]), row["result_unit"], row["result_date"],
                    ref_low, ref_high)
            log_audit(conn, "LAB_RESULTS", "INGESTION_CSV", user_name, f"{len(df)} lignes")
            st.success(f"{len(df)} lignes importées.")
            st.rerun()


# ---------------------------------------------------------------------
# PAGES - CRO
# ---------------------------------------------------------------------

def page_dashboard(conn):
    st.title("📊 Tableau de bord")
    with st.container(border=True):
        render_central_lab_reminder(conn)

    df = read_full_results(conn)
    if df.empty:
        st.info("Aucune donnée. Demandez au laboratoire centralisé d'importer un fichier.")
        return

    df = compute_oor_flag(df)
    n_oor = (df["Alerte"] == "⚠️ Hors norme").sum()

    with st.container(border=True):
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Résultats en base", len(df))
        col2.metric("Patients", df["usubjid"].nunique())
        col3.metric("Sites", df["site_id"].nunique())
        col4.metric("Valeurs hors norme", int(n_oor))

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Résultats par visite")
        st.bar_chart(df.groupby("visit_code").size())
    with col_right:
        st.subheader("Résultats par site")
        st.bar_chart(df.groupby("site_name").size())

    st.subheader("Données détaillées")
    st.dataframe(df, use_container_width=True)


def page_patients(conn):
    st.title("🧑‍⚕️ Suivi des patients")
    df = read_full_results(conn)
    matrix = build_patients_matrix(df)
    if matrix.empty:
        st.info("Aucun patient en base pour le moment.")
        return
    st.caption("Vue d'ensemble des visites documentées par patient.")
    st.dataframe(matrix, use_container_width=True, hide_index=True)


def page_review_queue(conn, user_name):
    st.title("🧾 File de revue")
    st.caption("Les résultats importés sont automatiquement marqués 'PENDING'. "
               "Cochez ceux que vous avez contrôlés puis validez pour les passer à 'REVIEWED'.")

    df = read_full_results(conn)
    pending_df = df[df["status"] == "PENDING"].copy()

    if pending_df.empty:
        st.success("✅ Aucun résultat en attente de revue.")
        return

    pending_df = compute_oor_flag(pending_df)
    pending_df.insert(0, "Revu", False)

    display_cols = ["Revu", "result_id", "usubjid", "visit_code", "test_code",
                    "test_name", "result_value", "result_unit", "Alerte"]

    edited = st.data_editor(
        pending_df[display_cols],
        use_container_width=True,
        hide_index=True,
        disabled=[c for c in display_cols if c != "Revu"],
        key="review_editor",
    )

    selected_ids = edited.loc[edited["Revu"], "result_id"].astype(int).tolist()

    if st.button(f"Marquer {len(selected_ids)} résultat(s) comme revus",
                disabled=len(selected_ids) == 0):
        n = mark_results_reviewed(conn, selected_ids, user_name)
        st.success(f"{n} résultat(s) marqué(s) comme revus.")
        st.rerun()


def page_export_sdtm(conn, user_name):
    st.title("🧬 Export CDISC SDTM - Domaine LB")
    df = read_full_results(conn)
    if df.empty:
        st.info("Aucune donnée à exporter.")
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
    if st.download_button("Télécharger le domaine LB (CSV)", csv_buffer.getvalue(),
                          file_name=f"SDTM_LB_BLOOD_{date.today()}.csv"):
        log_audit(conn, "LAB_RESULTS", "EXPORT_SDTM_LB", user_name, f"{len(sdtm)} lignes")


def page_settings(conn, user_name):
    st.title("⚙️ Paramètres")
    st.caption("Personnalisez l'apparence de l'application pour votre entreprise.")

    current_name = get_setting(conn, "company_name") or "Clinical Services"
    current_logo = get_setting(conn, "logo_base64")

    with st.container(border=True):
        st.subheader("Identité de l'entreprise")
        new_name = st.text_input("Nom de l'entreprise affiché dans l'en-tête", value=current_name)

        if current_logo:
            st.write("Logo actuel :")
            st.markdown(f'<img src="data:image/png;base64,{current_logo}" style="height:60px;">',
                       unsafe_allow_html=True)

        uploaded_logo = st.file_uploader("Nouveau logo (PNG ou JPG, fond transparent conseillé)",
                                         type=["png", "jpg", "jpeg"])

        if st.button("Enregistrer les paramètres"):
            set_setting(conn, "company_name", new_name)
            if uploaded_logo is not None:
                logo_bytes = uploaded_logo.read()
                logo_b64 = base64.b64encode(logo_bytes).decode("utf-8")
                set_setting(conn, "logo_base64", logo_b64)
            log_audit(conn, "SETTINGS", "UPDATE_BRANDING", user_name,
                     f"company_name={new_name}")
            st.success("Paramètres enregistrés.")
            st.rerun()


def page_audit_trail(conn):
    st.title("🕵️ Piste d'audit")
    df = read_audit_trail(conn)
    if df.empty:
        st.info("Aucune action journalisée.")
        return
    st.dataframe(df, use_container_width=True)


# ---------------------------------------------------------------------
# PAGES - PARTAGEES ENTRE CRO ET SPONSOR
# ---------------------------------------------------------------------

def page_extraction_vinc(conn, user_name, role):
    st.title("📤 Résultats visite VINC")
    render_sponsor_reminder(conn)

    df = read_full_results(conn)
    vinc_df = df[df["visit_code"] == "VINC"]

    if vinc_df.empty:
        st.info("Aucun résultat VINC disponible pour le moment.")
        return

    vinc_df = compute_oor_flag(vinc_df)
    st.dataframe(vinc_df, use_container_width=True)

    export_df = vinc_df[["usubjid", "site_id", "visit_code", "visit_date",
                         "test_code", "test_name", "result_value", "result_unit", "result_date"]]
    csv_buffer = io.StringIO()
    export_df.to_csv(csv_buffer, index=False)
    filename = f"extraction_VINC_{date.today()}.csv"

    if st.download_button("⬇️ Télécharger l'extraction VINC", csv_buffer.getvalue(),
                          file_name=filename):
        action = "CONSULTATION_VINC_SPONSOR" if role == "SPONSOR" else "EXPORT_VINC_CRO"
        log_audit(conn, "LAB_RESULTS", action, user_name, f"{len(export_df)} lignes")


def page_remarks(conn, user_name, role):
    st.title("💬 Remarques sur les résultats")
    df = read_full_results(conn)

    if role == "CRO":
        if df.empty:
            st.info("Aucun résultat disponible pour ajouter une remarque.")
        else:
            with st.container(border=True):
                st.subheader("Ajouter une remarque")
                options = df.apply(
                    lambda r: f"{r['result_id']} — {r['usubjid']} — {r['test_code']} ({r['visit_code']})",
                    axis=1,
                ).tolist()
                choice = st.selectbox("Résultat concerné", options)
                result_id = int(choice.split(" — ")[0])
                remark_text = st.text_area("Votre remarque")
                if st.button("Enregistrer la remarque", disabled=not remark_text):
                    insert_remark(conn, result_id, remark_text, user_name)
                    log_audit(conn, "REMARKS", "ADD_REMARK", user_name, f"result_id={result_id}")
                    st.success("Remarque enregistrée.")
                    st.rerun()

    st.subheader("Remarques existantes")
    remarks_df = read_remarks(conn)
    if remarks_df.empty:
        st.info("Aucune remarque enregistrée pour le moment.")
    else:
        st.dataframe(remarks_df, use_container_width=True)


# ---------------------------------------------------------------------
# POINT D'ENTREE
# ---------------------------------------------------------------------

def main():
    inject_custom_css()
    conn = get_connection()
    username, role, full_name = sidebar_user_identification(conn)

    if not username or role is None:
        render_header(conn, "Application de gestion des résultats d'analyses sanguines")
        st.warning("Identifiez-vous dans la barre latérale avec un identifiant valide "
                   "(ex: labcentral, cro_arc, sponsor_lph).")
        return

    notif_count = count_pending_results(conn) if role == "CRO" else None
    render_header(conn, f"{full_name} · {ROLE_LABELS.get(role, role)}", notif_count)

    allowed_pages = PAGE_PERMISSIONS.get(role, [])
    page = st.sidebar.radio("Navigation", allowed_pages)

    st.sidebar.markdown("---")
    st.sidebar.caption("Accès conforme au principe de moindre privilège "
                       "(21 CFR Part 11 / Annexe 11 §12).")

    if page == "Ingestion":
        page_ingestion(conn, username)
    elif page == "Tableau de bord":
        page_dashboard(conn)
    elif page == "Patients":
        page_patients(conn)
    elif page == "File de revue":
        page_review_queue(conn, username)
    elif page == "Extraction VINC":
        page_extraction_vinc(conn, username, role)
    elif page == "Remarques":
        page_remarks(conn, username, role)
    elif page == "Export CDISC SDTM":
        page_export_sdtm(conn, username)
    elif page == "Paramètres":
        page_settings(conn, username)
    elif page == "Audit Trail":
        page_audit_trail(conn)


if __name__ == "__main__":
    main()