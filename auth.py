"""
auth.py — Authentification, complexité de mot de passe, session.

Correction de sécurité importante par rapport à la version d'origine :
la comparaison "mot de passe en clair == hash" en repli (fallback) a
été supprimée. Si bcrypt.checkpw échoue ou lève une exception,
l'authentification est refusée, point final. Un mot de passe qui ne
serait pas correctement haché en base est un bug à corriger dans les
données, jamais une raison d'accepter une comparaison en clair.
"""
from datetime import datetime, timedelta, timezone

import bcrypt
import streamlit as st

from audit import log_audit
from constants import ROLE_LABELS

SESSION_TIMEOUT_MINUTES = 30


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


def get_user_record(conn, username):
    cur = conn.execute(
        "SELECT role, full_name, password_hash, email FROM USERS WHERE username = ?",
        (username,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"role": row[0], "full_name": row[1], "password_hash": row[2], "email": row[3]}


def check_password(plain_password, password_hash):
    """Aucun repli en clair : si le hash est absent/corrompu ou si la
    vérification échoue pour n'importe quelle raison, on refuse."""
    if not password_hash or not plain_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def update_password(conn, username, new_password):
    new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn.execute("UPDATE USERS SET password_hash = ? WHERE username = ?", (new_hash, username))
    conn.commit()


def session_expired():
    if "last_activity" not in st.session_state:
        return False
    elapsed = datetime.now(timezone.utc) - st.session_state.last_activity
    return elapsed > timedelta(minutes=SESSION_TIMEOUT_MINUTES)


def touch_session():
    st.session_state.last_activity = datetime.now(timezone.utc)


def sidebar_user_identification(conn):
    st.sidebar.markdown('<div class="sidebar-login-title">🫆 Identifiant</div>',
                         unsafe_allow_html=True)

    if "auth_username" not in st.session_state:
        st.session_state.auth_username = None
        st.session_state.auth_role = None
        st.session_state.auth_full_name = None

    if st.session_state.auth_username:
        if session_expired():
            log_audit(conn, "USERS", "SESSION_TIMEOUT", st.session_state.auth_username)
            st.session_state.auth_username = None
            st.session_state.auth_role = None
            st.session_state.auth_full_name = None
            st.sidebar.warning(
                f"Session expired after {SESSION_TIMEOUT_MINUTES} minutes of inactivity. "
                "Please log in again."
            )
            return None, None, None

        touch_session()
        st.sidebar.success(st.session_state.auth_full_name)
        st.sidebar.markdown(
            f'<span class="role-badge">{ROLE_LABELS.get(st.session_state.auth_role, "")}</span>',
            unsafe_allow_html=True,
        )

        with st.sidebar.expander("Change my password"):
            with st.form("change_password_form"):
                new_pwd = st.text_input("New password", type="password", key="new_pwd")
                confirm_pwd = st.text_input("Confirm new password", type="password",
                                             key="confirm_pwd")
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
                            log_audit(conn, "USERS", "PASSWORD_CHANGED",
                                      st.session_state.auth_username)
                            st.sidebar.success("Password updated.")

        if st.sidebar.button("Log out"):
            log_audit(conn, "USERS", "LOGOUT", st.session_state.auth_username)
            st.session_state.auth_username = None
            st.session_state.auth_role = None
            st.session_state.auth_full_name = None
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
            if not check_password(password_input, record["password_hash"]):
                st.sidebar.error("Incorrect username or password.")
                log_audit(conn, "USERS", "LOGIN_FAILED", username_input)
                return None, None, None

            st.session_state.auth_username = username_input
            st.session_state.auth_role = record["role"]
            st.session_state.auth_full_name = record["full_name"]
            touch_session()
            log_audit(conn, "USERS", "LOGIN_SUCCESS", username_input)
            st.rerun()

    st.sidebar.markdown(
        '<div class="sidebar-login-hint">Please enter your username and password '
        'to continue.</div>', unsafe_allow_html=True)
    return None, None, None
