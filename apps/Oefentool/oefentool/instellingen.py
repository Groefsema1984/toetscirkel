# -*- coding: utf-8 -*-
"""Paden en vaste waarden van de oefentool.

De oefentool staat in ~/Documents/Oefentool en werkt samen met twee andere apps:
- Toetsevaluatie (~/Documents/Toetsevaluatie): leerprofielen, leerdoelenregister, klassenlijsten.
  De oefentool leest daar alleen; resultaten van oefentoetsen komen er via de gewone scan in.
- Toetsconstructie (~/Documents/Toetsconstructie): de bouwmotor voor Word-bestanden in CE-stijl
  en de kennisbank (richtlijnen, rekenregels, leerregels van de docent).
Leerlingdata blijven lokaal op de Mac. Naar Google Drive gaat alleen lesmateriaal en goedgekeurde
oefenopgaven, zonder leerlingnummers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

PAKKET = Path(__file__).resolve().parent
BASIS = Path(os.environ.get("OEFENTOOL_MAP") or PAKKET.parent).expanduser()
HOME = Path.home()
TOETSEVALUATIE = Path(os.environ.get("TOETSEVALUATIE_MAP") or HOME / "Documents" / "Toetsevaluatie").expanduser()
TOETSCONSTRUCTIE = Path(os.environ.get("TOETSCONSTRUCTIE_MAP")
                        or HOME / "Documents" / "Toetsconstructie").expanduser()

DB = BASIS / "oefentool.db"
OPDRACHTEN = BASIS / "Opdrachten"            # per opdracht: plan, sets, uitvoer (met leerlingnummers)
EIGEN_BRONNEN = BASIS / "Bronnen"            # bestanden die je in de app sleept (Cumulus, PowerPoints)
KWALITEITSREGELS = BASIS / "kwaliteitsregels.json"
INSTELLINGEN = BASIS / "instellingen.json"
LOG = BASIS / "oefentool.log"

POORT = int(os.environ.get("OEFENTOOL_POORT", "5056"))
MODEL = os.environ.get("OEFENTOOL_MODEL", "claude-sonnet-5-5")          # opgaven maken en herzien
ZUINIG_MODEL = "claude-haiku-5-5"                                         # ongeveer 20x goedkoper
MOTOREN = {"abonnement": "Abonnement; API-tegoed alleen na mijn toestemming (aanbevolen)",
           "api": "Altijd API-tegoed (betaald)"}
MODELLEN = {"claude-haiku-5-5": "Haiku 5.5 (zuinig)", "claude-sonnet-5-5": "Sonnet 5.5 (sterker, duurder)"}
# Prijzen in dollar per miljoen tokens (platform.claude.com, oktober 2026): invoer, cache schrijven (5 min),
# cache lezen, uitvoer. Haiku: prijs voor verzoeken tot 100.000 tokens. Batch: de helft.
PRIJZEN = {"claude-sonnet-5-5": (2.00, 2.50, 0.10, 10.00), "claude-haiku-5-5": (0.10, 0.125, 0.01, 0.50)}


def kosten(model: str, invoer: int, schrijf: int, lees: int, uit: int, batch: bool = False) -> float:
    p = PRIJZEN.get(model, PRIJZEN["claude-sonnet-5-5"])
    k = (invoer * p[0] + schrijf * p[1] + lees * p[2] + uit * p[3]) / 1_000_000
    return k / 2 if batch else k
WERKERS = int(os.environ.get("OEFENTOOL_WERKERS", "3"))

BEHEERST, BIJNA = 0.8, 0.5                   # gelijk aan de Toetsevaluatie
TAALNIVEAU = {"mavo": "B1", "havo": "B1/B2", "vwo": "B2/C1"}
BLOOM = ["Onthouden", "Begrijpen", "Toepassen", "Analyseren", "Evalueren", "Creëren"]
LEERGATEN = {
    "KD": "Kennis: begrip, formule of feit nog niet paraat",
    "PR": "Rekenfout: aanpak goed, uitrekening fout",
    "RF": "Redeneerfout: stap in de uitleg ontbreekt of klopt niet",
    "LI": "Leesfout: vraag of bron niet goed gelezen",
    "DW": "Doorwerkfout: verder met een eerder fout antwoord",
    "MC": "Misconceptie: begrip verkeerd begrepen",
}
# Hoe Claude oefent op een leergat: dit gaat mee in de opdracht voor het genereren.
AANPAK_LEERGAT = {
    "KD": "eerst een korte kennisvraag (begrip of formule), daarna dezelfde stof toegepast",
    "PR": "berekeningen met tussenstappen en een schatting vooraf; laat de leerling de uitkomst controleren",
    "RF": "redeneerketens: geef de eerste schakel en laat de leerling de volgende schakels aanvullen, "
          "daarna een volledige uitlegvraag",
    "LI": "contexten met een bron of tabel waarin de leerling eerst de gevraagde gegevens moet selecteren; "
          "vraagwoorden die precies lezen belonen",
    "DW": "vervolgvragen die op elkaar voortbouwen, met een controlemoment na elke deelvraag",
    "MC": "een vraag die de misvatting zichtbaar maakt (twee begrippen naast elkaar, kies en verklaar)",
}
VORMEN = {"papier": "Papier (Word in toetsstijl)", "digitaal": "Digitaal (oefenpagina met directe feedback)"}
DOELEN = {
    "leerling": "Per leerling: eigen set op de eigen zwakke leerdoelen",
    "groep": "Per groep: leerlingen met dezelfde zwakke leerdoelen samen",
    "klas": "Per klas: één set voor iedereen op de leerdoelen die de klas het minst beheerst",
    "toets": "Per toets: nabespreking en herkansing van één toets",
    "examentraining": "Examentraining: alle leerdoelen van het jaar, op examenniveau",
    "onderhoud": "Onderhoud: korte herhaalset op leerdoelen die al een paar weken beheerst zijn, zodat de stof blijft hangen",
}

# Bronmappen in Google Drive: automatisch gezocht (privé-account, Drive voor desktop).
DRIVE_KANDIDATEN = ["Mijn Drive", "My Drive"]


def drive_map() -> Path | None:
    wolk = HOME / "Library" / "CloudStorage"
    if wolk.is_dir():
        for m in sorted(wolk.glob("GoogleDrive-*")):
            for naam in DRIVE_KANDIDATEN:
                if (m / naam).is_dir():
                    return m / naam
    extra = os.environ.get("OEFENTOOL_DRIVE")
    return Path(extra) if extra and Path(extra).is_dir() else None


def laad_instellingen() -> dict:
    drive = drive_map()
    standaard = {
        "bronmappen": [str(drive / "NotebookLM Onderwijs")] if drive else [],
        "bibliotheek_drive": str(drive / "Oefentool_Bibliotheek") if drive else "",
        "max_tekens_per_bron": 120_000,
        "model_index": os.environ.get("OEFENTOOL_MODEL_INDEX", ZUINIG_MODEL),     # bibliotheek lezen
        "model_kritiek": os.environ.get("OEFENTOOL_MODEL_KRITIEK", ZUINIG_MODEL),  # zelfkritiek
        "motor": "abonnement",               # abonnement (API alleen na toestemming) of api
        "abonnement_model": "sonnet",        # sonnet of opus (Claude Code)
    }
    try:
        eigen = json.loads(INSTELLINGEN.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        eigen = {}
    uit = dict(standaard, **{k: v for k, v in eigen.items() if v not in (None, "")})
    if str(EIGEN_BRONNEN) not in uit["bronmappen"]:
        uit["bronmappen"] = uit["bronmappen"] + [str(EIGEN_BRONNEN)]
    return uit


def bewaar_instellingen(d: dict) -> None:
    INSTELLINGEN.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


for _m in (OPDRACHTEN, EIGEN_BRONNEN):
    _m.mkdir(parents=True, exist_ok=True)
