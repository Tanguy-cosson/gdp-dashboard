"""business_logic.py — Calculs métier purs (aucun accès Streamlit/DB ici,
ce qui les rend testables unitairement avec pytest sans mocker st.*)."""
import pandas as pd

from constants import CRITICAL_FLAG, NORMAL_FLAG, OOR_FLAG


def compute_oor_flag(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute une colonne 'Alerte'. Une valeur CRITIQUE (panic value)
    prime toujours sur une simple alerte hors-norme."""
    df = df.copy()

    def flag(row):
        value = row.get("result_value")
        if pd.isna(value):
            return ""
        crit_low, crit_high = row.get("critical_low"), row.get("critical_high")
        if pd.notna(crit_low) and value < crit_low:
            return CRITICAL_FLAG
        if pd.notna(crit_high) and value > crit_high:
            return CRITICAL_FLAG
        ref_low, ref_high = row.get("ref_low"), row.get("ref_high")
        if pd.isna(ref_low) or pd.isna(ref_high):
            return ""
        if value < ref_low or value > ref_high:
            return OOR_FLAG
        return NORMAL_FLAG

    df["Alerte"] = df.apply(flag, axis=1)
    return df


def build_patients_matrix(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["usubjid", "site_name", "VINC", "V1", "V2"])
    pivot = df.pivot_table(
        index=["usubjid", "site_name"], columns="visit_code",
        values="result_id", aggfunc="count", fill_value=0,
    ).reset_index()
    for col in ["VINC", "V1", "V2"]:
        if col not in pivot.columns:
            pivot[col] = 0
        pivot[col] = pivot[col].apply(lambda x: "✅" if x > 0 else "—")
    return pivot[["usubjid", "site_name", "VINC", "V1", "V2"]]


def compute_tat_hours(df: pd.DataFrame):
    """TAT moyen (heures) : (réception -> validation technique) et
    (validation technique -> validation biologique). Renvoie
    (None, None) si aucune donnée exploitable (champs optionnels)."""
    d = df.copy()
    for col in ["receipt_datetime", "technical_validated_at", "biologist_validated_at"]:
        if col not in d.columns:
            return None, None

    d["_receipt"] = pd.to_datetime(d["receipt_datetime"], errors="coerce", utc=True).dt.tz_localize(None)
    d["_tech"] = pd.to_datetime(d["technical_validated_at"], errors="coerce", utc=True).dt.tz_localize(None)
    d["_bio"] = pd.to_datetime(d["biologist_validated_at"], errors="coerce", utc=True).dt.tz_localize(None)

    tat1 = d.dropna(subset=["_receipt", "_tech"])
    tat1_hours = ((tat1["_tech"] - tat1["_receipt"]).dt.total_seconds() / 3600).mean() if not tat1.empty else None

    tat2 = d.dropna(subset=["_tech", "_bio"])
    tat2_hours = ((tat2["_bio"] - tat2["_tech"]).dt.total_seconds() / 3600).mean() if not tat2.empty else None

    return tat1_hours, tat2_hours


def validate_ingestion_dataframe(df: pd.DataFrame):
    """Contrôle de qualité AVANT écriture en base. Renvoie une liste
    d'erreurs lisibles (une par ligne défaillante) au lieu de laisser
    le CSV planter l'import au milieu (import partiel = pire scénario
    en essai clinique). Retourne (is_valid, errors: list[str])."""
    required_cols = ["site_id", "patient_id", "usubjid", "subjid", "sex", "birth_year",
                      "visit_code", "visit_date", "visit_num", "test_code", "test_name",
                      "result_value", "result_unit", "result_date"]
    errors = []

    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        errors.append(f"Colonnes manquantes dans le CSV : {', '.join(missing_cols)}")
        return False, errors

    for idx, row in df.iterrows():
        line_no = idx + 2  # +1 header, +1 index 0-based
        for col in required_cols:
            if pd.isna(row[col]) or str(row[col]).strip() == "":
                errors.append(f"Ligne {line_no} : champ obligatoire '{col}' vide.")
        if pd.notna(row.get("sex")) and row["sex"] not in ("M", "F"):
            errors.append(f"Ligne {line_no} : sex='{row['sex']}' invalide (attendu M ou F).")
        if pd.notna(row.get("visit_code")) and row["visit_code"] not in ("VINC", "V1", "V2"):
            errors.append(f"Ligne {line_no} : visit_code='{row['visit_code']}' invalide "
                           f"(attendu VINC, V1 ou V2).")
        try:
            float(row["result_value"])
        except (ValueError, TypeError):
            errors.append(f"Ligne {line_no} : result_value='{row.get('result_value')}' "
                           f"n'est pas un nombre.")
        try:
            int(row["birth_year"])
        except (ValueError, TypeError):
            errors.append(f"Ligne {line_no} : birth_year='{row.get('birth_year')}' invalide.")

    return (len(errors) == 0), errors
