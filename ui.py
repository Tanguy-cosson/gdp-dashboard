"""ui.py — CSS, bannière, page d'accueil. Rendu HTML pur, pas d'accès DB
sauf pour lire les SETTINGS (nom de société / logo)."""
import base64
import os

import streamlit as st

from db import get_setting


def _html(text):
    """Retire toute indentation de chaque ligne d'un bloc HTML avant de
    l'envoyer à st.markdown (sinon Markdown transforme les lignes
    indentées de 4+ espaces en bloc de code, même avec
    unsafe_allow_html=True)."""
    return "\n".join(line.strip() for line in text.strip("\n").splitlines())


NETWORK_SVG = """<svg viewBox="0 0 500 300" xmlns="http://www.w3.org/2000/svg">
<g stroke="#FFFFFF" stroke-width="0.9" opacity="0.55">
<line x1="32.9" y1="23.5" x2="132.5" y2="20.4"/><line x1="32.9" y1="23.5" x2="37.9" y2="125.6"/>
<line x1="32.9" y1="23.5" x2="106.2" y2="101.4"/><line x1="132.5" y1="20.4" x2="210.1" y2="32.1"/>
<line x1="132.5" y1="20.4" x2="106.2" y2="101.4"/><line x1="210.1" y1="32.1" x2="269.6" y2="37.8"/>
<line x1="210.1" y1="32.1" x2="214.7" y2="130.4"/><line x1="269.6" y1="37.8" x2="351.9" y2="34.8"/>
<line x1="269.6" y1="37.8" x2="295.5" y2="108.4"/><line x1="351.9" y1="34.8" x2="436.8" y2="21.1"/>
<line x1="351.9" y1="34.8" x2="398.8" y2="94.4"/><line x1="436.8" y1="21.1" x2="398.8" y2="94.4"/>
<line x1="436.8" y1="21.1" x2="476.3" y2="104.1"/><line x1="37.9" y1="125.6" x2="106.2" y2="101.4"/>
<line x1="37.9" y1="125.6" x2="23.9" y2="172.2"/><line x1="106.2" y1="101.4" x2="115.4" y2="200.1"/>
<line x1="214.7" y1="130.4" x2="295.5" y2="108.4"/><line x1="214.7" y1="130.4" x2="192.4" y2="190.8"/>
<line x1="295.5" y1="108.4" x2="298.6" y2="182.4"/><line x1="398.8" y1="94.4" x2="476.3" y2="104.1"/>
<line x1="398.8" y1="94.4" x2="377.4" y2="170.0"/><line x1="398.8" y1="94.4" x2="436.3" y2="175.7"/>
<line x1="476.3" y1="104.1" x2="436.3" y2="175.7"/><line x1="23.9" y1="172.2" x2="115.4" y2="200.1"/>
<line x1="23.9" y1="172.2" x2="50.7" y2="259.6"/><line x1="115.4" y1="200.1" x2="192.4" y2="190.8"/>
<line x1="115.4" y1="200.1" x2="50.7" y2="259.6"/><line x1="115.4" y1="200.1" x2="115.7" y2="265.9"/>
<line x1="192.4" y1="190.8" x2="206.0" y2="254.5"/><line x1="298.6" y1="182.4" x2="377.4" y2="170.0"/>
<line x1="298.6" y1="182.4" x2="306.4" y2="270.5"/><line x1="377.4" y1="170.0" x2="436.3" y2="175.7"/>
<line x1="377.4" y1="170.0" x2="306.4" y2="270.5"/><line x1="50.7" y1="259.6" x2="115.7" y2="265.9"/>
<line x1="115.7" y1="265.9" x2="206.0" y2="254.5"/><line x1="206.0" y1="254.5" x2="306.4" y2="270.5"/>
</g>
<g fill="#FFFFFF" opacity="0.9">
<circle cx="32.9" cy="23.5" r="2"/><circle cx="132.5" cy="20.4" r="2"/><circle cx="210.1" cy="32.1" r="2.6"/>
<circle cx="269.6" cy="37.8" r="2.3"/><circle cx="351.9" cy="34.8" r="2.6"/><circle cx="436.8" cy="21.1" r="2.3"/>
<circle cx="37.9" cy="125.6" r="2.3"/><circle cx="106.2" cy="101.4" r="2.6"/><circle cx="214.7" cy="130.4" r="2.3"/>
<circle cx="295.5" cy="108.4" r="2.3"/><circle cx="398.8" cy="94.4" r="2.6"/><circle cx="476.3" cy="104.1" r="2"/>
<circle cx="23.9" cy="172.2" r="2"/><circle cx="115.4" cy="200.1" r="2.6"/><circle cx="192.4" cy="190.8" r="2.3"/>
<circle cx="298.6" cy="182.4" r="2"/><circle cx="377.4" cy="170.0" r="2.3"/><circle cx="436.3" cy="175.7" r="2"/>
<circle cx="50.7" cy="259.6" r="2.3"/><circle cx="115.7" cy="265.9" r="2.3"/><circle cx="206.0" cy="254.5" r="2"/>
<circle cx="306.4" cy="270.5" r="2.6"/>
</g>
</svg>"""


