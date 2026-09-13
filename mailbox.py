"""
mailbox.py — Messagerie interne à l'application.

Contexte : sans identifiants SMTP réels, l'automatisation ne peut pas
envoyer de vrais e-mails de façon démontrable. Plutôt que de rester
sur une simple simulation ("voici ce qui SERAIT envoyé"), ce module
livre réellement chaque message dans une boîte de réception interne,
consultable par le destinataire dans l'application — même principe
qu'une messagerie universitaire (identifiant + mot de passe du site,
pas un vrai compte Gmail/Outlook).

L'envoi SMTP réel (automation.send_email / send_secure_report) reste
disponible EN PLUS si vous configurez un jour de vrais identifiants —
les deux canaux ne s'excluent pas : un message peut être livré en
interne ET par e-mail réel en parallèle.
"""
import pandas as pd

from db import now_utc_iso


def send_internal_message(conn, recipient_username, subject, body,
                           sender_username=None, sender_label="Automatisation LIMS",
                           attachment_bytes=None, attachment_name=None,
                           attachment_mimetype=None):
    conn.execute(
        "INSERT INTO MESSAGES (recipient_username, sender_username, sender_label, "
        "subject, body, attachment_name, attachment_data, attachment_mimetype, "
        "is_read, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (recipient_username, sender_username, sender_label, subject, body,
         attachment_name, attachment_bytes, attachment_mimetype, now_utc_iso()),
    )
    conn.commit()


def send_internal_message_to_many(conn, recipient_usernames, subject, body, **kwargs):
    """Envoie le même message à plusieurs destinataires (une ligne par
    destinataire). Renvoie le nombre de messages effectivement créés
    (les entrées vides/blanches sont ignorées)."""
    sent = 0
    for username in recipient_usernames:
        username = (username or "").strip()
        if username:
            send_internal_message(conn, username, subject, body, **kwargs)
            sent += 1
    return sent


def get_inbox(conn, username):
    return pd.read_sql_query(
        "SELECT message_id, sender_label, subject, is_read, created_at, "
        "attachment_name FROM MESSAGES WHERE recipient_username = ? "
        "ORDER BY created_at DESC",
        conn, params=(username,),
    )


def get_message(conn, message_id, recipient_username):
    """Récupère un message — vérifie que le destinataire correspond
    bien à l'utilisateur connecté (pas de lecture croisée entre comptes)."""
    cur = conn.execute(
        "SELECT message_id, sender_username, sender_label, subject, body, "
        "attachment_name, attachment_data, attachment_mimetype, is_read, created_at "
        "FROM MESSAGES WHERE message_id = ? AND recipient_username = ?",
        (message_id, recipient_username),
    )
    row = cur.fetchone()
    if row is None:
        return None
    keys = ["message_id", "sender_username", "sender_label", "subject", "body",
            "attachment_name", "attachment_data", "attachment_mimetype", "is_read", "created_at"]
    return dict(zip(keys, row))


def mark_as_read(conn, message_id, recipient_username):
    conn.execute(
        "UPDATE MESSAGES SET is_read = 1 WHERE message_id = ? AND recipient_username = ?",
        (message_id, recipient_username),
    )
    conn.commit()


def count_unread(conn, username):
    cur = conn.execute(
        "SELECT COUNT(*) FROM MESSAGES WHERE recipient_username = ? AND is_read = 0",
        (username,),
    )
    return cur.fetchone()[0]


def list_active_usernames(conn, exclude_username=None):
    cur = conn.execute(
        "SELECT username, full_name, role FROM USERS WHERE active = 1 ORDER BY full_name"
    )
    rows = cur.fetchall()
    if exclude_username:
        rows = [r for r in rows if r[0] != exclude_username]
    return rows


def list_usernames_by_role(conn, role):
    cur = conn.execute("SELECT username FROM USERS WHERE role = ? AND active = 1", (role,))
    return [r[0] for r in cur.fetchall()]
