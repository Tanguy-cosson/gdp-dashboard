import base64
import io
import os
import sqlite3
from datetime import datetime, date
 
import bcrypt
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
 
def _html(text):
    """Retire toute indentation de chaque ligne d'un bloc HTML avant de l'envoyer
    a st.markdown. Indispensable : Markdown transforme automatiquement toute ligne
    indentee de 4 espaces ou plus en bloc de code brut, meme en mode
    unsafe_allow_html=True. Ecrire du HTML indente en Python (pour la lisibilite du
    code source) sans cette fonction fait donc afficher le code au lieu de la page."""
    return "\n".join(line.strip() for line in text.strip("\n").splitlines())
 
 
NETWORK_SVG = """<svg viewBox="0 0 500 300" xmlns="http://www.w3.org/2000/svg">
<g stroke="#FFFFFF" stroke-width="0.9" opacity="0.55">
<line x1="32.9" y1="23.5" x2="132.5" y2="20.4"/><line x1="32.9" y1="23.5" x2="37.9" y2="125.6"/><line x1="32.9" y1="23.5" x2="106.2" y2="101.4"/><line x1="132.5" y1="20.4" x2="210.1" y2="32.1"/><line x1="132.5" y1="20.4" x2="106.2" y2="101.4"/><line x1="210.1" y1="32.1" x2="269.6" y2="37.8"/><line x1="210.1" y1="32.1" x2="214.7" y2="130.4"/><line x1="269.6" y1="37.8" x2="351.9" y2="34.8"/><line x1="269.6" y1="37.8" x2="295.5" y2="108.4"/><line x1="351.9" y1="34.8" x2="436.8" y2="21.1"/><line x1="351.9" y1="34.8" x2="398.8" y2="94.4"/><line x1="436.8" y1="21.1" x2="398.8" y2="94.4"/><line x1="436.8" y1="21.1" x2="476.3" y2="104.1"/><line x1="37.9" y1="125.6" x2="106.2" y2="101.4"/><line x1="37.9" y1="125.6" x2="23.9" y2="172.2"/><line x1="106.2" y1="101.4" x2="115.4" y2="200.1"/><line x1="214.7" y1="130.4" x2="295.5" y2="108.4"/><line x1="214.7" y1="130.4" x2="192.4" y2="190.8"/><line x1="295.5" y1="108.4" x2="298.6" y2="182.4"/><line x1="398.8" y1="94.4" x2="476.3" y2="104.1"/><line x1="398.8" y1="94.4" x2="377.4" y2="170.0"/><line x1="398.8" y1="94.4" x2="436.3" y2="175.7"/><line x1="476.3" y1="104.1" x2="436.3" y2="175.7"/><line x1="23.9" y1="172.2" x2="115.4" y2="200.1"/><line x1="23.9" y1="172.2" x2="50.7" y2="259.6"/><line x1="115.4" y1="200.1" x2="192.4" y2="190.8"/><line x1="115.4" y1="200.1" x2="50.7" y2="259.6"/><line x1="115.4" y1="200.1" x2="115.7" y2="265.9"/><line x1="192.4" y1="190.8" x2="206.0" y2="254.5"/><line x1="298.6" y1="182.4" x2="377.4" y2="170.0"/><line x1="298.6" y1="182.4" x2="306.4" y2="270.5"/><line x1="377.4" y1="170.0" x2="436.3" y2="175.7"/><line x1="377.4" y1="170.0" x2="306.4" y2="270.5"/><line x1="50.7" y1="259.6" x2="115.7" y2="265.9"/><line x1="115.7" y1="265.9" x2="206.0" y2="254.5"/><line x1="206.0" y1="254.5" x2="306.4" y2="270.5"/>
</g>
<g fill="#FFFFFF" opacity="0.9">
<circle cx="32.9" cy="23.5" r="2"/><circle cx="132.5" cy="20.4" r="2"/><circle cx="210.1" cy="32.1" r="2.6"/><circle cx="269.6" cy="37.8" r="2.3"/><circle cx="351.9" cy="34.8" r="2.6"/><circle cx="436.8" cy="21.1" r="2.3"/><circle cx="37.9" cy="125.6" r="2.3"/><circle cx="106.2" cy="101.4" r="2.6"/><circle cx="214.7" cy="130.4" r="2.3"/><circle cx="295.5" cy="108.4" r="2.3"/><circle cx="398.8" cy="94.4" r="2.6"/><circle cx="476.3" cy="104.1" r="2"/><circle cx="23.9" cy="172.2" r="2"/><circle cx="115.4" cy="200.1" r="2.6"/><circle cx="192.4" cy="190.8" r="2.3"/><circle cx="298.6" cy="182.4" r="2"/><circle cx="377.4" cy="170.0" r="2.3"/><circle cx="436.3" cy="175.7" r="2"/><circle cx="50.7" cy="259.6" r="2.3"/><circle cx="115.7" cy="265.9" r="2.3"/><circle cx="206.0" cy="254.5" r="2"/><circle cx="306.4" cy="270.5" r="2.6"/>
</g>
</svg>"""
 
 
def load_image_base64(relative_path):
    """Charge un fichier image local (dossier assets/) et le renvoie encode en base64.
    Renvoie None si le fichier n'existe pas encore, pour ne jamais faire planter la page."""
    full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)
    if not os.path.exists(full_path):
        return None
    with open(full_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
 
 
def render_landing_page():
    """Page d'accueil affichee avant connexion, reprenant le gabarit fourni :
    bandeau reseau + message de bienvenue + schema du flux de donnees."""
    st.markdown(_html(f"""
    <div class="hero-banner">
        <div class="hero-network">{NETWORK_SVG}</div>
        <div class="hero-text">
            <h1>Clinical Services - Blood Study</h1>
            <p>Blood test results management application</p>
        </div>
    </div>
    """), unsafe_allow_html=True)
 
    st.markdown(
        '<div class="welcome-title">Welcome to our software for sharing'
        ' the results of a blood test</div>',
        unsafe_allow_html=True,
    )
 
    logo_lab = load_image_base64("assets/logo_central_lab_results.png")
    logo_cro = load_image_base64("assets/logo_clinical_services.png")
    logo_lph = load_image_base64("assets/logo_lph.png")
 
    def img_tag(b64):
        if b64 is None:
            return '<div style="font-size:0.75rem; color:#999;">logo manquant</div>'
        return f'<img src="data:image/png;base64,{b64}">'
 
    st.markdown(_html(f"""
    <div class="flow-box">
      <div style="display:flex; align-items:center; justify-content:center; gap:6px; flex-wrap:nowrap;">
        <div class="flow-card">
          <div class="logo-box">{img_tag(logo_lab)}</div>
          <div class="flow-label-box">Centralised laboratory</div>
        </div>
        <div class="flow-arrow-col">
          <div class="flow-arrow-label">CSV File</div>
          <svg width="100%" height="20" viewBox="0 0 100 20">
            <defs><marker id="fa1" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M2 1L8 5L2 9" fill="none" stroke="#1B2631" stroke-width="1.5"/></marker></defs>
            <line x1="4" y1="10" x2="92" y2="10" stroke="#1B2631" stroke-width="1.5" marker-end="url(#fa1)"/>
          </svg>
        </div>
        <div class="flow-card">
          <div class="logo-box">{img_tag(logo_cro)}</div>
          <div class="flow-label-box">CRO</div>
        </div>
        <div class="flow-arrow-col">
          <div class="flow-arrow-label">VINC extraction</div>
          <svg width="100%" height="20" viewBox="0 0 100 20">
            <defs><marker id="fa2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M2 1L8 5L2 9" fill="none" stroke="#1B2631" stroke-width="1.5"/></marker></defs>
            <line x1="4" y1="10" x2="92" y2="10" stroke="#1B2631" stroke-width="1.5" marker-end="url(#fa2)"/>
          </svg>
        </div>
        <div class="flow-card">
          <div class="logo-box">{img_tag(logo_lph)}</div>
          <div class="flow-label-box">Promoteur</div>
        </div>
      </div>
    </div>
    """), unsafe_allow_html=True)
 
 
def inject_custom_css():
    st.markdown("""
    <style>
    .block-container {
        padding-top: 1.2rem !important;
    }
    body {
        overflow-x: hidden;
    }
    [data-testid="stSidebar"] {
        background: #123C5A;
        border-right: 1px solid #0D2C42;
    }
    [data-testid="stSidebar"] * {
        color: #FFFFFF !important;
    }
    /* Le champ mot de passe a une icone oeil en plus, geree par Streamlit dans le
       meme conteneur que le champ texte : on met la bordure sur CE conteneur
       (et non sur <input> seul) pour que les deux champs fassent exactement
       la meme largeur/hauteur, icone comprise. */
    [data-testid="stSidebar"] [data-baseweb="input"] {
        border: 2px solid #FF6F59 !important;
        border-radius: 6px !important;
        background: #FFFFFF !important;
    }
    [data-testid="stSidebar"] [data-baseweb="input"] input {
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
        color: #1B2631 !important;
        -webkit-text-fill-color: #1B2631 !important;
        font-weight: 400 !important;
        font-style: normal !important;
        text-decoration: none !important;
    }
    [data-testid="stSidebar"] [data-testid="stTextInput"] input {
        color: #1B2631 !important;
        -webkit-text-fill-color: #1B2631 !important;
        font-weight: 400 !important;
        font-style: normal !important;
        text-decoration: none !important;
    }
    [data-testid="stSidebar"] [data-baseweb="input"] button {
        background: transparent !important;
    }
    [data-testid="stSidebar"] label {
        font-weight: 600 !important;
    }
    [data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.25);
    }
    .sidebar-login-title {
        font-size: 1.3rem;
        font-weight: 700;
        margin-bottom: 0.6rem;
    }
    .sidebar-login-hint {
        font-style: italic;
        font-size: 0.82rem;
        opacity: 0.85;
        margin-top: 0.6rem;
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
    [data-testid="stSidebar"] button {
        background: linear-gradient(135deg, #2E86C1 0%, #1B4F72 100%) !important;
        color: #FFFFFF !important;
        border: none !important;
        font-weight: 600 !important;
        box-shadow: 0 2px 5px rgba(0,0,0,0.2);
        transition: all 0.15s ease;
    }
    [data-testid="stSidebar"] button:hover {
        background: linear-gradient(135deg, #3498DB 0%, #21618C 100%) !important;
        transform: translateY(-2px);
        box-shadow: 0 5px 10px rgba(0,0,0,0.3);
    }
    [data-testid="stSidebar"] button:active {
        transform: translateY(0px);
    }
    [data-testid="stSidebar"] button p {
        color: #FFFFFF !important;
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
        background-color: rgba(255,255,255,0.15);
        color: #FFFFFF;
        border: 1px solid rgba(255,255,255,0.4);
        margin-top: 0.4rem;
    }
    .oor-flag { color: #B03A2E; font-weight: 700; }
 
    /* --- Bandeau d'accueil type maquette --- */
    .hero-banner {
        position: relative;
        overflow: hidden;
        background: linear-gradient(135deg, #123C5A 0%, #1B6EA5 100%);
        border-radius: 0 10px 10px 0;
        padding: 2.2rem 3rem 2.2rem 2rem;
        margin-left: -1rem;
        margin-right: -1rem;
        margin-top: -1.2rem;
        margin-bottom: 1.8rem;
        width: calc(100% + 2rem);
        min-height: 130px;
        box-sizing: border-box;
    }
    .hero-banner h1 {
        color: #FFFFFF;
        font-size: 2.1rem;
        font-weight: 800;
        margin: 0 0 0.2rem 0;
    }
    .hero-banner p {
        color: #DCEBF7;
        font-size: 1rem;
        margin: 0;
        position: relative;
        z-index: 2;
    }
    .hero-network {
        position: absolute;
        top: -10px;
        right: -10px;
        width: 52%;
        height: 180%;
        opacity: 0.8;
        pointer-events: none;
        z-index: 1;
    }
    .hero-text { position: relative; z-index: 2; max-width: 60%; }
 
    .welcome-title {
        text-align: center;
        color: #1B4F72;
        font-size: 1.5rem;
        font-weight: 700;
        margin: 1.6rem 0 1.4rem 0;
    }
    .flow-box {
        border: 2px solid #1B2631;
        border-radius: 10px;
        padding: 2rem 1.5rem;
        max-width: 820px;
        margin: 0 auto 2rem auto;
        background: #FFFFFF;
    }
    .flow-card {
        display:flex; flex-direction:column; align-items:center; gap:8px; width:150px;
    }
    .flow-card .logo-box {
        border: 1px solid #E0E0E0;
        border-radius: 10px;
        padding: 10px;
        width: 100%;
        height: 80px;
        display:flex; align-items:center; justify-content:center;
        background:#FAFAFA;
    }
    .flow-card .logo-box img { max-height: 55px; max-width: 100%; object-fit: contain; }
    .flow-card .flow-label-box {
        border: 1.5px solid #B03A2E;
        border-radius: 6px;
        padding: 4px 10px;
        font-size: 0.78rem;
        font-weight: 700;
        color: #1B2631;
        text-align: center;
    }
    .flow-arrow-col {
        display:flex; flex-direction:column; align-items:center; gap:4px; width:100px; flex-shrink:0;
    }
    .flow-arrow-label { font-size: 0.78rem; font-weight: 600; color: #1B2631; text-align:center; }
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
 
def get_user_record(conn, username):
    cur = conn.execute(
        "SELECT role, full_name, password_hash FROM USERS WHERE username = ?", (username,)
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"role": row[0], "full_name": row[1], "password_hash": row[2]}
 
 
def check_password(plain_password, password_hash):
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False
 
 
def sidebar_user_identification(conn):
    st.sidebar.markdown('<div class="sidebar-login-title">🫆Identifiant</div>', unsafe_allow_html=True)
 
    if "auth_username" not in st.session_state:
        st.session_state.auth_username = None
        st.session_state.auth_role = None
        st.session_state.auth_full_name = None
 
    # Deja connecte : on affiche l'etat et un bouton de deconnexion
    if st.session_state.auth_username:
        st.sidebar.success(st.session_state.auth_full_name)
        st.sidebar.markdown(
            f'<span class="role-badge">{ROLE_LABELS.get(st.session_state.auth_role, "")}</span>',
            unsafe_allow_html=True,
        )
        if st.sidebar.button("Se déconnecter"):
            log_audit(conn, "USERS", "LOGOUT", st.session_state.auth_username)
            st.session_state.auth_username = None
            st.session_state.auth_role = None
            st.session_state.auth_full_name = None
            st.rerun()
        return (st.session_state.auth_username, st.session_state.auth_role,
                st.session_state.auth_full_name)
 
    # Pas encore connecte : formulaire identifiant + mot de passe
    with st.sidebar.form("login_form"):
        username_input = st.text_input("User identifiant", placeholder="e.g. labcentral / cro_arc / sponsor_lph")
        password_input = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
 
    if submitted:
        record = get_user_record(conn, username_input)
        if record is None:
            st.sidebar.error("Identifiant ou mot de passe incorrect.")
            log_audit(conn, "USERS", "LOGIN_FAILED", username_input or "(vide)")
            return None, None, None
 
        if not check_password(password_input, record["password_hash"]):
            st.sidebar.error("Identifiant ou mot de passe incorrect.")
            log_audit(conn, "USERS", "LOGIN_FAILED", username_input)
            return None, None, None
 
        st.session_state.auth_username = username_input
        st.session_state.auth_role = record["role"]
        st.session_state.auth_full_name = record["full_name"]
        log_audit(conn, "USERS", "LOGIN_SUCCESS", username_input)
        st.rerun()
 
    st.sidebar.markdown(
        '<div class="sidebar-login-hint">Please enter your username and password '
        'to continue.</div>', unsafe_allow_html=True)
    return None, None, None
 
 
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
        render_landing_page()
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
 