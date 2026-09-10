"""
seed_demo_data.py — Peuple la base avec des données de démonstration
réalistes, pour que le dashboard, les graphiques et le workflow soient
visibles dès l'ouverture par un visiteur (jury, recruteur, sponsor).

Usage :
    python seed_demo_data.py

Idempotent : relancer le script ne duplique rien (vérifie un marqueur
dans SETTINGS avant d'insérer). Pour re-générer un jeu de données
différent, supprimez d'abord blood_study.db (ou la clé
'demo_data_seeded' dans SETTINGS) puis relancez.

IMPORTANT : ce script est un outil de démonstration, pas un outil de
production. Ne JAMAIS le lancer sur une base contenant de vraies
données patients.
"""
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import db as dbmod
from auth import hash_password
from constants import CRITICAL_FLAG

random.seed(42)  # jeu de données reproductible d'une exécution à l'autre

SITES = [
    ("FR-001", "Hopital Saint-Louis", "FR"),
    ("FR-002", "CHU Rennes", "FR"),
    ("BE-001", "Cliniques Universitaires Saint-Luc", "BE"),
]

FIRST_NAMES_M = ["Lucas", "Hugo", "Louis", "Jules", "Adam", "Raphael", "Arthur", "Gabriel"]
FIRST_NAMES_F = ["Emma", "Jade", "Louise", "Alice", "Chloe", "Lina", "Rose", "Anna"]

TESTS = [
    # code, name, unit, ref_low, ref_high, crit_low, crit_high
    ("HBA1C", "Hemoglobin A1c", "%", 4.0, 6.0, None, 12.0),
    ("GLUC", "Fasting Glucose", "mmol/L", 3.9, 5.6, 2.2, 22.0),
    ("CREAT", "Creatinine", "umol/L", 60, 110, None, 400),
    ("CHOL", "Total Cholesterol", "mmol/L", 3.0, 5.2, None, None),
    ("TRIG", "Triglycerides", "mmol/L", 0.5, 1.7, None, None),
]


def _iso(dt):
    return dt.strftime("%Y-%m-%d")


def _iso_ts(dt):
    return dt.isoformat(timespec="seconds")


def already_seeded(conn):
    return dbmod.get_setting(conn, "demo_data_seeded") == "1"


def seed(conn):
    if already_seeded(conn):
        print("Demo data already present (SETTINGS.demo_data_seeded = 1). Nothing to do.")
        print("To re-seed, delete blood_study.db and re-run this script.")
        return

    print("Seeding demo data...")
    base_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

    for site_id, site_name, country in SITES:
        dbmod.get_or_create_site(conn, site_id, site_name, country)

    n_patients = 25
    for i in range(1, n_patients + 1):
        site_id, site_name, country = random.choice(SITES)
        sex = random.choice(["M", "F"])
        first_name = random.choice(FIRST_NAMES_M if sex == "M" else FIRST_NAMES_F)
        birth_year = random.randint(1945, 2000)
        patient_id = f"P-{i:04d}"
        usubjid = f"BLOOD-{site_id}-{i:03d}"
        subjid = f"{i:03d}"

        dbmod.get_or_create_patient(conn, patient_id, usubjid, site_id, subjid, sex, birth_year)

        visit_defs = [
            ("VINC", 1, base_date),
            ("V1", 2, base_date + timedelta(days=30)),
            ("V2", 3, base_date + timedelta(days=90)),
        ]
        # Certains patients n'ont pas encore leur dernière visite (cohorte en cours)
        n_visits = random.choices([1, 2, 3], weights=[1, 2, 6])[0]

        for visit_code, visit_num, visit_date in visit_defs[:n_visits]:
            visit_id = dbmod.get_or_create_visit(conn, patient_id, visit_code,
                                                   _iso(visit_date), visit_num)
            receipt_dt = visit_date + timedelta(hours=random.randint(2, 48))
            sample_id = dbmod.get_or_create_sample(
                conn, patient_id, visit_id, "SERUM",
                collection_datetime=_iso_ts(visit_date),
                receipt_datetime=_iso_ts(receipt_dt),
            )

            # ~15% de valeurs hors-norme, ~3% de critiques, pour que le
            # dashboard montre des cas réalistes plutôt que tout au vert
            for test_code, test_name, unit, ref_low, ref_high, crit_low, crit_high in TESTS:
                roll = random.random()
                if roll < 0.03 and crit_high:
                    value = round(crit_high * random.uniform(1.05, 1.3), 1)
                elif roll < 0.18:
                    value = round(ref_high * random.uniform(1.1, 1.4), 1)
                else:
                    value = round(random.uniform(ref_low, ref_high), 2)

                dbmod.insert_lab_result(
                    conn, visit_id, test_code, test_name, value, unit,
                    _iso(receipt_dt), ref_low, ref_high, sample_id, crit_low, crit_high,
                )

            # Fait avancer une partie des résultats dans le workflow de
            # validation, pour que Dashboard / Technical / Biological
            # Validation ne soient pas tous vides.
            cur = conn.execute(
                "SELECT result_id FROM LAB_RESULTS WHERE sample_id = ?", (sample_id,)
            )
            result_ids = [r[0] for r in cur.fetchall()]
            stage = random.choices(
                ["pending", "technical_ok", "reviewed"], weights=[2, 2, 6]
            )[0]
            if stage in ("technical_ok", "reviewed"):
                dbmod.mark_technical_validation(conn, result_ids, "lab_tech1")
            if stage == "reviewed":
                dbmod.mark_biological_validation(
                    conn, result_ids, "biologist1", "Reviewed and approved",
                    "Demo data — auto-validated by seed script."
                )

    dbmod.set_setting(conn, "demo_data_seeded", "1")
    print(f"Done: {n_patients} patients across {len(SITES)} sites seeded.")
    print("Log in with cro_arc / cro2026 (or your own account) and open Dashboard.")


if __name__ == "__main__":
    conn = sqlite3.connect(dbmod.DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    with open(dbmod.SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    from migrations import run_migrations
    run_migrations(conn)

    seed(conn)
