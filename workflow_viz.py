"""
workflow_viz.py — Représentations visuelles du workflow, en s'inspirant
des diagrammes de pipeline de laboratoire (Start -> étapes -> End,
avec embranchement de décision) : un schéma d'ensemble avec les
compteurs en direct, et une frise par patient/échantillon montrant où
il en est précisément.

Tout est généré en SVG pur (pas de dépendance graphviz/matplotlib
supplémentaire à installer) pour rester cohérent avec le reste de
l'application (voir NETWORK_SVG dans ui.py) et gratuit à déployer.
"""
from business_logic import compute_oor_flag
from constants import CRITICAL_FLAG
from db import count_by_status, read_full_results

# Palette reprise de ui.py pour rester visuellement cohérent
_COLOR_START = ("#EAFAF1", "#27AE60")
_COLOR_BOX = ("#D6EAF8", "#2E86C1")
_COLOR_ALERT = ("#FDEBD0", "#B9770E")
_COLOR_REPORT = ("#E8DAEF", "#8E44AD")
_COLOR_END = ("#FDEDEC", "#B03A2E")
_COLOR_DECISION = ("#F4F6F7", "#7B8794")


def _box(x, y, w, h, label, count_label, fill, stroke):
    return f"""
    <g>
      <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>
      <text x="{x + w/2}" y="{y + h/2 - 6}" text-anchor="middle" font-size="12" font-weight="700" fill="#1B2631">{label}</text>
      <text x="{x + w/2}" y="{y + h/2 + 12}" text-anchor="middle" font-size="11" fill="{stroke}">{count_label}</text>
    </g>
    """


def _ellipse(cx, cy, rx, ry, label, fill, stroke):
    return f"""
    <g>
      <ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>
      <text x="{cx}" y="{cy + 4}" text-anchor="middle" font-size="12" font-weight="700" fill="{stroke}">{label}</text>
    </g>
    """


def _diamond(cx, cy, w, h, label):
    fill, stroke = _COLOR_DECISION
    points = f"{cx},{cy - h/2} {cx + w/2},{cy} {cx},{cy + h/2} {cx - w/2},{cy}"
    return f"""
    <g>
      <polygon points="{points}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>
      <text x="{cx}" y="{cy + 4}" text-anchor="middle" font-size="10.5" fill="#1B2631">{label}</text>
    </g>
    """


def _arrow(x1, y1, x2, y2, marker="arrow", label=None, dash=False):
    dash_attr = 'stroke-dasharray="4,3"' if dash else ""
    label_svg = ""
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        label_svg = f'<rect x="{mx-24}" y="{my-9}" width="48" height="16" fill="#FFFFFF"/>' \
                    f'<text x="{mx}" y="{my+3}" text-anchor="middle" font-size="9.5" fill="#7B8794">{label}</text>'
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#7B8794" stroke-width="1.4" {dash_attr} marker-end="url(#{marker})"/>{label_svg}'


def render_pipeline_svg(conn) -> str:
    """Schéma d'ensemble du pipeline avec les compteurs EN DIRECT
    (nombre réel de résultats à chaque étape dans la base actuelle)."""
    df = read_full_results(conn)
    n_total = len(df)
    n_pending = count_by_status(conn, "PENDING")
    n_tech_ok = count_by_status(conn, "TECHNICAL_OK")
    n_reviewed = count_by_status(conn, "REVIEWED")

    n_critical = 0
    if not df.empty:
        flagged = compute_oor_flag(df)
        n_critical = int(((flagged["Alerte"] == CRITICAL_FLAG) & (flagged["status"] != "REVIEWED")).sum())

    W, H = 720, 760
    svg = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" font-family="Helvetica, Arial, sans-serif">']
    svg.append("""<defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0L10 5L0 10z" fill="#7B8794"/>
        </marker>
    </defs>""")

    cx = W / 2
    box_w, box_h = 300, 50

    # Start
    svg.append(_ellipse(cx, 30, 55, 24, "Start", *_COLOR_START))
    svg.append(_arrow(cx, 54, cx, 84))

    # Ingestion
    svg.append(_box(cx - box_w/2, 84, box_w, box_h, "Ingestion (CSV / HL7)", f"{n_total} résultat(s) au total", *_COLOR_BOX))
    svg.append(_arrow(cx, 134, cx, 164))

    # Sample creation
    svg.append(_box(cx - box_w/2, 164, box_w, box_h, "Échantillon créé + code-barres", "chaîne de conservation", *_COLOR_BOX))
    svg.append(_arrow(cx, 214, cx, 244))

    # Technical validation
    svg.append(_box(cx - box_w/2, 244, box_w, box_h, "Validation technique", f"{n_pending} en attente", *_COLOR_BOX))
    svg.append(_arrow(cx, 294, cx, 324))

    # Biological validation
    svg.append(_box(cx - box_w/2, 324, box_w, box_h, "Validation biologique (signature)", f"{n_tech_ok} en attente", *_COLOR_BOX))
    svg.append(_arrow(cx, 374, cx, 404))

    # Decision diamond: critical value?
    svg.append(_diamond(cx, 430, 220, 60, "Valeur critique détectée ?"))
    # Yes branch -> alert box (side)
    alert_x, alert_y = cx + 170, 405
    svg.append(_arrow(cx + 60, 425, alert_x, alert_y + 25, label="Oui"))
    svg.append(_box(alert_x, alert_y, 220, 50, "🔴 Alerte immédiate", f"{n_critical} en cours", *_COLOR_ALERT))
    svg.append(_arrow(alert_x + 110, alert_y + 50, cx + 20, 480, dash=True))
    # No branch -> straight down
    svg.append(_arrow(cx, 460, cx, 490, label="Non"))

    # Reviewed / reporting stage
    svg.append(_box(cx - box_w/2, 490, box_w, box_h, "Résultats validés", f"{n_reviewed} disponibles", *_COLOR_REPORT))
    svg.append(_arrow(cx, 540, cx, 570))

    # Branch to 3 outputs
    out_y = 570
    out_w = 200
    positions = [cx - 250, cx - 10, cx + 230]
    labels = [("Dossier patient", "rapport PDF"), ("Export VINC", "CRO / Sponsor"), ("Export SDTM", "LB + DM + define.xml")]
    for px, (lab, sub) in zip(positions, labels):
        svg.append(_arrow(cx, 570, px + out_w/2 - 40, out_y, dash=False))
        svg.append(_box(px, out_y, out_w, 50, lab, sub, *_COLOR_BOX))
        svg.append(_arrow(px + out_w/2 - 40, out_y + 50, cx, 660))

    # End
    svg.append(_ellipse(cx, 690, 55, 24, "End", *_COLOR_END))

    svg.append("</svg>")
    return "".join(svg)


