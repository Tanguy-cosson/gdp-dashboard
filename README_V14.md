# BLOOD LIMS V14 — Data Correction / Void fix

Problem fixed:
`NameError: name 'page_data_correction' is not defined`

Changes in `streamlit_app.py` only:
- adds the missing `page_data_correction()` page implementation;
- imports `read_result_history` and `void_result` from `db`;
- imports `sqlite3` for the existing error handling.

No database schema change and no change to `db.py`.

Installation:
Replace the repository `streamlit_app.py` with the patched file, commit/push to GitHub, then let Streamlit Cloud redeploy.
