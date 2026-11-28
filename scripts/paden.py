# -*- coding: utf-8 -*-
"""Waar de drie apps en de ToetsCirkel-gegevens op deze Mac staan.

Standaard staan de apps in ~/Documents (zoals nu). Met omgevingsvariabelen kun je dat overschrijven:
TOETSCONSTRUCTIE_MAP, TOETSEVALUATIE_MAP, OEFENTOOL_MAP en TOETSCIRKEL_DATA.
"""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path.home()
DOCS = HOME / "Documents"


def _map(var: str, standaard: Path) -> Path:
    return Path(os.environ.get(var) or standaard).expanduser()


TOETSCONSTRUCTIE = _map("TOETSCONSTRUCTIE_MAP", DOCS / "Toetsconstructie")
TOETSEVALUATIE = _map("TOETSEVALUATIE_MAP", DOCS / "Toetsevaluatie")
OEFENTOOL = _map("OEFENTOOL_MAP", DOCS / "Oefentool")
DATA = _map("TOETSCIRKEL_DATA", DOCS / "ToetsCirkel_data")   # lokaal, nooit in Git

APPS = {"Toetsconstructie": TOETSCONSTRUCTIE, "Toetsevaluatie": TOETSEVALUATIE, "Oefentool": OEFENTOOL}

DATABASES = {
    "toetsevaluatie.db": TOETSEVALUATIE / "toetsevaluatie.db",
    "oefentool.db": OEFENTOOL / "oefentool.db",
}

# Kleine bestanden waarin het systeem leert: altijd mee in de back-up.
LEERBESTANDEN = {
    "Toetsconstructie/kennisbank": TOETSCONSTRUCTIE / "kennisbank",
    "Toetsconstructie/instellingen.json": TOETSCONSTRUCTIE / "instellingen.json",
    "Toetsevaluatie/docent_leerregels.json": TOETSEVALUATIE / "docent_leerregels.json",
    "Oefentool/kwaliteitsregels.json": OEFENTOOL / "kwaliteitsregels.json",
}

# Fase 3: de map waar de scanner (via OneDrive) de klassenscans neerzet.
SCANS = _map("TOETSCIRKEL_SCANS", HOME / "Library" / "CloudStorage" / "OneDrive-DunamareOnderwijsgroep" / "ToetsCirkel Scans")
