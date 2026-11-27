#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
toets_evaluator.py
==================
Bewaakt ~/Documents/Toetsevaluatie/Schoolexamens_Inbox en verwerkt per toetsmap:

  1. correctievoorschrift        -> .docx (tekst + tabellen) of .pdf met tekstlaag; de naam
                                    bevat 'correctie'. Opgaven en bijlage mogen er ook bij.
  2. toetsmatrijs                -> uit het correctievoorschrift gehaald (Claude) en opgeslagen
  3. klassenscan (één PDF)       -> per leerling opgeknipt en via de klassenlijst gekoppeld
                                    aan het leerlingnummer (naam op het werk -> nummer)
     elke leerling-PDF           -> pagina's als afbeelding (PyMuPDF) naar Claude (vision)
  4. resultaten                  -> toetsevaluatie.db (leerlingen, toetsen, toetsmatrijs,
                                    evaluatieresultaten)
  5. feedbackstrook              -> <toetsmap>/Feedback/<leerling_id>_feedback.md
  6. controlelijst voor docent   -> <toetsmap>/Feedback/_controle_docent.md

Afspraken
---------
* Naam van de toetsmap = toets_id, bijv. AT5.11_SE_5V_Economie_tweefasen.
  Uit de naam worden vak (Economie / Bedrijfseconomie), niveau (mavo/havo/vwo) en klas gelezen.
* Klassenscan: zet één PDF met het werk van de hele klas in de toetsmap (elke naam mag,
  bijv. scan.pdf). Het werk van een leerling staat achter elkaar en op het eerste blad
  (liefst op elk blad) staat de naam of het leerlingnummer.
* Klassenlijst (nodig voor een klassenscan): een .csv of .xlsx met leerlingnummer en naam,
  als _klassenlijst.csv/.xlsx in de toetsmap, of voor al je klassen in
  ~/Documents/Toetsevaluatie/Klassenlijsten/. Een kolom 'klas' wordt ook overgenomen.
* Het script knipt de scan op in <leerlingnummer>.pdf in de toetsmap. Wat niet te koppelen is
  komt in Niet_gekoppeld/; hernoem zo'n PDF naar het leerlingnummer en zet hem in de toetsmap.
  De oorspronkelijke scan verhuist naar Klassenscans_verwerkt/.
* Losse PDF per leerling kan ook: de bestandsnaam (zonder .pdf) is dan het leerling_id.
* Geen leerlingwerk: bestanden die met een punt of underscore beginnen, en PDF's met
  correctie, opgaven of bijlage in de naam.

Starten
-------
    export ANTHROPIC_API_KEY="sk-ant-..."
    python3 toets_evaluator.py              # bewaken (stoppen met Ctrl+C)
    python3 toets_evaluator.py --eenmalig   # alles in de inbox verwerken en stoppen
    python3 toets_evaluator.py --opnieuw    # ook al verwerkte leerlingen/matrijzen opnieuw doen
"""

from __future__ import annotations

import argparse
import base64
import csv
import difflib
import io
import json
import logging
import os
import re
import sqlite3
import shutil
import statistics
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

try:
    import anthropic
    try:
        import pymupdf as fitz  # PyMuPDF
    except ImportError:
        import fitz
    from docx import Document
    from docx.table import Table
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError as fout:  # pragma: no cover
    sys.exit(
        f"Ontbrekende library: {fout.name}\n"
        "Installeer met:  pip install anthropic watchdog python-docx pymupdf openpyxl"
    )

# --------------------------------------------------------------------------------------
# Instellingen
# --------------------------------------------------------------------------------------
BASIS = Path(os.environ.get("TOETSEVALUATIE_MAP", "~/Documents/Toetsevaluatie")).expanduser()
DB_PAD = BASIS / "toetsevaluatie.db"
INBOX = BASIS / "Schoolexamens_Inbox"
LOGBESTAND = BASIS / "toets_evaluator.log"
KLASSENLIJST_MAP = BASIS / "Klassenlijsten"

# Claude 3.5 Sonnet bestaat niet meer (uitgefaseerd op 28-10-2025); de opvolger is Sonnet 5.5.
MODEL = os.environ.get("TOETS_MODEL", "claude-sonnet-5-5")
MAX_ZIJDE_PX = int(os.environ.get("TOETS_MAX_ZIJDE_PX", "2000"))  # langste zijde per pagina
MAX_PAGINAS = 40            # per leerling; meer pagina's = waarschijnlijk een klassenscan
INDEX_ZIJDE_PX = int(os.environ.get("TOETS_INDEX_ZIJDE_PX", "1400"))  # voor het opknippen
INDEX_BATCH = 20            # pagina's per verzoek bij het opknippen van een klassenscan
MAX_AFBEELDING_BYTES = 21_000_000  # totaal per verzoek (API-grens is 32 MB inclusief base64)
WACHTTIJD = 6               # seconden rust in een map voordat de verwerking start
MAX_POGINGEN = 3            # per PDF, bij API-fouten
HERKANS_NA = 300            # seconden tot een nieuwe poging

LEERGATEN = {
    "KD": "Kennis: je had de stof nog niet paraat",
    "PR": "Rekenfout: de aanpak klopt, de uitrekening niet",
    "RF": "Redeneerfout: een stap in je uitleg klopt niet of ontbreekt",
    "LI": "Leesfout: de vraag of de bron niet goed gelezen",
    "DW": "Doorwerkfout: verder gewerkt met een eerder fout antwoord",
    "MC": "Misconceptie: je hebt een begrip verkeerd begrepen",
}
TAALNIVEAU = {"mavo": "B1", "havo": "B1/B2", "vwo": "B2/C1"}

log = logging.getLogger("toets_evaluator")


# --------------------------------------------------------------------------------------
# Toetsmap -> gegevens over de toets
# --------------------------------------------------------------------------------------
@dataclass
class ToetsInfo:
    toets_id: str
    vak: str          # "Economie" of "Bedrijfseconomie"
    niveau: str       # "mavo", "havo" of "vwo"
    klas: str         # bijv. "5H", "6V", "mavo4"
    tweefasen: bool

    @property
    def taalniveau(self) -> str:
        return TAALNIVEAU[self.niveau]


def lees_toetsmap(naam: str) -> ToetsInfo:
    """Leidt vak, niveau en klas af uit de mapnaam (bijv. AT5.11_SE_5H_Bedrijfseconomie)."""
    klein = naam.lower()
    if "bedrijfseconomie" in klein or re.search(r"(^|[_\-\s])(beco|be)($|[_\-\s])", klein):
        vak = "Bedrijfseconomie"
    elif "economie" in klein or re.search(r"(^|[_\-\s])(eco|ec)($|[_\-\s])", klein):
        vak = "Economie"
    else:
        raise ValueError(
            f"Geen vak gevonden in mapnaam '{naam}'. Zet 'Economie' of 'Bedrijfseconomie' in de naam."
        )

    tweefasen = "tweefasen" in klein
    niveau, klas = None, ""
    for deel in re.split(r"[_\-\s]+", naam):
        d = deel.lower()
        if "mavo" in d or "vmbo" in d or re.fullmatch(r"\d(m|tl|gt)|(m|tl|gt)\d", d):
            niveau, klas = "mavo", deel
        elif "vwo" in d or re.fullmatch(r"\dv|v\d|\d(ath|gym)", d):
            niveau, klas = "vwo", deel
        elif "havo" in d or re.fullmatch(r"\dh|h\d", d):
            niveau, klas = "havo", deel
        if niveau:
            break
    if tweefasen and niveau != "vwo":
        niveau, klas = "vwo", klas or "5V"
    if not niveau:
        raise ValueError(
            f"Geen niveau gevonden in mapnaam '{naam}'. Gebruik bijv. mavo4, 5H, 5V of 6V."
        )
    return ToetsInfo(toets_id=naam, vak=vak, niveau=niveau, klas=klas, tweefasen=tweefasen)


def schooljaar(dag: date) -> str:
    start = dag.year if dag.month >= 8 else dag.year - 1
    return f"{start}-{start + 1}"


# --------------------------------------------------------------------------------------
# Correctievoorschrift (.docx) -> tekst
# --------------------------------------------------------------------------------------
def _tabel_naar_tekst(tabel: Table) -> str:
    regels = []
    for rij in tabel.rows:
        cellen, vorige = [], None
        try:
            rij_cellen = rij.cells
        except Exception:  # beschadigde of vreemd samengevoegde rij
            continue
        for cel in rij_cellen:
            if cel._tc is vorige:      # samengevoegde cel: maar één keer opnemen
                continue
            vorige = cel._tc
            cellen.append(" / ".join(t.strip() for t in cel.text.splitlines() if t.strip()))
        if any(cellen):
            regels.append(" | ".join(cellen))
    return "[TABEL]\n" + "\n".join(regels) + "\n[EINDE TABEL]"


def lees_correctievoorschrift(pad: Path) -> str:
    """Leest alinea's en tabellen in de volgorde van het document."""
    doc = Document(str(pad))
    delen = []
    for blok in doc.iter_inner_content():
        if isinstance(blok, Table):
            delen.append(_tabel_naar_tekst(blok))
        else:
            tekst = blok.text.strip()
            if tekst:
                delen.append(tekst)
    return "\n".join(delen)


