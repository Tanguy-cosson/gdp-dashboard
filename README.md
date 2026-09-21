BLOOD Study LIMS
Educational GxP-oriented LIMS prototype for the BLOOD clinical study
This Streamlit application demonstrates a controlled data flow for a centralized laboratory sending blood results weekly to Clinical Services (CRO), with a monthly VINC package sent by the CRO to LPH (Sponsor).
The study context is intentionally generic: adults with type 2 diabetes, with laboratory assessments at VINC, V1 and V2. No treatment arm, investigational product or randomization logic is required for the demonstration.
> \*\*Important:\*\* this is a training/demo prototype. It is not a validated production GxP system and must not be used with real patient data.
What is implemented
Area	Final status
CSV / HL7 v2 ORU^R01 ingestion	✅
Pre-validation before write	✅
Atomic CSV batch import	✅
Source-file SHA-256 / import ledger	✅
Patient / visit / sample traceability	✅
Technical validation	✅
Biological validation	✅
Re-authenticated electronic signature	✅
Critical / out-of-range detection	✅
Controlled correction / void with supersession	✅
Field-level audit metadata (old/new/reason)	✅
Append-only audit trail	✅
Internal mailbox with real in-app delivery	✅
Weekly ingestion reminder	✅
Monthly reviewed-VINC package	✅
Package manifest + SHA-256	✅
Automatic execution on app load / wake-up	✅
Manual deterministic automation runner	✅
SDTM LB / DM starter export	✅
Define-XML starter	✅
Demo mode with deterministic reference date	✅
English user interface	✅
Automatic tests + GitHub CI	✅
Free-tier architecture
```text
Central Lab
   |
   | weekly CSV / HL7
   v
Streamlit Community Cloud
   |
   +--> pre-validation / atomic import
   +--> technical validation
   +--> biological validation + e-signature
   +--> audit trail / corrections / alerts
   +--> internal mailbox
   |
   +--> monthly VINC package (ACTIVE + REVIEWED + <= cut-off)
   |
   v
LPH Sponsor mailbox
```
The application deliberately uses an internal mailbox as the default delivery channel. This makes automation demonstrable without a paid SMTP service. Real SMTP can be enabled later through Streamlit Secrets, but it is not required for the demonstration.
Final automation model
The free Streamlit Community Cloud tier is not used as a permanent cron server. The final prototype therefore uses a controlled three-part model:
Automatic check on app load: the automation engine evaluates the configured weekly, monthly and critical-result rules when the application starts/wakes. This is the closest model available inside the free prototype without introducing a paid or external scheduler.
Manual deterministic execution: the CRO Automation page provides `Run automation now` and `Run controlled demo (force monthly send)`. The force option bypasses only the calendar/duplicate guard for the monthly job; it never bypasses VINC eligibility or validation status rules.
GitHub Actions for CI only: the repository keeps a GitHub Actions workflow to run automated tests on pushes/pull requests. It is deliberately not used as a hidden production scheduler for the Streamlit app.
This makes the automation behavior reproducible during a classroom demonstration while being honest about the free-tier limitation: a sleeping Streamlit application cannot be relied on as a permanently running regulated scheduler. For a real clinical production deployment, the automation runner would be hosted on a qualified persistent service with an appropriate database, monitoring and validated scheduling mechanism.
Demo automation configuration
For a deterministic classroom demonstration:
`Automation enabled`: ON
`Demo clock`: ON
`Demo reference date`: 2026-06-25
`Demo last ingestion date`: 2026-06-18
Sponsor recipient: sponsor_lph
Laboratory recipient: lab_tech1
Critical recipient: biologist1
With these settings:
the weekly reminder is due because the reference date is seven days after the last ingestion date;
the monthly VINC package is due because the reference date is day 25;
the monthly package contains only `VINC` results that are `ACTIVE` and `REVIEWED` on or before the cut-off;
unresolved critical results generate an internal alert.
Recommended demo sequence
1. Import the supplied laboratory file
Use the supplied `guide\_v2\_import.csv` from the laboratory central.
Expected dataset: 45 results / 5 patients / VINC + V1 + V2 / HbA1c + fasting glucose + creatinine.
The import page performs pre-validation before writing. The source file is hashed and registered in `IMPORT\_BATCHES`.
2. Technical validation
Login as:
```text
Username: lab\_tech1
Password: labtech2026
```
Go to Technical Validation.
Use the scope selector and Select all displayed results, then validate all results technically.
3. Biological validation: VINC only
Login as:
```text
Username: biologist1
Password: bio2026
```
Go to Biological Validation.
Choose VINC only → Select all displayed results.
Use:
```text
Meaning: Reviewed and approved
Remarks: VINC laboratory results reviewed against the study procedure. No additional comment.
```
Re-enter:
```text
bio2026
```
Then click Sign and finalize validation.
This makes the monthly VINC package eligible while leaving V1/V2 results available to demonstrate unresolved critical alerts.
4. Automation preview
Login as:
```text
Username: cro\_arc
Password: cro2026
```
Open Automation.
Click Live automation preview.
You should see three jobs:
Weekly central laboratory file → would trigger
Monthly VINC package → would trigger if eligible VINC exist
Critical result alert → would trigger while unresolved critical results remain
5. Send the monthly package in the demo
Click:
Run controlled demo (force monthly send)
The application will:
select eligible VINC records;
generate a ZIP package;
create the CSV;
create `manifest.json`;
calculate the CSV SHA-256;
calculate the package SHA-256;
register the package in `EXPORT\_PACKAGES`;
deliver the package into the sponsor internal mailbox;
mark the package as `SENT`;
write automation and export events to the Audit Trail.
6. Show that the sponsor really received it
Login as:
```text
Username: sponsor\_lph
Password: sponsor2026
```
Open Mailbox.
Open the monthly BLOOD Study VINC message and download its ZIP attachment.
Open the ZIP and show:
```text
BLOOD\_VINC\_YYYY-MM-DD.csv
manifest.json
```
The manifest contains the cut-off, record count, eligibility rule and hashes.
7. Prove the package was not silently resent
Return to Automation as the CRO and click normal Run automation now.
Because the current month is already marked as sent, the monthly VINC job should not send a second package.
8. Show the critical alert at the same time
The supplied dataset contains at least two intentionally critical examples, including:
`P-FULL-004 / V1 / GLUC = 2.0 mmol/L` with critical-low limit `2.2`;
`P-FULL-004 / V2 / HBA1C = 13.8 %` with critical-high limit `12.0`.
Because V1/V2 were not biologically signed in the recommended sequence, the automation engine can send the critical alert to `biologist1` while the monthly VINC package goes to `sponsor\_lph`.
9. Final evidence
As CRO, show Audit Trail and filter on:
```text
AUTOMATION\_RUN\_COMPLETED
EXPORT\_PACKAGE\_SENT
CRITICAL\_ALERT\_SENT
```
The demonstration then closes the full business loop:
```text
Central Lab
→ ingestion
→ technical validation
→ biological validation
→ critical alert
→ monthly VINC package
→ sponsor mailbox
→ audit evidence
```
Controlled data correction demo
As CRO open Data Correction / Void.
Select a non-critical reviewed result and choose Correct result.
Enter a new value and a mandatory reason, for example:
```text
Reason: Source laboratory correction received; superseding result required.
```
The application does not overwrite the old value. It creates a new result version and changes the previous record to `SUPERSEDED`.
Then show the Audit Trail with:
```text
old value
new value
reason
user
timestamp
object/result identifier
```
This demonstrates controlled correction and traceability.
HL7 demonstration
Use the supplied `guide\_test\_import.hl7`.
The message is an `ORU^R01` with HbA1c, fasting glucose and creatinine. The import demonstrates source lineage and parser validation.
This implementation is file-based: it is not an MLLP network listener.
Tests
Run locally:
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
pytest -q
```
The CI workflow runs the same suite on GitHub Actions using Python 3.12.
Deployment on Streamlit Community Cloud
Push this repository to GitHub.
Select `streamlit\_app.py` as the main file.
Keep `requirements.txt` in the repository root.
Optional: configure SMTP credentials in App settings → Secrets.
Use the Automation page for the deterministic demo runner; GitHub Actions is used for CI tests only.
The application uses SQLite for the educational prototype. Do not use this architecture for production clinical data without a validated, persistent, appropriately hosted database and infrastructure.
Regulatory positioning
The application is designed to demonstrate concepts expected in a regulated computerized system: controlled roles, auditability, electronic-signature intent, traceable corrections, validated-like imports, metadata, data integrity controls and controlled exports.
It is not a claim of compliance certification. A production system would still require formal URS, risk assessment, supplier assessment, validation/qualification, SOPs, security controls, backup/restore testing, infrastructure qualification, change control, release management, appropriate hosting and a validated CDISC implementation.
Demo accounts
Username	Password	Role
`lab\_tech1`	`labtech2026`	Laboratory Technician
`biologist1`	`bio2026`	Biologist
`physician1`	`doc2026`	Investigator Physician
`cro\_arc`	`cro2026`	CRO – Clinical Services
`sponsor\_lph`	`sponsor2026`	LPH Sponsor
These credentials are for the fictional demonstration environment only.