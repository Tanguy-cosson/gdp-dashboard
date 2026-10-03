# BLOOD LIMS V14 — Final Touches

- CRO archive visible as ISO year / ISO week / stakeholder / document type.
- Sponsor archive visible as calendar year / quarter / month / stakeholder / document type.
- Automatic source archiving on CSV/HL7 receipt.
- Automatic sponsor package and sponsor-receipt archiving.
- Clinical Services logo updated to supplied wordmark.
- Top banner spacing adjusted to prevent title clipping.
- Multi-month historical demonstration pack: 12 chronological CSV batches + 3 HL7 messages.
- Optional `seed_demo_history.py` loads historical results and creates sponsor archive evidence in a training database.
- Optional Settings toggle can auto-load history when the database is empty; disabled by default.
- No new application pages were added.

## Important
This remains an educational GxP-oriented prototype. SQLite/local file storage is not a validated production persistence layer. The demo-history timestamp field is intended only for controlled rehearsal.