def load_image_base64(relative_path):
    full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)
    if not os.path.exists(full_path):
        return None
    with open(full_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def inject_custom_css():
    st.markdown("""
    <style>
    .block-container { padding-top: 1.2rem !important; }
    body { overflow-x: hidden; }
    [data-testid="stSidebar"] { background: #123C5A; border-right: 1px solid #0D2C42; }
    [data-testid="stSidebar"] * { color: #FFFFFF !important; }
    [data-testid="stSidebar"] [data-baseweb="input"] {
        border: 2px solid #FF6F59 !important; border-radius: 6px !important; background: #FFFFFF !important;
    }
    [data-testid="stSidebar"] [data-baseweb="input"] input {
        border: none !important; background: transparent !important; box-shadow: none !important;
        color: #1B2631 !important; -webkit-text-fill-color: #1B2631 !important;
        font-weight: 400 !important; font-style: normal !important; text-decoration: none !important;
    }
    [data-testid="stSidebar"] [data-testid="stTextInput"] input {
        color: #1B2631 !important; -webkit-text-fill-color: #1B2631 !important;
        font-weight: 400 !important; font-style: normal !important; text-decoration: none !important;
    }
    [data-testid="stSidebar"] [data-baseweb="input"] button { background: transparent !important; }
    [data-testid="stSidebar"] label { font-weight: 600 !important; }
    [data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.25); }
    .sidebar-login-title { font-size: 1.3rem; font-weight: 700; margin-bottom: 0.6rem; }
    .sidebar-login-hint { font-style: italic; font-size: 0.82rem; opacity: 0.85; margin-top: 0.6rem; }
    .stButton>button, .stDownloadButton>button {
        border-radius: 8px; border: 1.5px solid #2E86C1; color: #2E86C1;
        background-color: #FFFFFF; font-weight: 600; transition: all 0.15s ease;
    }
    .stButton>button:hover, .stDownloadButton>button:hover { background-color: #2E86C1; color: #FFFFFF; }
    [data-testid="stSidebar"] button {
        background: linear-gradient(135deg, #2E86C1 0%, #1B4F72 100%) !important;
        color: #FFFFFF !important; border: none !important; font-weight: 600 !important;
        box-shadow: 0 2px 5px rgba(0,0,0,0.2); transition: all 0.15s ease;
    }
    [data-testid="stSidebar"] button:hover {
        background: linear-gradient(135deg, #3498DB 0%, #21618C 100%) !important;
        transform: translateY(-2px); box-shadow: 0 5px 10px rgba(0,0,0,0.3);
    }
    [data-testid="stSidebar"] button:active { transform: translateY(0px); }
    [data-testid="stSidebar"] button p { color: #FFFFFF !important; }
    [data-testid="stMetricValue"] { color: #1B4F72; font-weight: 700; }
    h1, h2, h3 { letter-spacing: -0.3px; }
    .notif-chip {
        position: absolute; top: 1.3rem; right: 1.6rem; z-index: 3;
        background-color: rgba(255,255,255,0.18); border: 1px solid rgba(255,255,255,0.45);
        border-radius: 999px; padding: 0.35rem 0.9rem; font-size: 0.85rem; font-weight: 600; color: white;
    }
    .role-badge {
        display: inline-block; padding: 0.25rem 0.75rem; border-radius: 999px;
        font-size: 0.8rem; font-weight: 600; background-color: rgba(255,255,255,0.15);
        color: #FFFFFF; border: 1px solid rgba(255,255,255,0.4); margin-top: 0.4rem;
    }
    .oor-flag { color: #B03A2E; font-weight: 700; }
    .critical-banner {
        background: #FDEDEC; border: 2px solid #B03A2E; border-radius: 10px;
        padding: 0.9rem 1.2rem; margin-bottom: 1.2rem; color: #7B241C; font-weight: 700;
    }
    .hero-banner {
        position: relative; overflow: hidden;
        background: linear-gradient(135deg, #123C5A 0%, #1B6EA5 100%);
        border-radius: 0 10px 10px 0; padding: 2.2rem 3rem 2.2rem 2rem;
        margin-left: -1rem; margin-right: -1rem; margin-top: -1.2rem; margin-bottom: 1.8rem;
        width: calc(100% + 2rem); min-height: 130px; box-sizing: border-box;
    }
    .hero-banner h1 { color: #FFFFFF; font-size: 2.1rem; font-weight: 800; margin: 0 0 0.2rem 0; }
    .hero-banner p { color: #DCEBF7; font-size: 1rem; margin: 0; position: relative; z-index: 2; }
    .hero-network {
        position: absolute; top: -10px; right: -10px; width: 52%; height: 180%;
        opacity: 0.8; pointer-events: none; z-index: 1;
    }
    .hero-text { position: relative; z-index: 2; max-width: 60%; }
    .welcome-title { text-align: center; color: #1B4F72; font-size: 1.5rem; font-weight: 700; margin: 1.6rem 0 1.4rem 0; }
    .landing-description {
        max-width: 720px; margin: 0 auto 2rem auto; text-align: center; color: #34495E;
        font-size: 0.95rem; line-height: 1.6;
    }
    .flow-box {
        border: 2px solid #1B2631; border-radius: 10px; padding: 2rem 1.5rem;
        max-width: 820px; margin: 0 auto 2rem auto; background: #FFFFFF;
    }
    .flow-card { display:flex; flex-direction:column; align-items:center; gap:8px; width:150px; }
    .flow-card .logo-box {
        border: 1px solid #E0E0E0; border-radius: 10px; padding: 10px; width: 100%; height: 80px;
        display:flex; align-items:center; justify-content:center; background:#FAFAFA;
    }
    .flow-card .logo-box img { max-height: 55px; max-width: 100%; object-fit: contain; }
    .flow-card .flow-label-box {
        border: 1.5px solid #B03A2E; border-radius: 6px; padding: 4px 10px;
        font-size: 0.78rem; font-weight: 700; color: #1B2631; text-align: center;
    }
    .flow-arrow-col { display:flex; flex-direction:column; align-items:center; gap:4px; width:100px; flex-shrink:0; }
    .flow-arrow-label { font-size: 0.78rem; font-weight: 600; color: #1B2631; text-align:center; }
    .kpi-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 1rem; margin-bottom: 1.2rem; }
    .kpi-card {
        background: #FFFFFF; border: 1px solid #E3E8EE; border-radius: 12px; padding: 1.1rem 1.2rem;
        border-left: 5px solid var(--accent, #2E86C1); box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .kpi-card .kpi-label {
        font-size: 0.78rem; font-weight: 600; color: #7B8794; text-transform: uppercase;
        letter-spacing: 0.4px; margin-bottom: 0.3rem;
    }
    .kpi-card .kpi-value { font-size: 1.9rem; font-weight: 800; color: #1B2631; line-height: 1.1; }
    .kpi-card .kpi-icon { font-size: 1.3rem; float: right; opacity: 0.7; }
    .lims-panel {
        background: #FFFFFF; border: 1px solid #E3E8EE; border-radius: 12px; padding: 1.3rem 1.4rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06); margin-bottom: 1.2rem; height: 100%;
    }
    .lims-panel h4 { margin: 0 0 1rem 0; color: #1B4F72; font-size: 1.02rem; font-weight: 700; }
    .funnel-row { display:flex; gap:0.8rem; margin-bottom:0.5rem; }
    .funnel-stage { flex:1; text-align:center; padding:0.7rem; border-radius:8px; background:#F5F7FA; border:1px solid #E3E8EE; }
    .funnel-stage .funnel-count { font-size:1.4rem; font-weight:800; color:#1B4F72; }
    .funnel-stage .funnel-label { font-size:0.72rem; color:#7B8794; text-transform:uppercase; }
    .donut-wrap { display: flex; align-items: center; justify-content: center; gap: 1.5rem; }
    .donut {
        width: 128px; height: 128px; border-radius: 50%;
        background: conic-gradient(#2E86C1 var(--pct, 0%), #E9EEF3 0);
        display: flex; align-items: center; justify-content: center; flex-shrink: 0;
    }
    .donut-hole {
        width: 92px; height: 92px; border-radius: 50%; background: #FFFFFF;
        display: flex; flex-direction: column; align-items: center; justify-content: center;
    }
    .donut-hole .donut-pct { font-size: 1.35rem; font-weight: 800; color: #1B4F72; }
    .donut-hole .donut-caption { font-size: 0.68rem; color: #7B8794; }
    .donut-legend { font-size: 0.85rem; color: #34495E; }
    .donut-legend .legend-item { display:flex; align-items:center; gap:0.5rem; margin-bottom:0.4rem; }
    .legend-dot { width:10px; height:10px; border-radius:50%; display:inline-block; }
    .activity-list { list-style: none; margin: 0; padding: 0; }
    .activity-item {
        display: flex; justify-content: space-between; align-items: baseline;
        padding: 0.55rem 0; border-bottom: 1px solid #F0F2F5; font-size: 0.85rem;
    }
    .activity-item:last-child { border-bottom: none; }
    .activity-action { color: #1B2631; font-weight: 600; }
    .activity-meta { color: #9AA5B1; font-size: 0.78rem; }
    .search-result-card {
        border: 1px solid #E3E8EE; border-left: 4px solid #2E86C1; border-radius: 8px;
        padding: 0.9rem 1.1rem; margin-bottom: 0.8rem; background: #FFFFFF;
    }
    .scan-box {
        border: 2px dashed #2E86C1; border-radius: 10px; padding: 1.4rem; text-align: center;
        background: #F5FAFF; margin-bottom: 1.2rem;
    }
    </style>
    """, unsafe_allow_html=True)


def render_top_banner(conn, subtitle, notif_count=None):
    company_name = get_setting(conn, "company_name") or "Clinical Services"
    notif_html = ""
    if notif_count is not None:
        notif_html = f'<div class="notif-chip">🔔 {notif_count} pending</div>'
    st.markdown(_html(f"""
    <div class="hero-banner">
        <div class="hero-network">{NETWORK_SVG}</div>
        {notif_html}
        <div class="hero-text">
            <h1>{company_name} - Blood Study</h1>
            <p>{subtitle}</p>
        </div>
    </div>
    """), unsafe_allow_html=True)


def render_landing_page(conn):
    render_top_banner(conn, "Blood test results management application")
    st.markdown(
        '<div class="welcome-title">Welcome to our software for sharing'
        ' the results of a blood test</div>',
        unsafe_allow_html=True,
    )
    st.markdown(_html("""
    <div class="landing-description">
        This software supports <strong>Clinical Services (CRO)</strong> in the
        operational management of blood test results for <strong>sponsor-led
        diabetes clinical trials</strong>. It tracks each sample from receipt
        through a two-level validation workflow (laboratory technician, then
        biologist), centralises data sent weekly by the Central Laboratory,
        and provides investigators, the CRO and the sponsor with fully
        traceable, role-based access — with every action permanently recorded
        in an immutable audit trail. Samples are tracked by barcode from
        reception to storage.
    </div>
    """), unsafe_allow_html=True)

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
                    <defs><marker id="fa1" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6"
                        markerHeight="6" orient="auto-start-reverse">
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
                    <defs><marker id="fa2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6"
                        markerHeight="6" orient="auto-start-reverse">
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


def kpi_card(label, value, icon, accent):
    return _html(f"""
    <div class="kpi-card" style="--accent:{accent};">
        <span class="kpi-icon">{icon}</span>
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{value}</div>
    </div>
    """)
