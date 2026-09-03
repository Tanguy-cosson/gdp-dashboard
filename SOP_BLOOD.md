# SOP — Transmission and availability of central lab results — study BLOOD

## 1. Purpose
Describe how Clinical Services (CRO) receives, checks and makes available the blood
analysis results produced by Central Lab Results for the BLOOD study, so that the
sponsor LPH obtains, every month, the results of the inclusion visit (VINC) for all
patients included to date.

## 2. Scope
Applies to all Clinical Services staff (CRA, Data Manager, IT) involved in the BLOOD
study, for visits VINC, V1 and V2.

## 3. Responsibilities
- **Central Lab Results**: transmits the analysis file every week in CSV format.
- **Data Manager (CRO)**: imports the file into the portal, reviews rejected rows and corrections.
- **CRA (CRO)**: reviews out-of-range values and escalates clinically relevant findings to the investigator.
- **Sponsor (LPH)**: reads results live and downloads the monthly VINC export.

## 4. Weekly procedure — receiving the lab file
a. Retrieve the weekly CSV file from Central Lab Results and check the file name and transmission date.
b. Sign in to the portal and open Import. Check that the file contains the columns
   patient_code, visit, sample_date, analyte, value, unit, ref_low, ref_high, site.
c. Upload the file. Records the import automatically in the audit trail.
d. Review the list of rejected rows. Request a corrected file from the lab for any
   rejected row; never edit source data manually.
e. Confirm on the Results page that the new rows appear and that the total matches the file.

## 5. Monthly procedure — sponsor deliverable
a. On the first working day of each month, open Sponsor export.
b. Select the reporting month and keep the default VINC-only scope, which corresponds
   to the sponsor's contractual request.
c. Generate the CSV, check the row count, and confirm the deliverable with the sponsor.
   The export is logged with the user, date and row count.
d. LPH may also download the export themselves at any time — their read access is live.

## 6. Data integrity and traceability
- Results cannot be modified or deleted once imported; a correction is issued as a
  new corrected file by the central lab.
- Every sign-in, view, import and export is recorded in the audit trail with the
  user identity and timestamp.
- Access is role-based: only the central lab, the CRO and administrators can import;
  all study roles can read.

## 7. In case of absence
Any Clinical Services colleague holding the CRO role can perform sections 4 and 5
without further handover: the file format, the import path and the monthly export
are identical for every user. If the weekly file has not arrived by the expected
day, contact Central Lab Results and record the delay in the study file.