# Bestanden van de toets zelf; een PDF met zo'n woord in de naam is geen leerlingwerk.
TOETSBESTAND = r"correctie|antwoordmodel|normering|opgaven|bijlage"


def _zoek_document(map_: Path, patroon: str, niet: str = "") -> Path | None:
    """Zoekt een .docx of .pdf op naam; een .docx gaat voor (tabellen blijven dan beter heel)."""
    kandidaten = sorted(
        (p for p in map_.iterdir()
         if p.is_file() and p.suffix.lower() in (".docx", ".pdf")
         and not p.name.startswith(("~$", "."))
         and re.search(patroon, p.name.lower())
         and not (niet and re.search(niet, p.name.lower()))),
        key=lambda p: (p.suffix.lower() != ".docx", p.name))
    if len(kandidaten) > 1 and kandidaten[0].stem != kandidaten[1].stem:
        log.warning("%s: meerdere bestanden passen bij '%s', ik gebruik %s",
                    map_.name, patroon, kandidaten[0].name)
    return kandidaten[0] if kandidaten else None


def zoek_correctievoorschrift(map_: Path) -> Path | None:
    return _zoek_document(map_, r"correctie|antwoordmodel|normering")


def lees_document(pad: Path) -> str:
    """Tekst uit een .docx (met tabellen) of uit een PDF met tekstlaag."""
    if pad.suffix.lower() == ".pdf":
        with fitz.open(str(pad)) as pdf:
            return "\n".join(pagina.get_text() for pagina in pdf).strip()
    return lees_correctievoorschrift(pad)


def lees_toetsbronnen(map_: Path) -> str:
    """Opgaven en bijlage (als ze in de map staan) als extra context bij het nakijken."""
    delen = []
    for titel, patroon, niet in (("OPGAVEN", r"opgaven", r"correctie"),
                                 ("BIJLAGE", r"bijlage", r"uitwerk|correctie|opgaven")):
        pad = _zoek_document(map_, patroon, niet)
        if pad:
            try:
                tekst = lees_document(pad)
            except Exception as fout:
                log.warning("%s: %s niet gelezen: %s", map_.name, pad.name, fout)
                continue
            if len(tekst) > 200:
                delen.append(f"{titel} ({pad.name})\n{tekst}")
    return "\n\n".join(delen)


def zoek_leerling_pdfs(map_: Path) -> list[Path]:
    return sorted(
        p for p in map_.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith((".", "_"))
        and not re.search(TOETSBESTAND, p.name.lower())
    )


# --------------------------------------------------------------------------------------
# PDF -> afbeeldingen
# --------------------------------------------------------------------------------------
def pdf_naar_afbeeldingen(pad: Path) -> list[bytes]:
    """Zet elke pagina om naar JPEG (in kleur: de pen van de docent moet herkenbaar blijven)."""
    with fitz.open(str(pad)) as pdf:
        if pdf.page_count == 0:
            raise ValueError("PDF bevat geen pagina's")
        if pdf.page_count > MAX_PAGINAS:
            raise ValueError(
                f"PDF heeft {pdf.page_count} pagina's (maximum per leerling: {MAX_PAGINAS}). "
                "Is dit een klassenscan? Zet dan een klassenlijst in de toetsmap."
            )
        for kwaliteit in (85, 70, 55, 40):
            beelden = []
            for pagina in pdf:
                langste = max(pagina.rect.width, pagina.rect.height)
                zoom = min(MAX_ZIJDE_PX / langste, 220 / 72)  # nooit boven 220 dpi
                pix = pagina.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                beelden.append(pix.tobytes("jpeg", jpg_quality=kwaliteit))
            if sum(len(b) for b in beelden) <= MAX_AFBEELDING_BYTES:
                return beelden
        raise ValueError("Scan is te groot voor één verzoek; scan op een lagere resolutie.")


# --------------------------------------------------------------------------------------
# Anthropic API
# --------------------------------------------------------------------------------------
class ApiFout(RuntimeError):
    pass


def vraag_json(client, systeem: list[dict], inhoud: list[dict], schema: dict,
               max_tokens: int) -> dict:
    """Eén verzoek met gestructureerde uitvoer (JSON volgens schema)."""
    with client.messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        system=systeem,
        messages=[{"role": "user", "content": inhoud}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    ) as stroom:
        antwoord = stroom.get_final_message()

    g = antwoord.usage
    log.info("   tokens: invoer %s (cache gelezen %s, cache geschreven %s), uitvoer %s",
             g.input_tokens, getattr(g, "cache_read_input_tokens", 0) or 0,
             getattr(g, "cache_creation_input_tokens", 0) or 0, g.output_tokens)

    if antwoord.stop_reason == "max_tokens":
        raise ApiFout("Antwoord afgekapt (max_tokens bereikt)")
    if antwoord.stop_reason == "refusal":
        raise ApiFout("Het model heeft het verzoek geweigerd")
    tekst = "".join(b.text for b in antwoord.content if b.type == "text")
    try:
        return json.loads(tekst)
    except json.JSONDecodeError as fout:
        raise ApiFout(f"Geen geldige JSON ontvangen: {fout}") from fout


SCHEMA_MATRIJS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "opmerking"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["vraagnummer", "domein_eindterm", "tweefasen_label",
                             "bloom_niveau", "max_score"],
                "properties": {
                    "vraagnummer": {"type": "integer"},
                    "domein_eindterm": {"type": "string"},
                    "tweefasen_label": {"type": "string", "enum": ["H+V", "V", "GEEN"]},
                    "bloom_niveau": {"type": "string"},
                    "max_score": {"type": "integer"},
                },
            },
        },
        "opmerking": {"type": "string"},
    },
}

SCHEMA_EVALUATIE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["vragen", "reflectie", "tips", "opmerking_docent"],
    "properties": {
        "vragen": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["vraagnummer", "antwoord_leerling", "score_docent", "score_ai",
                             "type_leergat", "foutomschrijving", "toelichting", "zekerheid"],
                "properties": {
                    "vraagnummer": {"type": "integer"},
                    "antwoord_leerling": {"type": "string"},
                    "score_docent": {"type": ["number", "null"]},
                    "score_ai": {"type": "number"},
                    "type_leergat": {"type": "string",
                                     "enum": ["GEEN", "KD", "PR", "RF", "LI", "DW", "MC"]},
                    "foutomschrijving": {"type": "string"},
                    "toelichting": {"type": "string"},
                    "zekerheid": {"type": "string", "enum": ["hoog", "middel", "laag"]},
                },
            },
        },
        "reflectie": {"type": "string"},
        "tips": {"type": "array", "items": {"type": "string"}},
        "opmerking_docent": {"type": "string"},
    },
}


def vakregels(vak: str) -> str:
    if vak == "Bedrijfseconomie":
        reken = (
            "- Rekenfouten (bedrijfseconomie): in principe GEEN aftrek voor een rekenfout. "
            "Alleen als de leerling kennelijk niet heeft geschat (de uitkomst is zichtbaar "
            "onmogelijk of van een totaal verkeerde orde van grootte en de leerling merkt dat "
            "niet op): 1 scorepunt aftrek, met een maximum van 2 scorepunten per opgave "
            "(een opgave = alle vragen bij dezelfde context/casus)."
        )
    else:
        reken = (
            "- Rekenfouten (economie): maximaal 1 scorepunt aftrek per vraag voor een rekenfout, "
            "ook als er in die vraag meerdere rekenfouten staan."
        )
    return (
        reken + "\n"
        "- Een los eindantwoord zonder berekening bij een vraag waar een berekening wordt "
        "gevraagd levert 0 scorepunten op, ook als het eindantwoord juist is.\n"
        "- Ken alleen hele scorepunten toe, volgens de deelscores van het correctievoorschrift, "
        "en nooit meer dan de maximumscore van de vraag."
    )


