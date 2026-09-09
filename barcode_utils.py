"""
barcode_utils.py — Émission et lecture de codes-barres pour les
échantillons.

ÉMISSION : Code128 (python-barcode), imprimable sur étiquette tube.
LECTURE  : deux méthodes complémentaires, à utiliser selon le matériel
disponible sur site :
  1. Scanner USB / douchette laser (le plus courant en labo) : il se
     comporte comme un clavier ("keyboard wedge") — il suffit d'un
     champ st.text_input avec le focus dessus, aucune librairie requise.
     Voir la page "Sample Scan" dans app.py.
  2. Caméra (téléphone / webcam) : capture une photo avec
     st.camera_input, puis décodage via pyzbar (nécessite la librairie
     système libzbar0 — voir packages.txt pour le déploiement
     Streamlit Cloud).
"""
import io

from fpdf import FPDF

try:
    import barcode
    from barcode.writer import ImageWriter
    _BARCODE_AVAILABLE = True
except ImportError:
    _BARCODE_AVAILABLE = False

try:
    from pyzbar.pyzbar import decode as _zbar_decode
    from PIL import Image
    _ZBAR_AVAILABLE = True
except ImportError:
    _ZBAR_AVAILABLE = False


def generate_barcode_png(data: str) -> bytes:
    """Génère un code-barres Code128 (PNG) à partir d'une chaîne
    alphanumérique (typiquement le sample_id, ex: 'S-A1B2C3D4E5')."""
    if not _BARCODE_AVAILABLE:
        raise RuntimeError(
            "La librairie 'python-barcode' n'est pas installée. "
            "Ajoutez 'python-barcode[images]' à requirements.txt."
        )
    code128 = barcode.get_barcode_class("code128")
    bc = code128(data, writer=ImageWriter())
    buf = io.BytesIO()
    bc.write(buf, options={
        "write_text": True,
        "module_height": 9.0,
        "font_size": 7,
        "text_distance": 2,
        "quiet_zone": 2,
    })
    return buf.getvalue()


def decode_barcode_from_image(image_bytes: bytes):
    """Décode un code-barres à partir d'une photo (bytes JPEG/PNG,
    typiquement issue de st.camera_input). Renvoie la chaîne décodée,
    ou None si rien n'a pu être lu. Ne lève jamais d'exception vers
    l'appelant : une lecture ratée doit juste inviter à réessayer /
    saisir manuellement, pas planter la page."""
    if not _ZBAR_AVAILABLE:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        results = _zbar_decode(img)
        if not results:
            return None
        return results[0].data.decode("utf-8")
    except Exception:
        return None


def zbar_available() -> bool:
    return _ZBAR_AVAILABLE


def generate_sample_label_pdf(samples: list) -> bytes:
    """Génère une planche d'étiquettes (une étiquette par échantillon),
    format 50x25mm — taille standard d'étiquette tube — avec code-barres,
    ID patient, visite, type d'échantillon.

    `samples` : liste de dicts avec les clés
        sample_id, barcode_value, usubjid, visit_code, sample_type, site_name
    """
    if not _BARCODE_AVAILABLE:
        raise RuntimeError("python-barcode requis pour générer les étiquettes.")

    pdf = FPDF(orientation="L", unit="mm", format=(50, 25))
    pdf.set_auto_page_break(auto=False)

    for s in samples:
        pdf.add_page()
        png_bytes = generate_barcode_png(s["barcode_value"] or s["sample_id"])
        img_buf = io.BytesIO(png_bytes)

        pdf.set_font("Helvetica", "B", 7)
        pdf.set_xy(2, 1)
        pdf.cell(46, 3, str(s.get("usubjid", ""))[:24], ln=0)
        pdf.set_font("Helvetica", "", 6)
        pdf.set_xy(2, 4)
        pdf.cell(46, 3,
                 f"{s.get('visit_code', '')} - {s.get('sample_type', '')}", ln=0)

        pdf.image(img_buf, x=2, y=7.5, w=46, h=13)

    return bytes(pdf.output(dest="S"))
