"""
auth.py — Authentification, session, sécurité des comptes.

Trois mécanismes de sécurité ajoutés par rapport à la version initiale :
1. Verrouillage temporaire après plusieurs échecs de connexion
   (protection brute-force absente auparavant).
2. Changement de mot de passe FORCÉ après une réinitialisation par un
   administrateur CRO (l'utilisateur ne peut pas continuer à utiliser
   le mot de passe temporaire indéfiniment).
3. "Mot de passe oublié" en self-service : jeton à usage unique envoyé
   par e-mail (via automation.send_email), valable 60 minutes.

Toujours pas de repli en clair sur le mot de passe : si bcrypt échoue,
c'est un refus, point final.
"""
import secrets
import string
from datetime import datetime, timedelta, timezone

import bcrypt
import streamlit as st

from audit import log_audit
from constants import (LOGIN_LOCKOUT_MINUTES, LOGIN_LOCKOUT_THRESHOLD,
                        PASSWORD_RESET_TOKEN_MINUTES, ROLE_LABELS)
from db import (clear_must_change_password, consume_password_reset_token,
                create_password_reset_token, find_user_by_username_or_email,
                record_login_failure, record_login_success)

SESSION_TIMEOUT_MINUTES = 30


# ---------------------------------------------------------------------
# Mots de passe
# ---------------------------------------------------------------------
def validate_password_complexity(password):
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit."
    return True, "OK"


def generate_temp_password():
    """Mot de passe temporaire aléatoire qui respecte toujours la
    politique de complexité (utilisé par le CRO lors de la création
    d'un compte ou d'une réinitialisation)."""
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(12))
        ok, _ = validate_password_complexity(pwd)
        if ok:
            return pwd