def systeemprompt(info: ToetsInfo, cv_tekst: str, matrijs: list[dict],
                  bronnen: str = "") -> list[dict]:
    regels = f"""Je bent tweede corrector bij een schoolexamen {info.vak} ({info.niveau}, klas {info.klas}) \
in het Nederlandse voortgezet onderwijs. Je krijgt het correctievoorschrift met toetsmatrijs en \
daarna de ingescande, handgeschreven uitwerking van één leerling. De docent kan het werk al hebben \
nagekeken: zijn scores en aantekeningen staan dan met de hand bij de vragen (vaak in een andere \
kleur, in de kantlijn of omcirkeld).

WERKWIJZE PER VRAAG UIT DE TOETSMATRIJS
1. antwoord_leerling: geef in maximaal 30 woorden weer wat de leerling heeft opgeschreven \
(kern van de berekening of redenering). Schrijf "niet gemaakt" als de vraag ontbreekt en \
"onleesbaar" als je het niet kunt lezen. Verzin niets.
2. score_docent: de score die de docent met de hand bij deze vraag heeft genoteerd. null als er \
geen docentscore bij de vraag staat. Raad niet: bij twijfel over het cijfer kies je de meest \
waarschijnlijke lezing en zet je zekerheid op "laag".
3. score_ai: jouw eigen score volgens het correctievoorschrift en de vakregels hieronder.
4. type_leergat: als de leerling niet de maximumscore haalt, label het puntenverlies met precies \
één code (de belangrijkste oorzaak). GEEN bij de maximumscore.
   KD = Kennisdeficit: begrip, formule of feit niet paraat / vraag niet gemaakt.
   PR = Procedurele rekenfout: juiste aanpak, fout in de uitrekening, afronding of eenheid.
   RF = Redeneerfout: oorzaak-gevolgketen onjuist of onvolledig, verkeerde conclusie.
   LI = Leesfout: vraag, gegeven of bron verkeerd gelezen of iets anders beantwoord dan gevraagd.
   DW = Doorwerkfout: punten verloren doordat met een eerder fout antwoord is doorgewerkt.
   MC = Misconceptie: hardnekkig verkeerd beeld van een begrip (bijv. verwisselen van twee begrippen).
5. foutomschrijving: maximaal 25 woorden, concreet en begrijpelijk voor de leerling, over wat er \
misging. Lege tekst bij de maximumscore.
6. toelichting: maximaal 25 woorden voor de docent, alleen als score_ai afwijkt van score_docent \
of als je twijfelt. Anders lege tekst.
7. zekerheid: hoe zeker je bent van lezing én score (hoog, middel of laag).

Baseer type_leergat en foutomschrijving op de docentscore als die er staat, anders op je eigen score.
Neem elke vraag uit de toetsmatrijs precies één keer op, in volgorde van vraagnummer.

VAKREGELS
{vakregels(info.vak)}

FEEDBACK VOOR DE LEERLING
- reflectie: 3 tot 5 zinnen, gericht aan de leerling ("je"), op taalniveau {info.taalniveau} \
van het ERK. Benoem eerst wat goed ging, daarna het belangrijkste patroon in de fouten. \
Noem geen cijfer en geen percentages.
- tips: 2 of 3 concrete, uitvoerbare oefentips die passen bij de gevonden leergaten, zelfde taalniveau.
- opmerking_docent: korte melding voor de docent bij bijzonderheden (ontbrekende pagina's, \
slechte scan, werk van een andere toets, pagina's die van een andere leerling lijken te zijn: \
andere naam of duidelijk ander handschrift). Anders lege tekst.

Schrijf alles in het Nederlands. Noem nergens de naam van de leerling."""

    matrijs_tekst = "\n".join(
        f"vraag {m['vraagnummer']}: max {m['max_score']} punten | domein/eindterm: "
        f"{m['domein_eindterm']} | Bloom: {m['bloom_niveau']}"
        + (f" | tweefasen: {m['tweefasen_label']}" if m.get("tweefasen_label") else "")
        for m in matrijs
    )
    context = (
        "TOETSMATRIJS (leidend voor vraagnummers en maximumscores)\n" + matrijs_tekst
        + "\n\nCORRECTIEVOORSCHRIFT\n" + cv_tekst
        + ("\n\n" + bronnen if bronnen else "")
    )
    return [
        {"type": "text", "text": regels},
        # Het correctievoorschrift is voor elke leerling gelijk: cachen scheelt kosten en tijd.
        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
    ]


def haal_toetsmatrijs(client, info: ToetsInfo, cv_tekst: str) -> list[dict]:
    systeem = [{
        "type": "text",
        "text": (
            "Je haalt de toetsmatrijs uit een correctievoorschrift van een Nederlands schoolexamen. "
            "Neem uitsluitend over wat in het document staat; verzin niets."
        ),
    }]
    opdracht = f"""Hieronder staat het correctievoorschrift van toets {info.toets_id} ({info.vak}).

Geef per vraag:
- vraagnummer (geheel getal)
- domein_eindterm: domein en/of eindterm zoals in de toetsmatrijs vermeld (bijv. "D Markt" of \
"F2 Kostencalculatie"). Lege tekst als het er niet staat.
- tweefasen_label: "H+V" als de vraag voor havo en vwo telt, "V" als de vraag alleen voor vwo \
telt, "GEEN" als de matrijs geen tweefasen-labels gebruikt.
- bloom_niveau: het Bloom-niveau zoals in de matrijs vermeld (bijv. "Toepassen"). Lege tekst als \
het er niet staat.
- max_score: maximumscore van de vraag. Bij verschil tussen matrijs en uitwerkingen is de \
maximumscore bij de uitwerking leidend; meld het verschil in 'opmerking'.

Neem elke vraag precies één keer op. Zet in 'opmerking' kort wat onduidelijk of tegenstrijdig is, \
anders lege tekst.

<correctievoorschrift>
{cv_tekst}
</correctievoorschrift>"""
    uit = vraag_json(client, systeem, [{"type": "text", "text": opdracht}],
                     SCHEMA_MATRIJS, max_tokens=16000)
    items, gezien = [], set()
    for it in uit.get("items", []):
        nr, mx = int(it["vraagnummer"]), int(it["max_score"])
        if nr in gezien or mx <= 0:
            log.warning("%s: vraag %s overgeslagen in matrijs (dubbel of max_score %s)",
                        info.toets_id, nr, mx)
            continue
        gezien.add(nr)
        label = it["tweefasen_label"] if it["tweefasen_label"] in ("H+V", "V") else None
        items.append({
            "vraagnummer": nr,
            "domein_eindterm": it["domein_eindterm"].strip() or "Onbekend",
            "tweefasen_label": label,
            "bloom_niveau": it["bloom_niveau"].strip() or None,
            "max_score": mx,
        })
    if not items:
        raise ApiFout("Geen toetsmatrijs gevonden in het correctievoorschrift")
    if uit.get("opmerking"):
        log.warning("%s: opmerking bij toetsmatrijs: %s", info.toets_id, uit["opmerking"])
    return sorted(items, key=lambda m: m["vraagnummer"])


def evalueer_leerling(client, systeem: list[dict], beelden: list[bytes]) -> dict:
    inhoud: list[dict] = []
    for nr, beeld in enumerate(beelden, 1):
        inhoud.append({"type": "text", "text": f"Pagina {nr} van {len(beelden)}:"})
        inhoud.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.standard_b64encode(beeld).decode("ascii")},
        })
    inhoud.append({
        "type": "text",
        "text": "Dit is het volledige ingescande werk van één leerling. Beoordeel alle vragen "
                "uit de toetsmatrijs volgens de werkwijze.",
    })
    return vraag_json(client, systeem, inhoud, SCHEMA_EVALUATIE, max_tokens=32000)


