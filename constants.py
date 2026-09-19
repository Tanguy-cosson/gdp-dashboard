STUDYID = "BLOOD"

ROLE_LABELS = {
    "LAB_TECH": "Laboratory Technician (ingestion + technical validation)",
    "BIOLOGIST": "Biologist (biological validation)",
    "PHYSICIAN": "Investigator Physician (patient records)",
    "CRO": "CRO Clinical Services (full oversight)",
    "SPONSOR": "Promoteur LPH (read-only)",
}

PAGE_PERMISSIONS = {
    "LAB_TECH": ["Data Ingestion", "HL7 Import", "Sample Labels", "Sample Scan", "Storage Map",
                 "Technical Validation", "Process Flow", "Messagerie", "Mon Compte", "Guide"],
    "BIOLOGIST": ["Biological Validation", "Sample Scan", "Patient Records", "Notes",
                  "Process Flow", "Messagerie", "Mon Compte", "Guide"],
    "PHYSICIAN": ["Patient Records", "Notes", "Messagerie", "Mon Compte", "Guide"],
    "CRO": ["Dashboard", "Process Flow", "Patient Search", "Patient follow-up", "Sample Labels",
            "Sample Scan", "Storage Map", "Technical Validation", "Biological Validation",
            "Patient Records", "VINC extraction", "Notes", "Export CDISC SDTM",
            "Data Privacy", "Messagerie", "Mon Compte", "Settings", "Automation",
            "User Management", "Audit Trail", "Guide"],
    "SPONSOR": ["VINC extraction", "Notes", "Messagerie", "Mon Compte", "Guide"],
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

# ---------------------------------------------------------------------
# Ergonomie : icônes de navigation (préfixées aux libellés de page dans
# la barre latérale, pour un repérage plus rapide) et rappel des
# comptes de démo affiché sur la page d'accueil.
# ---------------------------------------------------------------------
PAGE_ICONS = {
    "Data Ingestion": "📥", "HL7 Import": "🔌", "Sample Labels": "🏷️",
    "Sample Scan": "📷", "Storage Map": "🧊", "Process Flow": "🧭", "Technical Validation": "🧪",
    "Biological Validation": "🧬", "Dashboard": "📊", "Patient Search": "🔍",
    "Patient follow-up": "📋", "Patient Records": "👤", "VINC extraction": "📤",
    "Notes": "📝", "Export CDISC SDTM": "📦", "Data Privacy": "🔒",
    "Settings": "⚙️", "Automation": "🤖", "User Management": "👥",
    "Audit Trail": "🕵️", "Guide": "❓", "Messagerie": "📧", "Mon Compte": "🪪",
}

DEMO_ACCOUNTS = [
    ("lab_tech1", "labtech2026", "Technicien de laboratoire"),
    ("biologist1", "bio2026", "Biologiste"),
    ("physician1", "doc2026", "Médecin investigateur"),
    ("cro_arc", "cro2026", "CRO (accès complet)"),
    ("sponsor_lph", "sponsor2026", "Promoteur (Sponsor)"),
]
