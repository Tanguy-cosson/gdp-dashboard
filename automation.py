"""
automation.py — Relances automatiques.

Canal principal : la messagerie interne (mailbox.py) — chaque relance
est réellement livrée dans la boîte de réception du destinataire
configuré, sans dépendre d'un SMTP externe. C'est ce qui permet à
l'automatisation de fonctionner pour de vrai, y compris en démo, sans
identifiants de messagerie réels.

Canal secondaire (optionnel) : un vrai e-mail SMTP est envoyé EN PLUS
si des identifiants sont configurés dans st.secrets["smtp"] — les deux
canaux ne s'excluent pas.

Limite assumée du compte Streamlit Community Cloud gratuit : il n'y a
pas de vrai cron côté serveur (l'appli peut être mise en veille). La
stratégie retenue est donc "vérifier à chaque chargement de page" :
check_and_send_reminders() est appelée une fois au démarrage de main().
Un throttle (SETTINGS.last_reminder_*) évite d'envoyer plusieurs fois
la même relance le même jour, même si plusieurs personnes ouvrent
l'appli.

Les listes de destinataires (SETTINGS.notify_*) contiennent désormais
des NOMS D'UTILISATEUR internes (usernames), pas des adresses e-mail —
voir page Automation pour les configurer via une liste déroulante des
comptes existants.
"""
import smtplib
from datetime import date, datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import streamlit as st

from audit import log_audit
from business_logic import compute_oor_flag
from constants import CRITICAL_FLAG
from db import (count_by_status, get_setting, now_utc_iso, read_audit_trail,
                 read_full_results, set_setting)
from mailbox import send_internal_message_to_many


def _smtp_configured():
    """st.secrets lève une exception (pas juste un dict vide) quand
    aucun secrets.toml n'existe du tout sur la machine — ce qui est le
    cas par défaut tant que personne n'a configuré le SMTP. On traite
    ça comme 'non configuré', pas comme une erreur."""
    try:
        return "smtp" in st.secrets
    except Exception:
        return False


def send_email(subject, body, recipients):
    """Wrapper public réutilisé ailleurs (ex: auth.py pour les e-mails
    de réinitialisation de mot de passe) — même logique que les
    relances automatiques, même tolérance aux erreurs."""
    return _send_email(subject, body, recipients)


def send_secure_report(subject, body, recipients, attachment_bytes, attachment_filename):
    """Envoie un e-mail avec le compte rendu PDF (chiffré par mot de
    passe, voir pdf_reports.generate_patient_pdf_report) en pièce
    jointe.

    ====================== POINT D'INTÉGRATION MSSANTÉ =====================
    Cette fonction envoie aujourd'hui via SMTP classique (chiffré au
    niveau du PDF lui-même, pas au niveau du transport). Ce n'est PAS
    une messagerie de santé sécurisée MSSanté : MSSanté nécessite une
    accréditation ANS et passe par un opérateur agréé (ex: Apicrypt,
    Mailiz, therapass) avec sa propre API ou passerelle SMTP dédiée.

    Le jour où vous avez un compte MSSanté opérateur, remplacez le
    corps de cette fonction par un appel à l'API de cet opérateur —
    la signature de la fonction (mêmes paramètres : sujet, corps,
    destinataires, pièce jointe) ne devrait pas avoir besoin de
    changer, donc aucun autre fichier du projet n'aura à être modifié.
    ==========================================================================
    """
    recipients = [r.strip() for r in recipients if r.strip()]
    if not recipients or not _smtp_configured():
        return False
    try:
        cfg = st.secrets["smtp"]
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = cfg["sender"]
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(body))

        part = MIMEApplication(attachment_bytes, Name=attachment_filename)
        part["Content-Disposition"] = f'attachment; filename="{attachment_filename}"'
        msg.attach(part)

        with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=15) as server:
            server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["sender"], recipients, msg.as_string())
        return True
    except Exception:
        return False