# --------------------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------------------
def open_db() -> sqlite3.Connection:
    if not DB_PAD.exists():
        sys.exit(f"Database niet gevonden: {DB_PAD}")
    con = sqlite3.connect(str(DB_PAD), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    aanwezig = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ontbreekt = {"leerlingen", "toetsen", "toetsmatrijs", "evaluatieresultaten"} - aanwezig
    if ontbreekt:
        sys.exit(f"Tabellen ontbreken in {DB_PAD.name}: {', '.join(sorted(ontbreekt))}")
    return con


def db_toets(con, info: ToetsInfo) -> None:
    con.execute("INSERT OR IGNORE INTO toetsen (toets_id, vak, datum) VALUES (?, ?, ?)",
                (info.toets_id, info.vak, date.today().isoformat()))
    con.commit()


def db_lees_matrijs(con, toets_id: str) -> list[dict]:
    rijen = con.execute(
        "SELECT item_id, vraagnummer, domein_eindterm, tweefasen_label, bloom_niveau, max_score "
        "FROM toetsmatrijs WHERE toets_id = ? ORDER BY vraagnummer", (toets_id,)).fetchall()
    return [dict(r) for r in rijen]


def db_schrijf_matrijs(con, toets_id: str, items: list[dict]) -> None:
    """Voegt toe of werkt bij per vraagnummer, zodat bestaande item_id's (en resultaten) blijven."""
    bestaand = {m["vraagnummer"]: m["item_id"] for m in db_lees_matrijs(con, toets_id)}
    with con:
        for m in items:
            waarden = (m["domein_eindterm"], m["tweefasen_label"], m["bloom_niveau"], m["max_score"])
            if m["vraagnummer"] in bestaand:
                con.execute(
                    "UPDATE toetsmatrijs SET domein_eindterm=?, tweefasen_label=?, bloom_niveau=?, "
                    "max_score=? WHERE item_id=?", waarden + (bestaand[m["vraagnummer"]],))
            else:
                con.execute(
                    "INSERT INTO toetsmatrijs (toets_id, vraagnummer, domein_eindterm, "
                    "tweefasen_label, bloom_niveau, max_score) VALUES (?, ?, ?, ?, ?, ?)",
                    (toets_id, m["vraagnummer"]) + waarden)
        vervallen = set(bestaand) - {m["vraagnummer"] for m in items}
        if vervallen:
            log.warning("%s: vraag %s staat nog in de database maar niet meer in het "
                        "correctievoorschrift (niet verwijderd)",
                        toets_id, ", ".join(map(str, sorted(vervallen))))


def db_heeft_resultaten(con, leerling_id: str, toets_id: str) -> bool:
    return con.execute(
        "SELECT 1 FROM evaluatieresultaten e JOIN toetsmatrijs m ON m.item_id = e.item_id "
        "WHERE e.leerling_id = ? AND m.toets_id = ? LIMIT 1", (leerling_id, toets_id)
    ).fetchone() is not None


def db_schrijf_resultaten(con, info: ToetsInfo, leerling_id: str, regels: list[dict],
                          klas: str = "") -> None:
    """Eén rij per vraag (ook bij de volle score), anders klopt de view cijferoverzicht niet."""
    with con:
        con.execute(
            "INSERT INTO leerlingen (leerling_id, klas, niveau, cohort) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(leerling_id) DO UPDATE SET klas=excluded.klas, niveau=excluded.niveau, "
            "cohort=excluded.cohort",
            (leerling_id, klas or info.klas, info.niveau, schooljaar(date.today())))
        con.execute(
            "DELETE FROM evaluatieresultaten WHERE leerling_id = ? AND item_id IN "
            "(SELECT item_id FROM toetsmatrijs WHERE toets_id = ?)", (leerling_id, info.toets_id))
        con.executemany(
            "INSERT INTO evaluatieresultaten (leerling_id, item_id, behaalde_score, type_leergat, "
            "foutomschrijving) VALUES (?, ?, ?, ?, ?)",
            [(leerling_id, r["item_id"], r["score"], r["leergat"], r["omschrijving"])
             for r in regels])


# --------------------------------------------------------------------------------------
# Resultaat samenstellen, feedbackstrook en controlelijst
# --------------------------------------------------------------------------------------
def stel_resultaat_samen(matrijs: list[dict], evaluatie: dict) -> tuple[list[dict], list[str]]:
    """Koppelt het AI-antwoord aan de matrijs. Docentscore is leidend als die er staat."""
    per_vraag = {}
    for v in evaluatie.get("vragen", []):
        per_vraag.setdefault(int(v["vraagnummer"]), v)

    regels, controle = [], []
    for m in matrijs:
        nr, mx = m["vraagnummer"], m["max_score"]
        v = per_vraag.get(nr)
        if v is None:
            regels.append({**m, "score": 0.0, "leergat": None, "bron": "ontbreekt",
                           "omschrijving": "Niet beoordeeld door AI: handmatig invullen"})
            controle.append(f"Vraag {nr}: niet beoordeeld door de AI, score staat op 0. "
                            "**Handmatig invullen.**")
            continue

        docent, ai = v["score_docent"], float(v["score_ai"])
        ruwe = float(docent) if docent is not None else ai
        score = round(min(max(ruwe, 0.0), float(mx)), 2)
        bron = "docent" if docent is not None else "AI"
        if score != ruwe:
            controle.append(f"Vraag {nr}: gelezen score {ruwe:g} valt buiten 0-{mx}, "
                            f"opgeslagen als {score:g}.")
        if docent is not None and abs(float(docent) - ai) >= 0.5:
            controle.append(f"Vraag {nr}: docent {float(docent):g}, AI {ai:g} (max {mx}). "
                            f"{v['toelichting']}".strip())
        if v["zekerheid"] == "laag":
            controle.append(f"Vraag {nr}: lage zekerheid (score {bron}: {score:g} van {mx}). "
                            f"{v['toelichting']}".strip())

        if score >= mx:
            leergat, omschrijving = None, None
        else:
            leergat = v["type_leergat"] if v["type_leergat"] in LEERGATEN else None
            omschrijving = v["foutomschrijving"].strip() or None
            if leergat is None:
                controle.append(f"Vraag {nr}: puntenverlies zonder leergat-label.")
        regels.append({**m, "score": score, "leergat": leergat, "omschrijving": omschrijving,
                       "bron": bron})

    onbekend = sorted(set(per_vraag) - {m["vraagnummer"] for m in matrijs})
    if onbekend:
        controle.append("AI noemt vraagnummers die niet in de matrijs staan: "
                        + ", ".join(map(str, onbekend)) + " (genegeerd).")
    if evaluatie.get("opmerking_docent", "").strip():
        controle.append("Opmerking: " + evaluatie["opmerking_docent"].strip())
    return regels, controle


def _pct(behaald: float, maximaal: float) -> str:
    return f"{round(100 * behaald / maximaal):d}%" if maximaal else "-"


def schrijf_feedback(map_: Path, info: ToetsInfo, leerling_id: str, regels: list[dict],
                     evaluatie: dict, naam: str = "", klas: str = "") -> Path:
    totaal = sum(r["score"] for r in regels)
    maximaal = sum(r["max_score"] for r in regels)

    domeinen: dict[str, list[float]] = {}
    for r in regels:
        d = domeinen.setdefault(r["domein_eindterm"] or "Onbekend", [0.0, 0.0])
        d[0] += r["score"]
        d[1] += r["max_score"]

    uit = [
        f"# Feedback {info.toets_id.replace('_', ' ')}",
        "",
        f"**Leerling:** {naam + ' (' + leerling_id + ')' if naam else leerling_id}  ",
        f"**Vak:** {info.vak} ({info.niveau}, {klas or info.klas})  ",
        f"**Datum:** {date.today().strftime('%d-%m-%Y')}",
        "",
        "## Jouw resultaat",
        "",
        f"Je hebt **{totaal:g} van de {maximaal:g} punten** gehaald ({_pct(totaal, maximaal)}).",
        "",
        "## Beheersing per domein",
        "",
        "| Domein | Punten | Beheersing |",
        "|---|---|---|",
    ]
    for naam, (b, mx) in domeinen.items():
        uit.append(f"| {naam} | {b:g} van {mx:g} | {_pct(b, mx)} |")

    if any(r["tweefasen_label"] for r in regels):
        uit += ["", "## Tweefasen-vwo", "", "| Onderdeel | Punten | Beheersing |", "|---|---|---|"]
        for label, naam in (("H+V", "Stof voor havo en vwo"), ("V", "Stof alleen voor vwo")):
            deel = [r for r in regels if r["tweefasen_label"] == label]
            if deel:
                b, mx = sum(r["score"] for r in deel), sum(r["max_score"] for r in deel)
                uit.append(f"| {naam} | {b:g} van {mx:g} | {_pct(b, mx)} |")

    verlies = [r for r in regels if r["score"] < r["max_score"]]
    if verlies:
        uit += ["", "## Waar verloor je punten?", "", "| Soort fout | Vragen | Punten |",
                "|---|---|---|"]
        for code, uitleg in LEERGATEN.items():
            groep = [r for r in verlies if r["leergat"] == code]
            if groep:
                punten = sum(r["max_score"] - r["score"] for r in groep)
                vragen = ", ".join(str(r["vraagnummer"]) for r in groep)
                uit.append(f"| {uitleg} | {vragen} | {punten:g} |")
        uit += ["", "## Per vraag", ""]
        for r in verlies:
            code = f", {r['leergat']}" if r["leergat"] else ""
            uit.append(f"- **Vraag {r['vraagnummer']}** ({r['score']:g} van {r['max_score']:g}"
                       f"{code}): {r['omschrijving'] or 'zie het nagekeken werk.'}")

    uit += ["", "## Reflectie", "", evaluatie.get("reflectie", "").strip() or "-"]
    tips = [t.strip() for t in evaluatie.get("tips", []) if t.strip()]
    if tips:
        uit += ["", "## Zo ga je verder", ""] + [f"{i}. {t}" for i, t in enumerate(tips, 1)]
    uit.append("")

    doelmap = map_ / "Feedback"
    doelmap.mkdir(exist_ok=True)
    pad = doelmap / f"{leerling_id}_feedback.md"
    pad.write_text("\n".join(uit), encoding="utf-8")
    return pad


def schrijf_controle(map_: Path, kop: str, punten: list[str]) -> None:
    if not punten:
        return
    doelmap = map_ / "Feedback"
    doelmap.mkdir(exist_ok=True)
    pad = doelmap / "_controle_docent.md"
    nieuw = not pad.exists()
    with pad.open("a", encoding="utf-8") as f:
        if nieuw:
            f.write(f"# Controlelijst docent - {map_.name}\n\n"
                    "Punten waar de AI afwijkt van jouw score, twijfelt of iets mist.\n")
        f.write(f"\n## {kop} ({datetime.now().strftime('%d-%m-%Y %H:%M')})\n\n")
        f.writelines(f"- {p}\n" for p in punten)


# --------------------------------------------------------------------------------------
# Klassenlijst (leerlingnummer <-> naam)
# --------------------------------------------------------------------------------------
@dataclass
class Leerling:
    leerling_id: str
    naam: str
    klas: str = ""
    groep: str = ""    # tabblad waar de leerling op stond, bijv. "5h.beco"


def _lees_tabelbestand(pad: Path) -> list[tuple[str, list[list[str]]]]:
    """Geeft per tabblad (naam, rijen). Een csv telt als één tabblad zonder naam."""
    if pad.suffix.lower() == ".xlsx":
        try:
            import openpyxl
        except ImportError:
            raise ValueError("voor een .xlsx-klassenlijst is openpyxl nodig: pip install openpyxl")
        werkboek = openpyxl.load_workbook(str(pad), read_only=True, data_only=True)
        tabellen = []
        for blad in werkboek.worksheets:
            rijen = [["" if c is None or re.fullmatch(r"#[A-Z0-9/]+[!?]?", str(c).strip())
                      else re.sub(r"\.0$", "", str(c)).strip() for c in rij]
                     for rij in blad.iter_rows(values_only=True)]
            naam = "" if re.fullmatch(r"(sheet|blad|tabelle)\s*\d*", blad.title.lower()) \
                else blad.title.strip()
            tabellen.append((naam, [r for r in rijen if any(r)]))
        werkboek.close()
        return tabellen
    else:
        ruw = pad.read_bytes()
        try:
            tekst = ruw.decode("utf-8-sig")
        except UnicodeDecodeError:
            tekst = ruw.decode("cp1252")
        eerste = next((r for r in tekst.splitlines() if r.strip()), "")
        scheiding = max(";\t,", key=eerste.count)
        rijen = [[c.strip() for c in rij] for rij in csv.reader(io.StringIO(tekst),
                                                              delimiter=scheiding)]
    return [("", [r for r in rijen if any(r)])]


def _is_nummer(cel: str) -> bool:
    return bool(re.fullmatch(r"\d{3,}", cel))


KOPWOORD = r"nummer|tamnr|llnr|leerlingnr|naam|klas|groep|tussenv|voorvoegsel"


def lees_klassenlijst(pad: Path) -> list[Leerling]:
    """Leest alle tabbladen. Meerdere klassen op één tabblad of één klas per tabblad kan allebei."""
    tabellen = _lees_tabelbestand(pad)
    uit, fouten = [], []
    for bladnaam, rijen in tabellen:
        try:
            uit += _leerlingen_uit_tabel(rijen, bladnaam)
        except ValueError as fout:
            fouten.append(f"{bladnaam or 'tabblad'}: {fout}")
    if not uit and fouten:
        raise ValueError("; ".join(fouten))
    return uit


def _leerlingen_uit_tabel(rijen: list[list[str]], bladnaam: str = "") -> list[Leerling]:
    """Herkent de kolommen aan de kopregel, of anders aan de inhoud."""
    if not rijen:
        return []
    # Titelregels boven de kopregel overslaan: de kopregel is de eerste rij met kolomnamen.
    for i, rij in enumerate(rijen[:15]):
        if sum(bool(re.search(KOPWOORD, c.lower())) for c in rij) >= 2 \
                and not any(_is_nummer(c) for c in rij):
            rijen = rijen[i:]
            break
    heeft_kop = not any(_is_nummer(c) for c in rijen[0])
    kop = [c.lower() for c in rijen[0]] if heeft_kop else []
    data = rijen[1:] if heeft_kop else rijen
    if not data:
        return []
    breedte = max(len(r) for r in data)
    data = [r + [""] * (breedte - len(r)) for r in data]
    kop += [""] * (breedte - len(kop))

    def zoek(patroon: str) -> list[int]:
        return [i for i, k in enumerate(kop) if re.search(patroon, k)]

    id_kol = (zoek(r"nummer|tamnr|llnr|leerlingnr|leerling.?id|^nr\.?$|^id$") or [None])[0]
    if id_kol is None:
        tel = [sum(_is_nummer(r[i]) for r in data) for i in range(breedte)]
        id_kol = tel.index(max(tel))
        if max(tel) < 0.4 * len(data):
            raise ValueError("geen kolom met leerlingnummers gevonden")

    klas_kol = (zoek(r"klas|groep") or [None])[0]
    if klas_kol is None and not heeft_kop:
        for i in range(breedte):
            if i != id_kol and all(re.fullmatch(r"[A-Za-z]{0,2}\d[A-Za-z0-9.]{0,3}", r[i])
                                   for r in data):
                klas_kol = i
                break

    voor = zoek(r"roepnaam") or zoek(r"voornaam")
    naam_kols = voor[:1] + zoek(r"tussenv|voorvoegsel")[:1] + zoek(r"achternaam")[:1]
    if not (voor or zoek(r"achternaam")):
        naam_kols = [i for i in zoek(r"naam") if i != id_kol]
    if not naam_kols:
        naam_kols = [i for i in range(breedte) if i not in (id_kol, klas_kol)
                     and sum(bool(re.search(r"[A-Za-zÀ-ÿ]{2,}", r[i])) for r in data)
                     >= 0.6 * len(data)]
    if not naam_kols:
        raise ValueError("geen kolom met namen gevonden")

    def naam_van(r: list[str]) -> str:
        naam = " ".join(r[i] for i in naam_kols if r[i]).strip()
        if "," in naam:                       # "Vries, Sanne de" -> "Sanne de Vries"
            achter, _, rest = naam.partition(",")
            naam = f"{rest.strip()} {achter.strip()}".strip()
        return naam

    # Eén leerling per rij, of (export met pasfoto's) het nummer op de ene rij en de naam op
    # de rij eronder.
    records: list[list[str]] = []
    for r in data:
        lid, naam = r[id_kol].strip(), naam_van(r)
        if lid:
            records.append([lid, naam, r[klas_kol].strip() if klas_kol is not None else ""])
        elif naam and records and not records[-1][1]:
            records[-1][1] = naam
    # Bij genummerde leerlingen: tussenkopjes en herhaalde kopregels (geen nummer) overslaan.
    if records and sum(_is_nummer(x[0]) for x in records) >= 0.6 * len(records):
        records = [x for x in records if _is_nummer(x[0])]
    return [Leerling(lid, naam, klas or bladnaam, bladnaam) for lid, naam, klas in records if naam]


def _groep_past(groep: str, info: ToetsInfo) -> bool:
    """Hoort een tabblad als '5h.beco' bij deze toets (zelfde leerjaar, niveau en vak)?"""
    g = groep.lower()
    m = re.search(r"(\d)\s*([mhv])", g) or re.search(r"([mhv])\s*(\d)", g)
    if not m:
        return False
    jaar, letter = sorted(m.groups())          # cijfer sorteert vóór letter
    toetsjaar = re.search(r"\d", info.klas)
    if letter != info.niveau[0] or (toetsjaar and jaar != toetsjaar.group()):
        return False
    is_beco = bool(re.search(r"beco|bedrijf|m&o", g))
    is_eco = bool(re.search(r"econ|eco", g)) and not is_beco
    if info.vak == "Bedrijfseconomie":
        return not is_eco
    return not is_beco


def laad_klassenlijst(map_: Path, info: ToetsInfo | None = None
                      ) -> tuple[dict[str, Leerling], bool]:
    """Geeft (leerlingen per nummer, lijst_is_precies_deze_groep).

    Volgorde: een klassenlijst in de toetsmap; anders de lijsten in Klassenlijsten/, en daaruit
    alleen het tabblad dat bij de toets past (bijv. '5h.beco') als dat er is."""
    def lijsten(waar: Path, alles: bool) -> list[Path]:
        if not waar.is_dir():
            return []
        # 'Klassenlijsten extra.xlsx' (leerlingen die in de webapp zijn toegevoegd) eerst: die wint bij een klaswissel
        return sorted((p for p in waar.iterdir()
                       if p.is_file() and p.suffix.lower() in (".csv", ".xlsx")
                       and not p.name.startswith(("~$", "."))
                       and (alles or re.search(r"klassenlijst|leerling|namen", p.name.lower()))),
                      key=lambda p: ("extra" not in p.name.lower(), p.name))

    lokaal = lijsten(map_, alles=False)
    alle: list[Leerling] = []
    for pad in lokaal or lijsten(KLASSENLIJST_MAP, alles=True):
        try:
            alle += lees_klassenlijst(pad)
        except Exception as fout:
            log.error("Klassenlijst %s niet gelezen: %s", pad.name, fout)
    passend = [] if lokaal or info is None else [l for l in alle if _groep_past(l.groep, info)]
    uit: dict[str, Leerling] = {}
    for l in passend or alle:
        uit.setdefault(l.leerling_id, l)
    return uit, bool(lokaal or passend)


# --------------------------------------------------------------------------------------
# Klassenscan -> één PDF per leerling
# --------------------------------------------------------------------------------------
SCHEMA_INDEX = {
    "type": "object",
    "additionalProperties": False,
    "required": ["paginas"],
    "properties": {
        "paginas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pagina", "leeg", "naam_gelezen", "leerling_id",
                             "begin_nieuw_werk", "zekerheid"],
                "properties": {
                    "pagina": {"type": "integer"},
                    "leeg": {"type": "boolean"},
                    "naam_gelezen": {"type": "string"},
                    "leerling_id": {"type": "string"},
                    "begin_nieuw_werk": {"type": "boolean"},
                    "zekerheid": {"type": "string", "enum": ["hoog", "middel", "laag"]},
                },
            },
        },
    },
}


