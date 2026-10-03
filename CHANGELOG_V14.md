# BLOOD LIMS V14 — Final Change Log

## V14.0 — 2026-10-03

### User experience
- New Clinical Services logo asset and sidebar presentation.
- Top banner spacing adjusted so the title is not clipped.
- English / Français language selection retained across the UI.

### Archive
- Automatic archive entry on CSV receipt.
- Automatic archive entry on HL7 receipt.
- Automatic archive of monthly sponsor packages and sponsor receipt copies.
- CRO taxonomy: `YYYY/Wxx/stakeholder/document_type/file`.
- Sponsor taxonomy: `YYYY/Qn/YYYY-MM/stakeholder/document_type/file`.
- Archive browser integrated into existing Dashboard / Automation pages; no new top-level page added.
- Weekly and sponsor archive ZIP bundles include manifest metadata and hashes.

### Dashboard
- Live local time + UTC.
- KPI cards and workload/validation indicators.
- Status funnel / distribution views.
- Results by visit/test and by time.
- Critical queue and recent activity.
- Weekly Central Lab cadence and monthly sponsor delivery cadence.
- CRO and sponsor archive retrieval.

### Demonstration history
- Multi-month historical loader with explicit training-only behavior.
- 12 chronological CSV batches (99 results total).
- 13 demo subjects and 4 demo sites across VINC/V1/V2.
- Mixed workflow states: REVIEWED, TECHNICAL_OK, PENDING.
- Historical critical/OOR scenarios.
- Three HL7 ORU^R01 files.
- Historical sponsor packages covering multiple months.

### Documentation
- User manual
- Test & demo protocol
- Automation & archive SOP
- Dashboard/KPI guide
- Architecture/data model/CDISC
- Regulatory framework/gap analysis
- Functional dependencies/maturity
- Installation/release notes
- Exact demo data catalogue and note matrix
- Excel traceability/KPI workbook

### Validation
- V14 regression suite: 81 tests passing in the controlled development test environment.
- This is evidence for the software test suite, not a statement of regulatory qualification.