def _send_email(subject, body, recipients):
    """Envoie un e-mail via les identifiants stockés dans st.secrets["smtp"].
    Format attendu dans .streamlit/secrets.toml :

        [smtp]
        host = "smtp.example.com"
        port = 587
        username = "alerts@example.com"
        password = "..."
        sender = "alerts@example.com"

    Renvoie True si l'envoi a réussi, False sinon (jamais d'exception
    propagée vers l'appelant)."""
    recipients = [r.strip() for r in recipients if r.strip()]
    if not recipients or not _smtp_configured():
        return False
    try:
        cfg = st.secrets["smtp"]
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = cfg["sender"]
        msg["To"] = ", ".join(recipients)
        with smtplib.SMTP(cfg["host"], int(cfg.get("port", 587)), timeout=10) as server:
            server.starttls()
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["sender"], recipients, msg.as_string())
        return True
    except Exception:
        return False


def _already_sent_today(conn, key):
    last = get_setting(conn, key)
    if not last:
        return False
    try:
        last_date = datetime.fromisoformat(last).date()
    except ValueError:
        return False
    return last_date == date.today()


def check_and_send_reminders(conn):
    """À appeler une fois par rendu de main(). Toutes les conditions
    sont silencieuses si l'automatisation est désactivée (mode dégradé, pas d'erreur visible)."""
    if get_setting(conn, "automation_enabled", "1") != "1":
        return

    _check_ingestion_overdue(conn)
    _check_sponsor_extract(conn)
    _check_critical_pending(conn)


def preview_automation(conn):
    """Mode aperçu (dry-run) : exécute la MÊME logique de détection que
    check_and_send_reminders, mais SANS rien envoyer, sans toucher au
    throttle, et sans écrire dans l'audit trail. Utile en démonstration
    ou en configuration : on voit exactement ce qui se déclencherait,
    même sans SMTP configuré (le blocage réel se ferait uniquement à
    l'étape d'envoi, jamais à l'étape de détection)."""
    results = []

    threshold_days = int(get_setting(conn, "reminder_ingestion_days", "7"))
    audit_df = read_audit_trail(conn)
    ingestion_rows = audit_df[audit_df["action"] == "INGESTION_CSV"] if not audit_df.empty else audit_df
    if ingestion_rows is None or ingestion_rows.empty:
        overdue, detail = True, "Aucun import CSV n'a jamais été enregistré."
    else:
        last_dt = datetime.fromisoformat(ingestion_rows.iloc[0]["event_timestamp"])
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - last_dt).days
        overdue = days >= threshold_days
        detail = f"Dernier import il y a {days} jour(s) (seuil : {threshold_days})."
    results.append({
        "title": "Import hebdomadaire en retard",
        "would_trigger": overdue,
        "detail": detail,
        "recipients": (get_setting(conn, "notify_emails_lab", "") or "").split(","),
    })

    cur = conn.execute(
        "SELECT MAX(event_timestamp) FROM AUDIT_TRAIL "
        "WHERE action IN ('EXPORT_VINC_CRO','CONSULTATION_VINC_SPONSOR') "
        "AND strftime('%Y-%m', event_timestamp) = strftime('%Y-%m', 'now')"
    )
    row = cur.fetchone()
    already_done = bool(row and row[0])
    past_20th = date.today().day >= 20
    results.append({
        "title": "Extrait VINC mensuel non téléchargé",
        "would_trigger": (not already_done) and past_20th,
        "detail": ("Déjà exporté/consulté ce mois-ci." if already_done else
                   f"Pas encore exporté ce mois-ci (relance à partir du 20 — {'atteint' if past_20th else 'pas encore atteint'})."),
        "recipients": (get_setting(conn, "notify_emails_sponsor", "") or "").split(","),
    })

    df = read_full_results(conn)
    n_critical, patients = 0, 0
    if not df.empty:
        flagged = compute_oor_flag(df)
        unresolved = flagged[(flagged["Alerte"] == CRITICAL_FLAG) & (flagged["status"] != "REVIEWED")]
        n_critical = len(unresolved)
        patients = unresolved["usubjid"].nunique() if n_critical else 0
    results.append({
        "title": "Valeur(s) critique(s) en attente de validation",
        "would_trigger": n_critical > 0,
        "detail": (f"{n_critical} valeur(s) critique(s) sur {patients} patient(s)." if n_critical
                   else "Aucune valeur critique non résolue actuellement."),
        "recipients": (get_setting(conn, "notify_emails_critical", "") or "").split(","),
    })

    return results