def _index_systeem(klassenlijst: dict[str, Leerling]) -> list[dict]:
    regels = """Je krijgt pagina's uit één scan waarin het handgeschreven toetswerk van een hele \
klas achter elkaar staat. Jouw taak: per pagina vaststellen van welke leerling de pagina is, zodat \
de scan per leerling kan worden opgeknipt. Je beoordeelt het werk niet.

Geef voor elke gevraagde pagina:
- leeg: true als de pagina helemaal blanco is (bijvoorbeeld een lege achterkant). Een voorbedrukte \
pagina zonder handschrift is NIET leeg.
- naam_gelezen: de naam en/of het leerlingnummer dat de leerling zelf op deze pagina heeft \
geschreven (naamveld, kop of hoek van het blad), letterlijk zoals je het leest. Lege tekst als er \
geen naam of nummer van de leerling op staat. Namen in opgaven, bronnen of van de docent tellen niet.
- leerling_id: het leerlingnummer uit de klassenlijst dat bij naam_gelezen hoort. Gebruik de \
klassenlijst om een slordig geschreven naam te herkennen. Geef lege tekst als er geen naam staat, \
als de naam bij niemand past, of als hij bij meer dan één leerling past (bijvoorbeeld alleen een \
voornaam die twee keer voorkomt). Neem nooit het nummer van de vorige pagina over als er op deze \
pagina zelf geen naam of nummer staat.
- begin_nieuw_werk: true alleen als deze pagina duidelijk het eerste blad van het werk van een \
leerling is: een voorblad, een ingevuld naamveld bovenaan het eerste blad, of het handschrift \
wisselt duidelijk ten opzichte van de vorige pagina én het werk begint opnieuw bij de eerste vraag.
- zekerheid: hoe zeker je bent van de koppeling op deze pagina (hoog, middel of laag).

Neem elke gevraagde pagina precies één keer op, met het paginanummer dat erbij staat."""
    lijst = "KLASSENLIJST (leerlingnummer | naam)\n" + "\n".join(
        f"{l.leerling_id} | {l.naam}" for l in klassenlijst.values())
    return [{"type": "text", "text": regels},
            {"type": "text", "text": lijst, "cache_control": {"type": "ephemeral"}}]


