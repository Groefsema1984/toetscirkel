# -*- coding: utf-8 -*-
"""Instellingen van de webapp. Alle paden hangen aan de map Toetsevaluatie (de map boven webapp/)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_MAP = Path(__file__).resolve().parent
BASIS = Path(os.environ.get("TOETSEVALUATIE_MAP") or APP_MAP.parent).expanduser()
os.environ["TOETSEVALUATIE_MAP"] = str(BASIS)
if str(BASIS) not in sys.path:
    sys.path.insert(0, str(BASIS))

import toets_evaluator as kern  # noqa: E402  (bestaande motor: klassenlijst, PDF, API, database)

DATA = BASIS / "App_data"
SESSIES = DATA / "sessies"                      # tussenresultaten per toets (nog niet in de database)
LEERREGELS = BASIS / "docent_leerregels.json"   # jouw redenen om van de AI af te wijken

POORT = int(os.environ.get("TOETS_POORT", "5055"))
WERKERS = int(os.environ.get("TOETS_WERKERS", "2"))   # aantal leerlingen dat tegelijk wordt nagekeken
BEELD_ZIJDE_PX = 1500                            # pagina-afbeeldingen voor het scherm en het namen lezen
MAX_LEERREGELS_IN_PROMPT = 40

# Norm voor de toetslengte in minuten per scorepunt (mavo: geen norm opgegeven).
NORM_MIN_PER_PUNT = {"havo": 3.2, "vwo": 3.1}

# Keuzes op het uploadscherm: (waarde in de toetsnaam, omschrijving, standaard tweefasen)
KLASSEN = [
    ("mavo3", "3 mavo", False), ("mavo4", "4 mavo", False),
    ("4H", "4 havo", False), ("4V", "4 vwo", True),
    ("5H", "5 havo", False), ("5V", "5 vwo", True),
    ("6V", "6 vwo", False),
]
VAKKEN = ["Economie", "Bedrijfseconomie"]

# Soort toets in de toetsnaam: _SE_ (schoolexamen of andere toets) of _OE_ (oefentoets uit de oefentool).
SOORTEN = [("SE", "Schoolexamen of toets"), ("OE", "Oefentoets (oefentool)")]


def soort_van(toets_id: str) -> str:
    """'oefen' voor een oefentoets (toetsnaam met _OE_), anders 'toets'."""
    import re
    return "oefen" if re.search(r"(^|_)OE(_|$)", toets_id or "") else "toets"

BLOOM = ["Onthouden", "Begrijpen", "Toepassen", "Analyseren", "Evalueren", "Creëren"]
BLOOM_ALIAS = {
    "onthouden": "Onthouden", "herinneren": "Onthouden", "kennis": "Onthouden", "reproductie": "Onthouden",
    "begrijpen": "Begrijpen", "inzicht": "Begrijpen", "begrip": "Begrijpen",
    "toepassen": "Toepassen", "toepassing": "Toepassen",
    "analyseren": "Analyseren", "analyse": "Analyseren",
    "evalueren": "Evalueren", "evaluatie": "Evalueren", "beoordelen": "Evalueren",
    "creëren": "Creëren", "creeren": "Creëren", "scheppen": "Creëren", "synthese": "Creëren",
}

# Leergaten in leerlingtaal (vaste tekst; de AI maakt er bij publicatie een persoonlijke versie van).
LEERGAT_LEERLING = {
    "KD": ("Kennis", "Je wist een begrip, formule of feit nog niet goed genoeg.",
           "Maak een begrippenlijst van dit onderdeel en laat je overhoren tot je alles zonder spieken weet."),
    "PR": ("Rekenen", "Je aanpak was goed, maar in de berekening ging iets mis.",
           "Schrijf elke tussenstap op en controleer je uitkomst met een schatting: kan dit getal kloppen?"),
    "RF": ("Redeneren", "In je uitleg ontbrak een stap of de conclusie klopte niet.",
           "Oefen met redeneerketens: schrijf elke stap op met 'daardoor' en sla geen stap over."),
    "LI": ("Lezen", "Je hebt de vraag of de bron niet goed gelezen.",
           "Onderstreep het vraagwoord en de gegevens voordat je begint en lees na afloop de vraag nog eens."),
    "DW": ("Doorwerken", "Je rekende of redeneerde verder met een eerder fout antwoord.",
           "Controleer een uitkomst voordat je die in een volgende vraag gebruikt."),
    "MC": ("Begrip", "Je hebt een begrip anders begrepen dan het bedoeld is.",
           "Zoek het begrip op, schrijf het verschil met wat jij dacht op en maak er twee oefenvragen over."),
}
