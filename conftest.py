# Fichier vide, mais sa PRÉSENCE à la racine du projet suffit :
# pytest l'importe automatiquement et ajoute ce dossier à sys.path,
# ce qui permet à tests/test_*.py de faire "import business_logic"
# sans configuration supplémentaire.
import os
import sqlite3

import pytest

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


@pytest.fixture
def conn():
    """Base SQLite en mémoire, schéma complet appliqué, migrations
    jouées — utilisée par tous les tests qui touchent à la couche base
    de données (db.py, mailbox.py). Fermée automatiquement en fin de
    test, jamais partagée entre tests (isolation totale)."""
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON;")
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        connection.executescript(f.read())
    connection.commit()

    from migrations import run_migrations
    run_migrations(connection)

    yield connection
    connection.close()
