# BLOOD LIMS V14 — Unified Archive + Data Correction/Void fix

This patch keeps the dedicated Archive page and restores Data Correction / Void in the same codebase.

Replace `streamlit_app.py`, `constants.py`, `i18n.py`, and `ui.py` together. Do not modify `db.py` and do not reset the database.

Then run: `python -m py_compile streamlit_app.py`