def hash_password(plain_password):
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(plain_password, password_hash):
    """Aucun repli en clair : si le hash est absent/corrompu ou si la
    vérification échoue pour n'importe quelle raison, on refuse."""
    if not password_hash or not plain_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def get_user_record(conn, username):
    cur = conn.execute(
        "SELECT role, full_name, password_hash, email, active, locked_until, "
        "must_change_password FROM USERS WHERE username = ?",
        (username,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "role": row[0], "full_name": row[1], "password_hash": row[2], "email": row[3],
        "active": bool(row[4]), "locked_until": row[5], "must_change_password": bool(row[6]),
    }


def update_password(conn, username, new_password):
    new_hash = hash_password(new_password)
    conn.execute("UPDATE USERS SET password_hash = ? WHERE username = ?", (new_hash, username))
    conn.commit()


def _is_locked(record):
    if not record.get("locked_until"):
        return False
    locked_until = datetime.fromisoformat(record["locked_until"])
    if locked_until.tzinfo is None:
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < locked_until


# ---------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------
def session_expired():
    if "last_activity" not in st.session_state:
        return False
    elapsed = datetime.now(timezone.utc) - st.session_state.last_activity
    return elapsed > timedelta(minutes=SESSION_TIMEOUT_MINUTES)


def touch_session():
    st.session_state.last_activity = datetime.now(timezone.utc)


def _reset_session():
    st.session_state.auth_username = None
    st.session_state.auth_role = None
    st.session_state.auth_full_name = None
    st.session_state.auth_must_change_password = False


# ---------------------------------------------------------------------
# Mot de passe oublié (self-service, par jeton e-mail)
# ---------------------------------------------------------------------
def handle_password_reset_flow(conn):
    """À appeler tout en haut de main(), AVANT le rendu de la sidebar de
    connexion. Si l'URL contient ?reset_token=..., affiche un
    formulaire de réinitialisation en plein écran et arrête le reste du
    rendu de la page (return True) tant que ce n'est pas résolu."""
    token = st.query_params.get("reset_token")
    if not token:
        return False

    st.title("🔑 Reset your password")
    with st.form("reset_password_form"):
        new_pwd = st.text_input("New password", type="password")
        confirm_pwd = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Set new password")

    if submitted:
        if new_pwd != confirm_pwd:
            st.error("Passwords do not match.")
        else:
            ok, msg = validate_password_complexity(new_pwd)
            if not ok:
                st.error(msg)
            else:
                username = consume_password_reset_token(conn, token)
                if username is None:
                    st.error("This reset link is invalid or has expired. Please request a new one.")
                else:
                    update_password(conn, username, new_pwd)
                    clear_must_change_password(conn, username)
                    record_login_success(conn, username)
                    log_audit(conn, "USERS", "PASSWORD_RESET_VIA_TOKEN", username)
                    st.success("Password updated. You can close this tab and log in with your new password.")
                    st.query_params.clear()
    return True


def _render_forgot_password(conn):
    with st.sidebar.expander("Forgot password?"):
        with st.form("forgot_password_form"):
            identifier = st.text_input("Your username or e-mail")
            submitted = st.form_submit_button("Send reset link")
            if submitted:
                username = find_user_by_username_or_email(conn, identifier)
                # Message identique que le compte existe ou non : évite de
                # révéler si un identifiant/e-mail est enregistré.
                st.info("If this account exists, a reset link has been sent.")
                if username:
                    from automation import send_email
                    record = get_user_record(conn, username)
                    token = secrets.token_urlsafe(32)
                    create_password_reset_token(conn, username, token, PASSWORD_RESET_TOKEN_MINUTES)
                    base_url = st.context.headers.get("Origin", "") if hasattr(st, "context") else ""
                    reset_link = f"{base_url}/?reset_token={token}" if base_url else f"?reset_token={token}"
                    sent = send_email(
                        subject="[BLOOD Study] Password reset request",
                        body=(f"A password reset was requested for your account.\n\n"
                              f"Open this link within {PASSWORD_RESET_TOKEN_MINUTES} minutes:\n{reset_link}\n\n"
                              f"If you did not request this, ignore this e-mail."),
                        recipients=[record["email"]] if record and record["email"] else [],
                    )
                    log_audit(conn, "USERS", "PASSWORD_RESET_REQUESTED", username,
                              comment="email sent" if sent else "email not sent (SMTP not configured?)")


# ---------------------------------------------------------------------
# Connexion / sidebar
# ---------------------------------------------------------------------
def sidebar_user_identification(conn):
    st.sidebar.markdown('<div class="sidebar-login-title">🫆 Identifiant</div>',
                         unsafe_allow_html=True)

    if "auth_username" not in st.session_state:
        _reset_session()

    if st.session_state.auth_username:
        if session_expired():
            log_audit(conn, "USERS", "SESSION_TIMEOUT", st.session_state.auth_username)
            _reset_session()
            st.sidebar.warning(
                f"Session expired after {SESSION_TIMEOUT_MINUTES} minutes of inactivity. "
                "Please log in again."
            )
            return None, None, None

        touch_session()

        # Changement de mot de passe forcé (réinitialisation par un admin CRO)
        if st.session_state.get("auth_must_change_password"):
            st.warning("⚠️ Your password was reset by an administrator. "
                       "You must set a new password before continuing.")
            with st.form("forced_change_password_form"):
                new_pwd = st.text_input("New password", type="password")
                confirm_pwd = st.text_input("Confirm new password", type="password")
                submitted = st.form_submit_button("Set new password")
                if submitted:
                    if new_pwd != confirm_pwd:
                        st.error("Passwords do not match.")
                    else:
                        ok, msg = validate_password_complexity(new_pwd)
                        if not ok:
                            st.error(msg)
                        else:
                            update_password(conn, st.session_state.auth_username, new_pwd)
                            clear_must_change_password(conn, st.session_state.auth_username)
                            log_audit(conn, "USERS", "PASSWORD_CHANGED_FORCED",
                                      st.session_state.auth_username)
                            st.session_state.auth_must_change_password = False
                            st.success("Password updated.")
                            st.rerun()
            return None, None, None  # bloque l'accès au reste de l'appli tant que non résolu

        st.sidebar.success(st.session_state.auth_full_name)
        st.sidebar.markdown(
            f'<span class="role-badge">{ROLE_LABELS.get(st.session_state.auth_role, "")}</span>',
            unsafe_allow_html=True,
        )

        with st.sidebar.expander("Change my password"):
            with st.form("change_password_form"):
                new_pwd = st.text_input("New password", type="password", key="new_pwd")
                confirm_pwd = st.text_input("Confirm new password", type="password", key="confirm_pwd")
                pwd_submitted = st.form_submit_button("Update password")
                if pwd_submitted:
                    if new_pwd != confirm_pwd:
                        st.sidebar.error("Passwords do not match.")
                    else:
                        ok, msg = validate_password_complexity(new_pwd)
                        if not ok:
                            st.sidebar.error(msg)
                        else:
                            update_password(conn, st.session_state.auth_username, new_pwd)
                            log_audit(conn, "USERS", "PASSWORD_CHANGED", st.session_state.auth_username)
                            st.sidebar.success("Password updated.")

        if st.sidebar.button("Log out"):
            log_audit(conn, "USERS", "LOGOUT", st.session_state.auth_username)
            _reset_session()
            st.rerun()

        return (st.session_state.auth_username, st.session_state.auth_role,
                st.session_state.auth_full_name)

    with st.sidebar.form("login_form"):
        username_input = st.text_input(
            "User identifiant", placeholder="e.g. lab_tech1 / biologist1 / physician1")
        password_input = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

        if submitted:
            record = get_user_record(conn, username_input)
            if record is None:
                st.sidebar.error("Incorrect username or password.")
                log_audit(conn, "USERS", "LOGIN_FAILED", username_input or "(vide)")
                return None, None, None

            if not record["active"]:
                st.sidebar.error("This account has been deactivated. Contact your CRO administrator.")
                log_audit(conn, "USERS", "LOGIN_BLOCKED_INACTIVE", username_input)
                return None, None, None

            if _is_locked(record):
                st.sidebar.error(f"Account temporarily locked after repeated failed attempts. "
                                  f"Try again in a few minutes.")
                log_audit(conn, "USERS", "LOGIN_BLOCKED_LOCKED", username_input)
                return None, None, None

            if not check_password(password_input, record["password_hash"]):
                just_locked = record_login_failure(conn, username_input,
                                                     LOGIN_LOCKOUT_THRESHOLD, LOGIN_LOCKOUT_MINUTES)
                st.sidebar.error("Incorrect username or password.")
                log_audit(conn, "USERS", "LOGIN_FAILED", username_input)
                if just_locked:
                    log_audit(conn, "USERS", "ACCOUNT_LOCKED", username_input,
                              comment=f"{LOGIN_LOCKOUT_THRESHOLD} failed attempts")
                return None, None, None

            record_login_success(conn, username_input)
            st.session_state.auth_username = username_input
            st.session_state.auth_role = record["role"]
            st.session_state.auth_full_name = record["full_name"]
            st.session_state.auth_must_change_password = record["must_change_password"]
            touch_session()
            log_audit(conn, "USERS", "LOGIN_SUCCESS", username_input)
            st.rerun()

    _render_forgot_password(conn)

    st.sidebar.markdown(
        '<div class="sidebar-login-hint">Please enter your username and password '
        'to continue.</div>', unsafe_allow_html=True)
    return None, None, None
