"""Lightweight bilingual UI layer for the BLOOD LIMS demonstration."""
from __future__ import annotations
import streamlit as st

SUPPORTED_LANGUAGES = {"English": "en", "Français": "fr"}
TRANSLATIONS = {
    "en": {
        "Language":"Language","Navigation":"Navigation","Dashboard":"Dashboard",
        "DATA INGESTION":"DATA INGESTION","SAMPLE LABELS (barcode printing)":"SAMPLE LABELS (barcode printing)","🧊 STORAGE MAP":"🧊 STORAGE MAP","🧪 BIOLOGICAL VALIDATION":"🧪 BIOLOGICAL VALIDATION","🪪 MY ACCOUNT":"🪪 MY ACCOUNT","📧 MAILBOX":"📧 MAILBOX","❓ GUIDE":"❓ GUIDE","CDISC SDTM EXPORT":"CDISC SDTM EXPORT","HL7 IMPORT":"HL7 IMPORT","SAMPLE LABELS":"SAMPLE LABELS","SAMPLE SCAN":"SAMPLE SCAN","STORAGE MAP":"STORAGE MAP","TECHNICAL VALIDATION":"TECHNICAL VALIDATION","BIOLOGICAL VALIDATION":"BIOLOGICAL VALIDATION","PROCESS FLOW":"PROCESS FLOW","PATIENT SEARCH":"PATIENT SEARCH","PATIENT FOLLOW-UP":"PATIENT FOLLOW-UP","PATIENT RECORDS":"PATIENT RECORDS","VINC VISIT RESULTS":"VINC VISIT RESULTS","RESULT COMMENTS":"RESULT COMMENTS","EXPORT CDISC SDTM":"EXPORT CDISC SDTM","DATA PRIVACY (RGPD)":"DATA PRIVACY (RGPD)","AUTOMATION":"AUTOMATION","AUDIT TRAIL":"AUDIT TRAIL","SETTINGS":"SETTINGS","USER MANAGEMENT":"USER MANAGEMENT","MY ACCOUNT":"MY ACCOUNT",
        "Process Flow":"Process Flow","Patient Search":"Patient Search","Patient follow-up":"Patient follow-up",
        "Sample Labels":"Sample Labels","Sample Scan":"Sample Scan","Storage Map":"Storage Map",
        "Technical Validation":"Technical Validation","Biological Validation":"Biological Validation",
        "Patient Records":"Patient Records","VINC extraction":"VINC extraction","Notes":"Notes",
        "Export CDISC SDTM":"Export CDISC SDTM","Data Privacy":"Data Privacy","Mailbox":"Mailbox",
        "My Account":"My Account","Settings":"Settings","Automation":"Automation",
        "Data Correction / Void":"Data Correction / Void","User Management":"User Management",
        "Audit Trail":"Audit Trail","Guide":"Guide","Data Ingestion":"Data Ingestion","HL7 Import":"HL7 Import",
        "Real-time overview":"Real-time operational overview","Local time":"Local time","UTC time":"UTC time",
        "Total results":"Total results","Patients":"Patients","Sites":"Sites","Reviewed":"Reviewed",
        "Critical pending":"Critical pending","Validation progress":"Validation progress",
        "Results by visit":"Results by visit","Alerts distribution":"Alerts distribution","Results over time":"Results over time",
        "Recent critical alerts":"Recent critical alerts","Recent system activity":"Recent system activity",
        "Contractual cadence":"Contractual cadence","Weekly lab → CRO":"Weekly lab → CRO",
        "Monthly CRO → sponsor":"Monthly CRO → sponsor","Document archive":"Document archive",
        "Archive year":"Archive year","ISO week":"ISO week","Stakeholder":"Stakeholder","Quarter":"Quarter","Month":"Month","All months":"All months",
        "Sponsor archive":"Sponsor archive","No sponsor archive documents":"No sponsor archive documents for this period.","Build sponsor archive bundle":"Build sponsor archive bundle",
        "Build weekly archive bundle":"Build weekly archive bundle","No archived documents":"No archived documents for this period.",
        "Log out":"Log out","Password":"Password","User identifiant":"User identifiant","Log in":"Log in","Archive":"Archive","Archive explorer":"Archive explorer","CRO archive":"CRO archive","Sponsor archive":"Sponsor archive","Sponsor-visible documents only":"Sponsor-visible documents only",
    },
    "fr": {
        "Language":"Langue","Navigation":"Navigation","Dashboard":"Tableau de bord",
        "DATA INGESTION":"INGESTION DES DONNÉES","SAMPLE LABELS (barcode printing)":"ÉTIQUETTES ÉCHANTILLONS (impression code-barres)","🧊 STORAGE MAP":"🧊 CARTE DE STOCKAGE","🧪 BIOLOGICAL VALIDATION":"🧪 VALIDATION BIOLOGIQUE","🪪 MY ACCOUNT":"🪪 MON COMPTE","📧 MAILBOX":"📧 MESSAGERIE","❓ GUIDE":"❓ GUIDE","CDISC SDTM EXPORT":"EXPORT CDISC SDTM","HL7 IMPORT":"IMPORT HL7","SAMPLE LABELS":"ÉTIQUETTES ÉCHANTILLONS","SAMPLE SCAN":"SCAN ÉCHANTILLON","STORAGE MAP":"CARTE DE STOCKAGE","TECHNICAL VALIDATION":"VALIDATION TECHNIQUE","BIOLOGICAL VALIDATION":"VALIDATION BIOLOGIQUE","PROCESS FLOW":"FLUX DE PROCESSUS","PATIENT SEARCH":"RECHERCHE PATIENT","PATIENT FOLLOW-UP":"SUIVI DES PATIENTS","PATIENT RECORDS":"DOSSIERS PATIENTS","VINC VISIT RESULTS":"RÉSULTATS VINC","RESULT COMMENTS":"COMMENTAIRES RÉSULTATS","EXPORT CDISC SDTM":"EXPORT CDISC SDTM","DATA PRIVACY (RGPD)":"PROTECTION DES DONNÉES (RGPD)","AUTOMATION":"AUTOMATISATION","AUDIT TRAIL":"PISTE D’AUDIT","SETTINGS":"PARAMÈTRES","USER MANAGEMENT":"GESTION DES UTILISATEURS","MY ACCOUNT":"MON COMPTE",
        "Process Flow":"Flux de processus","Patient Search":"Recherche patient","Patient follow-up":"Suivi des patients",
        "Sample Labels":"Étiquettes échantillons","Sample Scan":"Scan échantillon","Storage Map":"Carte de stockage",
        "Technical Validation":"Validation technique","Biological Validation":"Validation biologique",
        "Patient Records":"Dossiers patients","VINC extraction":"Extraction VINC","Notes":"Notes",
        "Export CDISC SDTM":"Export CDISC SDTM","Data Privacy":"Protection des données","Mailbox":"Messagerie",
        "My Account":"Mon compte","Settings":"Paramètres","Automation":"Automatisation",
        "Data Correction / Void":"Correction / Void des données","User Management":"Gestion des utilisateurs",
        "Audit Trail":"Piste d’audit","Guide":"Guide","Data Ingestion":"Ingestion des données","HL7 Import":"Import HL7",
        "Real-time overview":"Vue opérationnelle en temps réel","Local time":"Heure locale","UTC time":"Heure UTC",
        "Total results":"Résultats totaux","Patients":"Patients","Sites":"Sites","Reviewed":"Validés",
        "Critical pending":"Critiques en attente","Validation progress":"Avancement des validations",
        "Results by visit":"Résultats par visite","Alerts distribution":"Répartition des alertes","Results over time":"Résultats dans le temps",
        "Recent critical alerts":"Alertes critiques récentes","Recent system activity":"Activité système récente",
        "Contractual cadence":"Cadence contractuelle","Weekly lab → CRO":"Hebdomadaire laboratoire → CRO",
        "Monthly CRO → sponsor":"Mensuelle CRO → promoteur","Document archive":"Archive documentaire",
        "Archive year":"Année d'archive","ISO week":"Semaine ISO","Stakeholder":"Partie prenante",
        "Build weekly archive bundle":"Créer le dossier d'archive hebdomadaire","No archived documents":"Aucun document archivé pour cette période.",
        "Log out":"Déconnexion","Password":"Mot de passe","User identifiant":"Identifiant utilisateur","Log in":"Connexion","Archive":"Archive","Archive explorer":"Explorateur d’archives","CRO archive":"Archive CRO","Sponsor archive":"Archive promoteur","Sponsor-visible documents only":"Uniquement les documents visibles par le promoteur",
    },
}

def get_language() -> str:
    return st.session_state.get("ui_lang", "en")

def set_language(lang_code: str) -> None:
    st.session_state.ui_lang = lang_code if lang_code in TRANSLATIONS else "en"

def tr(key: str, fallback: str | None = None) -> str:
    return TRANSLATIONS.get(get_language(), TRANSLATIONS["en"]).get(key, fallback or key)

def translate_page(page_name: str) -> str:
    return tr(page_name, page_name)
