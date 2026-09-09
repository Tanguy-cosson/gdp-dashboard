STUDYID = "BLOOD"

ROLE_LABELS = {
    "LAB_TECH": "Laboratory Technician (ingestion + technical validation)",
    "BIOLOGIST": "Biologist (biological validation)",
    "PHYSICIAN": "Investigator Physician (patient records)",
    "CRO": "CRO Clinical Services (full oversight)",
    "SPONSOR": "Promoteur LPH (read-only)",
}

PAGE_PERMISSIONS = {
    "LAB_TECH": ["Data Ingestion", "Sample Labels", "Sample Scan", "Technical Validation"],
    "BIOLOGIST": ["Biological Validation", "Sample Scan", "Patient Records", "Notes"],
    "PHYSICIAN": ["Patient Records", "Notes"],
    "CRO": ["Dashboard", "Patient Search", "Patient follow-up", "Sample Labels",
            "Sample Scan", "Technical Validation", "Biological Validation",
            "Patient Records", "VINC extraction", "Notes", "Export CDISC SDTM",
            "Settings", "Audit Trail", "Automation"],
    "SPONSOR": ["VINC extraction", "Notes"],
}

OOR_FLAG = "⚠️ Outlier"
NORMAL_FLAG = "✅ Normal"
CRITICAL_FLAG = "🔴 CRITICAL"

SIGNATURE_REASONS = [
    "Reviewed and approved",
    "Approved with comment",
    "Reviewed — repeat test requested",
    "Reviewed — investigator notified",
]
