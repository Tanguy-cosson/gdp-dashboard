"""
automation.py — Relances automatiques par e-mail.

Limite assumée du compte Streamlit Community Cloud gratuit : il n'y a
pas de vrai cron côté serveur (l'appli peut être mise en veille). La
stratégie retenue est donc "vérifier à chaque chargement de page" :
check_and_send_reminders() est appelée une fois au démarrage de main().
Un throttle (SETTINGS.last_reminder_*) évite d'envoyer plusieurs fois
la même relance le même jour, même si plusieurs personnes ouvrent
l'appli. Pour une vraie exécution planifiée indépendante des visites
(ex: tous les jours à 8h même si personne n'ouvre l'appli), il faudra
migrer vers un backend avec un scheduler externe (GitHub Actions cron
appelant un script qui se connecte à une base partagée, ou Streamlit
Cloud payant avec un worker séparé) — voir README, section "Limites
connues".

Toute erreur SMTP est absorbée : l'automatisation ne doit jamais faire
planter l'application pour les utilisateurs métier.
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
    sont silencieuses si l'automatisation est désactivée ou si le SMTP
    n'est pas configuré (mode dégradé, pas d'erreur visible)."""
    if get_setting(conn, "automation_enabled", "1") != "1":
        return

    _check_ingestion_overdue(conn)
    _check_sponsor_extract(conn)
    _check_critical_pending(conn)


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
    sent = _send_email(
        subject="[BLOOD Study] Weekly lab file overdue",
        body=(f"No CSV ingestion has been recorded in the last {threshold_days} day(s). "
              f"Please submit the weekly Central Lab file."),
        recipients=recipients,
    )
    if sent:
        set_setting(conn, "last_reminder_ingestion_sent", now_utc_iso())
        log_audit(conn, "AUTOMATION", "REMINDER_SENT", "system", comment="ingestion_overdue")


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
    sent = _send_email(
        subject="[BLOOD Study] Monthly VINC extract not yet retrieved",
        body="The VINC extract for this month has not been downloaded or consulted yet.",
        recipients=recipients,
    )
    if sent:
        set_setting(conn, "last_reminder_sponsor_sent", now_utc_iso())
        log_audit(conn, "AUTOMATION", "REMINDER_SENT", "system", comment="sponsor_extract")


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
    sent = _send_email(
        subject=f"[BLOOD Study] {n} CRITICAL value(s) pending validation",
        body=(f"{n} critical (panic) value(s) across {patients} patient(s) are still "
              f"awaiting validation. Immediate attention required."),
        recipients=recipients,
    )
    if sent:
        set_setting(conn, "last_reminder_critical_sent", now_utc_iso())
        log_audit(conn, "AUTOMATION", "REMINDER_SENT", "system", comment="critical_pending")
