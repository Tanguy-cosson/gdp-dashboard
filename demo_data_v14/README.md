# BLOOD LIMS V14 demo data

Fictitious training data only.

## Historical CSV batches
Import in chronological order to simulate several months of operation. Each CSV contains a `source_received_datetime` column used only for controlled rehearsal so the application can preserve the simulated reception date and place the source artifact in the corresponding weekly archive. In production, reception time must come from the approved transfer channel.

1. W03 — initial VINC
2. W05 — new VINC
3. W07 — new VINC
4. W09 — first V1
5. W11 — VINC + new site
6. W13 — V1/V2 mix
7. W15 — new VINC + V1
8. W17 — critical follow-up
9. W19 — V1 follow-up
10. W21 — V2 follow-up
11. W23 — V2 follow-up
12. W25 — current VINC cohort

## HL7
Three file-based ORU^R01 messages are supplied for separate integration testing. Use the HL7 mapping screen to assign site, visit, visit number, visit date and sample type before insertion.

## Suggested historical state
The optional `seed_demo_history.py` script loads the same files into an empty training database, advances historical batches through the workflow, and creates sample sponsor packages/receipts so the archive browser is populated from multiple periods.