_STEPPER_STAGES = [
    ("PENDING", "Ingéré"),
    ("TECHNICAL_OK", "Validé technique"),
    ("REVIEWED", "Validé biologique"),
]


def render_status_stepper(status: str) -> str:
    """Frise horizontale (HTML/CSS) montrant à quel stade en est UN
    résultat/visite précis, avec les étapes déjà franchies en surbrillance.
    Utilisée sur Patient Records pour visualiser où en est chaque visite
    sans avoir à lire une colonne de statut texte."""
    order = ["PENDING", "TECHNICAL_OK", "REVIEWED"]
    current_index = order.index(status) if status in order else -1

    items = []
    for i, (code, label) in enumerate(_STEPPER_STAGES):
        reached = i <= current_index
        color = "#2E86C1" if reached else "#D5DBDB"
        text_color = "#1B2631" if reached else "#95A5A6"
        items.append(
            f'<div style="display:flex;flex-direction:column;align-items:center;flex:1;">'
            f'<div style="width:22px;height:22px;border-radius:50%;background:{color};'
            f'display:flex;align-items:center;justify-content:center;color:white;font-size:11px;font-weight:700;">'
            f'{"✓" if reached else i+1}</div>'
            f'<div style="font-size:0.72rem;color:{text_color};margin-top:4px;text-align:center;">{label}</div>'
            f'</div>'
        )
        if i < len(_STEPPER_STAGES) - 1:
            line_color = "#2E86C1" if i < current_index else "#D5DBDB"
            items.append(f'<div style="flex:0.6;height:2px;background:{line_color};margin-top:11px;"></div>')

    return f'<div style="display:flex;align-items:flex-start;width:100%;margin:0.4rem 0 0.8rem 0;">{"".join(items)}</div>'


def render_storage_map_html(storage_df) -> str:
    """Carte de stockage : une carte par congélateur, listant ses racks
    avec le nombre d'échantillons par boîte. Reçoit directement le
    DataFrame de db.get_all_current_storage_locations (pas d'accès DB
    ici, pour rester testable sans base)."""
    if storage_df.empty:
        return '<div class="lims-panel"><p style="color:#7B8794;">Aucun échantillon rangé pour le moment.</p></div>'

    cards = []
    for freezer_id, freezer_group in storage_df.groupby("freezer_id"):
        rack_blocks = []
        for rack_id, rack_group in freezer_group.groupby("rack_id"):
            box_chips = []
            for box_id, box_group in rack_group.groupby("box_id"):
                n = len(box_group)
                # Couleur indicative de densité (purement visuelle, pas de seuil métier réel)
                color = "#B03A2E" if n >= 8 else ("#B9770E" if n >= 4 else "#2E86C1")
                box_chips.append(
                    f'<span style="display:inline-block;margin:2px;padding:4px 10px;'
                    f'border-radius:6px;background:{color}1A;color:{color};'
                    f'font-size:0.78rem;font-weight:600;border:1px solid {color}55;">'
                    f'📦 {box_id} · {n}</span>'
                )
            rack_blocks.append(
                f'<div style="margin-bottom:0.5rem;"><strong style="font-size:0.85rem;">Rack {rack_id}</strong><br/>'
                f'{"".join(box_chips)}</div>'
            )
        total = len(freezer_group)
        cards.append(f"""
        <div class="lims-panel" style="margin-bottom:1rem;">
            <h4>🧊 {freezer_id} <span style="font-weight:400;color:#7B8794;font-size:0.8rem;">— {total} échantillon(s)</span></h4>
            {"".join(rack_blocks)}
        </div>
        """)
    return "".join(cards)