def _render(pdf, index: int, max_zijde: int, kwaliteit: int) -> bytes:
    pagina = pdf.load_page(index)
    zoom = min(max_zijde / max(pagina.rect.width, pagina.rect.height), 220 / 72)
    pix = pagina.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("jpeg", jpg_quality=kwaliteit)


def _beeldblok(beeld: bytes) -> dict:
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.standard_b64encode(beeld).decode("ascii")}}


def _woorden(tekst: str) -> list[str]:
    kaal = unicodedata.normalize("NFKD", tekst).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", kaal).split()


def _naam_past(gelezen: str, leerling: Leerling) -> bool:
    """Grove controle achteraf: lijkt de gelezen naam op de naam uit de klassenlijst?"""
    g = _woorden(gelezen)
    if leerling.leerling_id.lower() in g:
        return True
    doel = [w for w in _woorden(leerling.naam) if len(w) >= 3]
    return any(difflib.SequenceMatcher(None, a, b).ratio() >= 0.75
               for a in g if len(a) >= 3 for b in doel)


def _reeksen(paginas: list[int]) -> str:
    """[0,1,2,5] -> '1-3, 6' (paginanummers vanaf 1)."""
    delen, start, vorige = [], None, None
    for p in sorted(paginas):
        if start is None:
            start = vorige = p
        elif p == vorige + 1:
            vorige = p
        else:
            delen.append((start, vorige))
            start = vorige = p
    if start is not None:
        delen.append((start, vorige))
    return ", ".join(f"{a + 1}" if a == b else f"{a + 1}-{b + 1}" for a, b in delen)


def _schrijf_deel(bron, paginas: list[int], doel: Path) -> None:
    nieuw = fitz.open()
    for p in paginas:
        nieuw.insert_pdf(bron, from_page=p, to_page=p)
    nieuw.save(str(doel))
    nieuw.close()


def indexeer_klassenscan(client, pdf, klassenlijst: dict[str, Leerling],
                         cache: dict[int, list[dict]]) -> list[dict]:
    """Vraagt per pagina van wie die is. 'cache' bewaart afgeronde reeksen bij een herkansing."""
    systeem = _index_systeem(klassenlijst)
    totaal = pdf.page_count
    for start in range(0, totaal, INDEX_BATCH):
        if start in cache:
            continue
        eind = min(start + INDEX_BATCH, totaal)
        log.info("   pagina %d-%d van %d: namen lezen ...", start + 1, eind, totaal)
        inhoud: list[dict] = []
        if start > 0:
            inhoud.append({"type": "text", "text":
                           f"Ter vergelijking van het handschrift: pagina {start} (de pagina vóór "
                           "deze reeks). Neem deze pagina NIET op in je antwoord."})
            inhoud.append(_beeldblok(_render(pdf, start - 1, INDEX_ZIJDE_PX, 70)))
        for i in range(start, eind):
            inhoud.append({"type": "text", "text": f"Pagina {i + 1}:"})
            inhoud.append(_beeldblok(_render(pdf, i, INDEX_ZIJDE_PX, 75)))
        inhoud.append({"type": "text",
                       "text": f"Geef de gegevens voor pagina {start + 1} tot en met {eind}."})
        uit = vraag_json(client, systeem, inhoud, SCHEMA_INDEX, max_tokens=8000)
        per = {int(r["pagina"]): r for r in uit.get("paginas", [])}
        cache[start] = [
            per.get(i + 1) or {"pagina": i + 1, "leeg": False, "naam_gelezen": "",
                               "leerling_id": "", "begin_nieuw_werk": False,
                               "zekerheid": "laag", "ontbreekt": True}
            for i in range(start, eind)]
    return [r for start in sorted(cache) for r in cache[start]]


