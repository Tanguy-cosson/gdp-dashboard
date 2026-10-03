# BLOOD Study LIMS V14

Final educational prototype release for the BLOOD clinical-trial laboratory data workflow.

## Scope
- Central Laboratory -> Clinical Services CRO weekly CSV / file-based HL7 intake
- Technical validation -> biological review -> electronic signature
- Critical-result alerting
- Monthly VINC sponsor package with explicit cut-off
- Automatic archive taxonomy for CRO and sponsor views
- English / Français user interface
- Dashboard KPIs, charts, live local/UTC clock, alerts and archive retrieval
- Controlled multi-month demo history for rehearsal
- SDTM LB/DM educational export and metadata starter

## Important boundary
This is a functional educational prototype. It is not a qualified production GxP system. Production would require qualified infrastructure, persistent data storage, validated interfaces, formal validation/qualification, security assessment, backup/restore/PRA, controlled retention and operational procedures.

## Quick demo start
1. Deploy `streamlit_app.py` from the repository root.
2. In a fresh training database, log in as `cro_arc / cro2026`.
3. Open Settings -> Demonstration history and run `Load multi-month demonstration history`.
4. Open Dashboard to see the historical timeline, KPIs, alerts and archive.
5. Use `demo_data_v14` for controlled weekly imports and HL7 exercises.

## Demo users
- `lab_tech1 / labtech2026`
- `biologist1 / bio2026`
- `physician1 / doc2026`
- `cro_arc / cro2026`
- `sponsor_lph / sponsor2026`

## Archive examples
- CRO inbound: `2026/W11/Central_Lab_Results/CSV/...`
- CRO automation evidence: `2026/W11/Clinical_Services/AUTOMATION_MESSAGE/...`
- Sponsor package: `2026/Q1/2026-03/Clinical_Services/VINC_PACKAGE/...`
- Sponsor receipt: `2026/Q1/2026-03/LPH_Sponsor/SPONSOR_RECEIPT/...`

Folders are logical archive paths rendered by the application / archive bundles. The prototype stores archive artefacts in SQLite; production should use approved persistent document/object storage and retention controls.

## Data
- `demo_data/` = existing quick nominal/negative examples
- `demo_data_v14/history/` = 12 chronological CSV batches from Jan-Jun 2026
- `demo_data_v14/hl7/` = 3 HL7 ORU^R01 scenarios

## Testing
Run `pytest -q` in a development environment with the dependencies from `requirements.txt` / `requirements-dev.txt`.
