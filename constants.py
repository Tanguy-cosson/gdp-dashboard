STUDYID = "BLOOD"

ROLE_LABELS = {
    "LAB_TECH": "Laboratory Technician (ingestion + technical validation)",
    "BIOLOGIST": "Biologist (biological validation)",
    "PHYSICIAN": "Investigator Physician (patient records)",
    "CRO": "CRO Clinical Services (full oversight)",
    "SPONSOR": "Promoteur LPH (read-only)",
}

PAGE_PERMISSIONS = {
    "LAB_TECH": ["Data Ingestion", "HL7 Import", "Sample Labels", "Sample Scan", "Technical Validation", "Process Flow"],
    "BIOLOGIST": ["Biological Validation", "Sample Scan", "Patient Records", "Notes", "Process Flow"],
    "PHYSICIAN": ["Patient Records", "Notes"],
    "CRO": ["Dashboard", "Process Flow", "Patient Search", "Patient follow-up", "Sample Labels",
            "Sample Scan", "Technical Validation", "Biological Validation",
            "Patient Records", "VINC extraction", "Notes", "Export CDISC SDTM",
            "Data Privacy", "Settings", "Automation", "User Management", "Audit Trail"],
    "SPONSOR": ["VINC extraction", "Notes"],
}

# ---------------------------------------------------------------------
# Sécurité des comptes
# ---------------------------------------------------------------------
LOGIN_LOCKOUT_THRESHOLD = 5          # tentatives échouées avant verrouillage
LOGIN_LOCKOUT_MINUTES = 15           # durée du verrouillage
PASSWORD_RESET_TOKEN_MINUTES = 60    # durée de validité d'un lien de réinitialisation

ALL_ROLES = ["LAB_TECH", "BIOLOGIST", "PHYSICIAN", "CRO", "SPONSOR"]

ESIGNATURE_LEGAL_NOTICE = (
    "By entering your password to sign, you are applying your unique electronic "
    "signature to this record. Under 21 CFR Part 11 §11.100, this signature is "
    "legally binding and equivalent to a handwritten signature on paper."
)

OOR_FLAG = "⚠️ Outlier"
NORMAL_FLAG = "✅ Normal"
CRITICAL_FLAG = "🔴 CRITICAL"

SIGNATURE_REASONS = [
    "Reviewed and approved",
    "Approved with comment",
    "Reviewed — repeat test requested",
    "Reviewed — investigator notified",
]