def splits_klassenscan(client, map_: Path, scan: Path, klassenlijst: dict[str, Leerling],
                       lijst_lokaal: bool, cache: dict[int, list[dict]]) -> None:
    """Knipt de klassenscan op in <leerlingnummer>.pdf en schrijft een overzicht voor de docent."""
    controle: list[str] = []
    with fitz.open(str(scan)) as pdf:
        if pdf.page_count == 0:
            raise ValueError("PDF bevat geen pagina's")
        paginas = indexeer_klassenscan(client, pdf, klassenlijst, cache)

        groepen: dict[str, list[int]] = {}
        gelezen_naam: dict[str, str] = {}
        huidig, zonder_naam, leeg = None, 0, 0
        for i, p in enumerate(paginas):
            if p["leeg"]:
                leeg += 1
                continue
            lid = p["leerling_id"].strip()
            naam = p["naam_gelezen"].strip()
            if lid not in klassenlijst:
                lid = ""
            if lid:
                huidig = lid
                if naam and not _naam_past(naam, klassenlijst[lid]):
                    controle.append(f"Pagina {i + 1}: gelezen '{naam}', gekoppeld aan "
                                    f"{klassenlijst[lid].naam} ({lid}). **Controleer.**")
                elif p["zekerheid"] == "laag":
                    controle.append(f"Pagina {i + 1}: koppeling aan {klassenlijst[lid].naam} "
                                    f"({lid}) met lage zekerheid (gelezen: '{naam}').")
            elif naam:
                huidig = "onbekend_" + ("-".join(_woorden(naam)) or "naam")[:40]
                gelezen_naam[huidig] = naam
            elif p["begin_nieuw_werk"] or huidig is None:
                zonder_naam += 1
                huidig = f"onbekend_zonder-naam-{zonder_naam}"
            if p.get("ontbreekt"):
                controle.append(f"Pagina {i + 1}: niet beoordeeld door de AI; bij de vorige "
                                "leerling gevoegd.")
            groepen.setdefault(huidig, []).append(i)

        rest = map_ / "Niet_gekoppeld"
        overzicht, aantallen = [], []
        for sleutel, pags in groepen.items():
            if sleutel in klassenlijst:
                l = klassenlijst[sleutel]
                doel = map_ / f"{sleutel}.pdf"
                if doel.exists():
                    rest.mkdir(exist_ok=True)
                    doel = rest / f"{sleutel}_extra_p{pags[0] + 1:03d}.pdf"
                    controle.append(f"{l.naam} ({sleutel}): er bestond al een PDF; pagina "
                                    f"{_reeksen(pags)} staat in Niet_gekoppeld/{doel.name}.")
                else:
                    aantallen.append(len(pags))
                    overzicht.append((l, pags))
                    if "," in _reeksen(pags):
                        controle.append(f"{l.naam} ({sleutel}): pagina's staan niet achter "
                                        f"elkaar in de scan ({_reeksen(pags)}).")
            else:
                rest.mkdir(exist_ok=True)
                doel = rest / f"{sleutel}_p{pags[0] + 1:03d}-{pags[-1] + 1:03d}.pdf"
                wat = (f"naam gelezen als '{gelezen_naam[sleutel]}', niet in de klassenlijst"
                       if sleutel in gelezen_naam else "geen naam gevonden")
                controle.append(f"Pagina {_reeksen(pags)}: {wat}. Staat in Niet_gekoppeld/"
                                f"{doel.name}. **Hernoem naar het leerlingnummer en zet de PDF "
                                "in de toetsmap.**")
            _schrijf_deel(pdf, pags, doel)
        totaal_paginas = pdf.page_count

    if len(aantallen) >= 5:
        mediaan = statistics.median(aantallen)
        for l, pags in overzicht:
            if len(pags) >= 1.6 * mediaan and len(pags) - mediaan >= 3:
                controle.append(f"{l.naam} ({l.leerling_id}): {len(pags)} pagina's, de klas heeft "
                                f"er meestal {mediaan:g}. **Zit hier werk van een tweede "
                                "leerling bij?**")
            elif len(pags) <= 0.5 * mediaan:
                controle.append(f"{l.naam} ({l.leerling_id}): maar {len(pags)} pagina('s), de "
                                f"klas heeft er meestal {mediaan:g}. Ontbreekt er werk?")
    if lijst_lokaal:
        afwezig = [l for lid, l in klassenlijst.items()
                   if lid not in groepen and not (map_ / f"{lid}.pdf").exists()]
        if afwezig:
            controle.append("Geen werk gevonden van: "
                            + ", ".join(f"{l.naam} ({l.leerling_id})" for l in afwezig) + ".")

    gekoppeld = len(overzicht)
    los = sum(1 for s in groepen if s not in klassenlijst)
    kop = [f"{totaal_paginas} pagina's: {gekoppeld} leerling(en) gekoppeld, {los} deel/delen "
           f"niet gekoppeld, {leeg} lege pagina('s) weggelaten."]
    lijst = [f"{l.naam} ({l.leerling_id}): pagina {_reeksen(pags)} ({len(pags)})"
             for l, pags in overzicht]
    schrijf_controle(map_, f"Klassenscan {scan.name}", kop + controle + lijst)
    log.info("   %s", kop[0])

    klaar = map_ / "Klassenscans_verwerkt"
    klaar.mkdir(exist_ok=True)
    doel = klaar / scan.name
    if doel.exists():
        doel = klaar / f"{scan.stem}_{datetime.now():%Y%m%d_%H%M%S}{scan.suffix}"
    shutil.move(str(scan), str(doel))


