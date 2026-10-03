# BLOOD LIMS V14 — Visual Archive Tree Fix

These are the only code files modified for the visual archive-explorer change.

## Replace
- `archive_service.py` → replace the existing root file.
- `streamlit_app.py` → replace the existing root file.

## No other code file needs to be replaced.

The change keeps the existing archive repository and metadata. It only:
- adds retrieval of archived file content for download;
- renders CRO archive as `Year → ISO Week → Stakeholder → Document Type → File`;
- renders Sponsor archive as `Year → Quarter → Month → Stakeholder → Document Type → File`;
- adds archive search and per-file download cards.

## Checks
```bash
python -m py_compile archive_service.py streamlit_app.py
pytest -q
streamlit run streamlit_app.py
```

No database schema change is required by this visual change.