def _check_ingestion_overdue(conn):
    if _already_sent_today(conn, "last_reminder_ingestion_sent"):
        return
    threshold_days = int(get_setting(conn, "reminder_ingestion_days", "7"))
    audit_df = read_audit_trail(conn)
    ingestion_rows = audit_df[audit_df["action"] == "INGESTION_CSV"] if not audit_df.empty else audit_df

    overdue = False
    if ingestion_rows is None or ingestion_rows.empty:
        overdue = True
    else:
        last_dt = datetime.fromisoformat(ingestion_rows.iloc[0]["event_timestamp"])
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - last_dt).days
        overdue = days >= threshold_days

    if not overdue:
        return

    recipients = (get_setting(conn, "notify_emails_lab", "") or "").split(",")
    subject = "Import hebdomadaire en retard"
    body = (f"Aucun import CSV n'a été enregistré depuis au moins {threshold_days} jour(s). "
            f"Merci de déposer le fichier hebdomadaire du laboratoire central.")
    n_delivered = send_internal_message_to_many(conn, recipients, subject, body)
    also_emailed = _send_email(subject=f"[BLOOD Study] {subject}", body=body, recipients=recipients)

    if n_delivered or also_emailed:
        set_setting(conn, "last_reminder_ingestion_sent", now_utc_iso())
        log_audit(conn, "AUTOMATION", "REMINDER_SENT", "system",
                  comment=f"ingestion_overdue -> {n_delivered} message(s) interne(s)"
                          f"{' + e-mail' if also_emailed else ''}")


def _check_sponsor_extract(conn):
    if _already_sent_today(conn, "last_reminder_sponsor_sent"):
        return
    cur = conn.execute(
        "SELECT MAX(event_timestamp) FROM AUDIT_TRAIL "
        "WHERE action IN ('EXPORT_VINC_CRO','CONSULTATION_VINC_SPONSOR') "
        "AND strftime('%Y-%m', event_timestamp) = strftime('%Y-%m', 'now')"
    )
    row = cur.fetchone()
    if row and row[0]:
        return  # déjà consulté/exporté ce mois-ci

    # Ne relancer qu'à partir du 20 du mois pour laisser le temps normal du cycle
    if date.today().day < 20:
        return

    recipients = (get_setting(conn, "notify_emails_sponsor", "") or "").split(",")
    subject = "Extrait VINC mensuel non téléchargé"
    body = "L'extrait VINC de ce mois-ci n'a pas encore été téléchargé ni consulté."
    n_delivered = send_internal_message_to_many(conn, recipients, subject, body)
    also_emailed = _send_email(subject=f"[BLOOD Study] {subject}", body=body, recipients=recipients)

    if n_delivered or also_emailed:
        set_setting(conn, "last_reminder_sponsor_sent", now_utc_iso())
        log_audit(conn, "AUTOMATION", "REMINDER_SENT", "system",
                  comment=f"sponsor_extract -> {n_delivered} message(s) interne(s)"
                          f"{' + e-mail' if also_emailed else ''}")


def _check_critical_pending(conn):
    if _already_sent_today(conn, "last_reminder_critical_sent"):
        return
    df = read_full_results(conn)
    if df.empty:
        return
    df = compute_oor_flag(df)
    unresolved = df[(df["Alerte"] == CRITICAL_FLAG) & (df["status"] != "REVIEWED")]
    if unresolved.empty:
        return

    recipients = (get_setting(conn, "notify_emails_critical", "") or "").split(",")
    n = len(unresolved)
    patients = unresolved["usubjid"].nunique()
    subject = f"{n} valeur(s) CRITIQUE(S) en attente de validation"
    body = (f"{n} valeur(s) critique(s) (panic values) sur {patients} patient(s) sont encore en "
            f"attente de validation. Attention immédiate requise.")
    n_delivered = send_internal_message_to_many(conn, recipients, subject, body)
    also_emailed = _send_email(subject=f"[BLOOD Study] {subject}", body=body, recipients=recipients)

    if n_delivered or also_emailed:
        set_setting(conn, "last_reminder_critical_sent", now_utc_iso())
        log_audit(conn, "AUTOMATION", "REMINDER_SENT", "system",
                  comment=f"critical_pending -> {n_delivered} message(s) interne(s)"
                          f"{' + e-mail' if also_emailed else ''}")