# --------------------------------------------------------------------------------------
# Verwerking van één toetsmap
# --------------------------------------------------------------------------------------
class Verwerker:
    def __init__(self, client, opnieuw: bool = False):
        self.client = client
        self.opnieuw = opnieuw
        self.gedaan: set[Path] = set()           # PDF's die in deze run al zijn (her)verwerkt
        self.matrijs_ververst: set[str] = set()
        self.pogingen: dict[Path, int] = {}
        self.index_cache: dict[tuple, dict[int, list[dict]]] = {}
        self.gemeld: set[Path] = set()

    def _splits_klassenscans(self, map_: Path, pdfs: list[Path], info: ToetsInfo) -> bool:
        """Knipt klassenscans op. Geeft True terug als er later nog een poging nodig is."""
        klassenlijst, lokaal = laad_klassenlijst(map_, info)
        later = False
        for pdf in pdfs:
            if pdf.stem.strip() in klassenlijst or self.pogingen.get(pdf, 0) >= MAX_POGINGEN:
                continue                       # losse leerling-PDF, of al opgegeven
            if not klassenlijst:
                try:
                    with fitz.open(str(pdf)) as d:
                        groot = d.page_count > MAX_PAGINAS
                except Exception:
                    groot = False
                if groot and pdf not in self.gemeld:
                    self.gemeld.add(pdf)
                    log.error("%s: %s lijkt een klassenscan, maar er is geen klassenlijst. Zet "
                              "_klassenlijst.csv of .xlsx (leerlingnummer en naam) in de toetsmap.",
                              map_.name, pdf.name)
                continue
            groepen = sorted({l.groep for l in klassenlijst.values() if l.groep})
            log.info("%s: klassenscan %s opknippen (%d leerlingen in de klassenlijst%s)",
                     map_.name, pdf.name, len(klassenlijst),
                     ": " + ", ".join(groepen) if lokaal and groepen else "")
            sleutel = (pdf, pdf.stat().st_mtime_ns)
            try:
                splits_klassenscan(self.client, map_, pdf, klassenlijst, lokaal,
                                   self.index_cache.setdefault(sleutel, {}))
                self.index_cache.pop(sleutel, None)
            except (anthropic.APIError, ApiFout) as fout:
                self.pogingen[pdf] = self.pogingen.get(pdf, 0) + 1
                log.error("   API-fout bij opknippen (poging %d/%d): %s", self.pogingen[pdf],
                          MAX_POGINGEN, fout)
                later = later or self.pogingen[pdf] < MAX_POGINGEN
            except Exception as fout:
                self.pogingen[pdf] = MAX_POGINGEN
                log.exception("   %s niet opgeknipt: %s", pdf.name, fout)
        return later

    def verwerk_map(self, map_: Path) -> bool:
        """Geeft True terug als er later nog een poging nodig is."""
        if not map_.is_dir():
            return False
        try:
            info = lees_toetsmap(map_.name)
        except ValueError as fout:
            log.error("%s", fout)
            return False

        scan_later = self._splits_klassenscans(map_, zoek_leerling_pdfs(map_), info)
        klassenlijst, _ = laad_klassenlijst(map_, info)
        pdfs = [p for p in zoek_leerling_pdfs(map_)
                if not klassenlijst or p.stem.strip() in klassenlijst]
        cv_pad = zoek_correctievoorschrift(map_)
        if not cv_pad:
            if pdfs:
                log.info("%s: wacht op het correctievoorschrift, .docx of .pdf (%d PDF's klaar)",
                         map_.name, len(pdfs))
            return scan_later
        if not pdfs:
            log.info("%s: correctievoorschrift gevonden, nog geen leerling-PDF's", map_.name)
            return scan_later

        con = open_db()
        try:
            te_doen = [p for p in pdfs if p not in self.gedaan
                       and self.pogingen.get(p, 0) < MAX_POGINGEN
                       and (self.opnieuw or not db_heeft_resultaten(con, p.stem.strip(),
                                                                    info.toets_id))]
            if not te_doen:
                return scan_later

            log.info("%s: %s, %s, klas %s%s - %d leerling(en) te verwerken", map_.name, info.vak,
                     info.niveau, info.klas, ", tweefasen" if info.tweefasen else "", len(te_doen))
            cv_tekst = lees_document(cv_pad)
            if len(cv_tekst) < 300:
                log.error("%s: %s bevat nauwelijks tekst (%d tekens). Is het een gescande PDF? "
                          "Lever het correctievoorschrift dan als .docx of als PDF met tekst.",
                          map_.name, cv_pad.name, len(cv_tekst))
                return False

            db_toets(con, info)
            matrijs = db_lees_matrijs(con, info.toets_id)
            if not matrijs or (self.opnieuw and info.toets_id not in self.matrijs_ververst):
                log.info("%s: toetsmatrijs uit correctievoorschrift halen ...", map_.name)
                try:
                    items = haal_toetsmatrijs(self.client, info, cv_tekst)
                except (anthropic.APIError, ApiFout) as fout:
                    log.error("%s: toetsmatrijs mislukt: %s", map_.name, fout)
                    return True
                db_schrijf_matrijs(con, info.toets_id, items)
                self.matrijs_ververst.add(info.toets_id)
                matrijs = db_lees_matrijs(con, info.toets_id)
                totaal = sum(m["max_score"] for m in matrijs)
                log.info("%s: toetsmatrijs opgeslagen: %d vragen, %d punten",
                         map_.name, len(matrijs), totaal)
                schrijf_controle(map_, "Toetsmatrijs", [
                    f"{len(matrijs)} vragen, totaal {totaal} punten. Controleer dit tegen je "
                    "correctievoorschrift.",
                    "Vraag (max): " + ", ".join(f"{m['vraagnummer']} ({m['max_score']})"
                                                for m in matrijs),
                ])

            systeem = systeemprompt(info, cv_tekst, matrijs, lees_toetsbronnen(map_))
            later_opnieuw = scan_later
            for nr, pdf in enumerate(te_doen, 1):
                leerling_id = pdf.stem.strip()
                bekend = klassenlijst.get(leerling_id)
                naam, klas = (bekend.naam, bekend.klas) if bekend else ("", "")
                log.info("%s: leerling %s %s(%d/%d)", map_.name, leerling_id,
                         f"{naam} " if naam else "", nr, len(te_doen))
                try:
                    beelden = pdf_naar_afbeeldingen(pdf)
                    log.info("   %d pagina('s) naar de API ...", len(beelden))
                    evaluatie = evalueer_leerling(self.client, systeem, beelden)
                except (anthropic.APIError, ApiFout) as fout:
                    self.pogingen[pdf] = self.pogingen.get(pdf, 0) + 1
                    log.error("   API-fout (poging %d/%d): %s", self.pogingen[pdf],
                              MAX_POGINGEN, fout)
                    later_opnieuw = later_opnieuw or self.pogingen[pdf] < MAX_POGINGEN
                    continue
                except Exception as fout:  # kapotte PDF e.d.: niet opnieuw proberen
                    self.pogingen[pdf] = MAX_POGINGEN
                    log.error("   %s overgeslagen: %s", pdf.name, fout)
                    continue

                regels, controle = stel_resultaat_samen(matrijs, evaluatie)
                db_schrijf_resultaten(con, info, leerling_id, regels, klas)
                pad = schrijf_feedback(map_, info, leerling_id, regels, evaluatie, naam, klas)
                schrijf_controle(map_, f"Leerling {leerling_id}" + (f" - {naam}" if naam else ""),
                                 controle)
                self.gedaan.add(pdf)
                totaal = sum(r["score"] for r in regels)
                maximaal = sum(r["max_score"] for r in regels)
                log.info("   klaar: %g/%g punten, %d controlepunt(en) -> %s", totaal, maximaal,
                         len(controle), pad.name)
            return later_opnieuw
        finally:
            con.close()


# --------------------------------------------------------------------------------------
# Bewaking met watchdog
# --------------------------------------------------------------------------------------
class InboxHandler(FileSystemEventHandler):
    """Onthoudt per toetsmap wanneer er voor het laatst iets veranderde."""

    def __init__(self):
        self.wachtrij: dict[Path, float] = {}
        self.slot = threading.Lock()

    def plan(self, map_: Path, wanneer: float | None = None) -> None:
        with self.slot:
            self.wachtrij[map_] = wanneer if wanneer is not None else time.time() + WACHTTIJD

    def klaar(self) -> list[Path]:
        nu = time.time()
        with self.slot:
            rijp = [m for m, t in self.wachtrij.items() if t <= nu]
            for m in rijp:
                del self.wachtrij[m]
        return rijp

    def on_any_event(self, event):
        for ruw in (getattr(event, "src_path", None), getattr(event, "dest_path", None)):
            if not ruw:
                continue
            pad = Path(os.fsdecode(ruw))
            if pad.suffix.lower() not in (".pdf", ".docx", ".csv", ".xlsx") or pad.name.startswith(("~$", ".")):
                continue
            try:
                rel = pad.relative_to(INBOX)
            except ValueError:
                continue
            if len(rel.parts) == 2:           # <toetsmap>/<bestand>
                self.plan(INBOX / rel.parts[0])
            elif len(rel.parts) == 1:
                log.warning("%s staat los in de inbox; zet het bestand in een toetsmap.", pad.name)


def controleer_api(client) -> None:
    """Controleert bij het starten de sleutel, het model en de versie van de anthropic-library."""
    import inspect
    try:
        par = inspect.signature(client.messages.stream).parameters
        if "output_config" not in par and not any(p.kind == p.VAR_KEYWORD for p in par.values()):
            sys.exit("De anthropic-library is te oud. Voer uit:  pip install --upgrade anthropic")
    except (TypeError, ValueError):
        pass
    try:
        client.models.retrieve(MODEL)
    except anthropic.AuthenticationError:
        log.error("De API-sleutel is geweigerd. Controleer de sleutel op console.anthropic.com.")
        sys.exit(3)
    except anthropic.NotFoundError:
        sys.exit(f"Model '{MODEL}' bestaat niet (meer). Zet TOETS_MODEL op een bestaand model.")
    except Exception as fout:
        log.warning("Kon de API nu niet bereiken (%s); ik probeer het bij de eerste toets.", fout)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evalueert ingescande schoolexamens met Claude.")
    parser.add_argument("--eenmalig", action="store_true",
                        help="verwerk wat er nu in de inbox staat en stop")
    parser.add_argument("--opnieuw", action="store_true",
                        help="verwerk ook leerlingen en toetsmatrijzen die al in de database staan")
    args = parser.parse_args()

    BASIS.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOGBESTAND, encoding="utf-8")])
    for naam in ("httpx", "httpx2", "httpcore", "httpcore2", "anthropic"):
        logging.getLogger(naam).setLevel(logging.WARNING)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is niet ingesteld. Voer uit:\n"
                 '  export ANTHROPIC_API_KEY="sk-ant-..."')
    open_db().close()                     # controleert database en tabellen
    INBOX.mkdir(parents=True, exist_ok=True)

    client = anthropic.Anthropic(max_retries=4, timeout=900.0)
    controleer_api(client)
    verwerker = Verwerker(client, opnieuw=args.opnieuw)
    handler = InboxHandler()

    for map_ in sorted(p for p in INBOX.iterdir() if p.is_dir() and not p.name.startswith(".")):
        handler.plan(map_, wanneer=0)     # bestaande mappen direct meenemen

    def ronde() -> None:
        for map_ in handler.klaar():
            try:
                if verwerker.verwerk_map(map_):
                    if args.eenmalig:
                        log.info("%s: niet alles is gelukt; start het script opnieuw.", map_.name)
                    else:
                        handler.plan(map_, wanneer=time.time() + HERKANS_NA)
                        log.info("%s: nieuwe poging over %d minuten", map_.name,
                                 HERKANS_NA // 60)
            except Exception:
                log.exception("%s: onverwachte fout", map_.name)

    if args.eenmalig:
        ronde()
        return

    observer = Observer()
    observer.schedule(handler, str(INBOX), recursive=True)
    observer.start()
    log.info("Bewaak %s (model: %s). Stoppen met Ctrl+C.", INBOX, MODEL)
    try:
        while True:
            ronde()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Gestopt.")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
