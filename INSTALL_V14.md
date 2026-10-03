# BLOOD LIMS V14 — Installation / GitHub Overlay

## GitHub rule
Use this release as an overlay on the existing repository.

- **REPLACE** files included here when the same path already exists.
- **ADD** new files/directories included here that do not exist.
- **DO NOT DELETE** unrelated repository files only because they are absent from this release.
- Do not commit `blood_study.db`, secrets or temporary Python caches.

## Main application files to replace
`streamlit_app.py`, `db.py`, `archive_service.py`, `automation.py`, `audit.py`, `auth.py`, `business_logic.py`, `i18n.py`, `ui.py`, `demo_history.py`, `seed_demo_history.py`.

## Files to add / update as a release pack
- `assets/logo_clinical_services.png`
- `demo_data_v14/`
- `docs_v14/`
- `assets_v14/`
- `BLOOD_LIMS_V14_Testing_KPI_Metadata.xlsx`
- `.streamlit/secrets.toml.example`
- V14 README / release notes / manifest files

## Deployment
1. Copy the release contents into the repository root.
2. Commit the complete V14 change set.
3. Push to the target branch.
4. Confirm the Streamlit entry point is `streamlit_app.py`.
5. Wait for redeployment and open the application.
6. If a new deployment is required, use Streamlit Cloud Manage app -> Reboot / redeploy rather than deleting application data manually.

## Local development
```bash
pip install -r requirements.txt
pytest -q
rm -f blood_study.db        # training reset only
streamlit run streamlit_app.py
```

## Demo reset
For a clean local rehearsal, remove only the local training database and restart. The application will recreate the schema.

For Streamlit Community Cloud, do not rely on local SQLite as production persistence. This release is a training prototype.
