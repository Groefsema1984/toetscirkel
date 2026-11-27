# -*- coding: utf-8 -*-
"""Opslag van een lopende analyse (sessie) en van de leerregels van de docent.

Een sessie is één JSON-bestand per toets in App_data/sessies/<toets_id>/sessie.json. Alles wat de
AI vindt staat hier, niet in de database: pas bij 'Definitief maken' gaan de scores naar
toetsevaluatie.db.
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from config import LEERREGELS, SESSIES

slot = threading.RLock()
_cache: dict[str, dict] = {}


# ---------------------------------------------------------------------------- sessies
def geldig_id(toets_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,80}", toets_id or ""))


def map_van(toets_id: str) -> Path:
    if not geldig_id(toets_id):
        raise ValueError(f"Ongeldige toetsnaam: {toets_id!r}")
    return SESSIES / toets_id


def _bestand(toets_id: str) -> Path:
    return map_van(toets_id) / "sessie.json"


def _laad(toets_id: str) -> dict | None:
    """Geeft het levende object uit het geheugen (alleen gebruiken binnen 'slot')."""
    if toets_id not in _cache:
        pad = _bestand(toets_id)
        if not pad.exists():
            return None
        _cache[toets_id] = json.loads(pad.read_text(encoding="utf-8"))
    return _cache[toets_id]


def _bewaar(s: dict) -> None:
    pad = _bestand(s["toets_id"])
    pad.parent.mkdir(parents=True, exist_ok=True)
    s["gewijzigd"] = datetime.now().isoformat(timespec="seconds")
    tijdelijk = pad.with_suffix(".tmp")
    tijdelijk.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tijdelijk, pad)


def lees(toets_id: str) -> dict | None:
    """Een losse kopie om te tonen; wijzigingen daarin worden niet bewaard."""
    if not geldig_id(toets_id):
        return None
    with slot:
        s = _laad(toets_id)
        return copy.deepcopy(s) if s else None


@contextmanager
def bewerk(toets_id: str):
    """with bewerk(id) as s: ...  -> wijzigingen worden bij het verlaten opgeslagen."""
    with slot:
        s = _laad(toets_id)
        if s is None:
            raise KeyError(f"Geen analyse gevonden voor {toets_id}")
        yield s
        _bewaar(s)


def nieuw(toets_id: str, gegevens: dict) -> dict:
    with slot:
        s = {
            "toets_id": toets_id,
            "aangemaakt": datetime.now().isoformat(timespec="seconds"),
            "status": "bezig",          # bezig | fout | review | publiceren | gepubliceerd
            "voortgang": {"stap": 0, "tekst": "Wachten op start", "gedaan": 0, "totaal": 0},
            "fout": "",
            "matrijs": [], "paginas": [], "index_cache": {}, "leerlingen": {}, "wezen": [],
            "klassenlijst": {}, "lijst_precies": False, "afwezig": [],
            "feedforward": {}, "klasadvies": None,
        }
        s.update(gegevens)
        _cache[toets_id] = s
        _bewaar(s)
        return copy.deepcopy(s)


def verwijder(toets_id: str) -> None:
    """Gooit een sessie weg (bij opnieuw analyseren van dezelfde toets)."""
    import shutil
    with slot:
        _cache.pop(toets_id, None)
        shutil.rmtree(map_van(toets_id), ignore_errors=True)


def alle() -> list[dict]:
    """Korte samenvatting van elke sessie, nieuwste eerst."""
    uit = []
    if not SESSIES.is_dir():
        return uit
    for pad in SESSIES.iterdir():
        if not (pad / "sessie.json").exists() or not geldig_id(pad.name):
            continue
        s = lees(pad.name)
        if s:
            uit.append({"toets_id": s["toets_id"], "status": s["status"], "vak": s.get("vak", ""),
                        "klas": s.get("klas", ""), "datum": s.get("datum", ""),
                        "gewijzigd": s.get("gewijzigd", ""), "aantal": len(s["leerlingen"]),
                        "fout": s.get("fout", "")})
    return sorted(uit, key=lambda x: x["gewijzigd"], reverse=True)


# ---------------------------------------------------------------------------- leerregels
def laad_leerregels() -> list[dict]:
    with slot:
        if not LEERREGELS.exists():
            return []
        try:
            data = json.loads(LEERREGELS.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []


def _schrijf_leerregels(regels: list[dict]) -> None:
    tijdelijk = LEERREGELS.with_suffix(".tmp")
    tijdelijk.write_text(json.dumps(regels, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tijdelijk, LEERREGELS)


def voeg_leerregel_toe(regel: dict) -> None:
    with slot:
        regels = laad_leerregels()
        regel = dict(regel, id=(max((r.get("id", 0) for r in regels), default=0) + 1),
                     datum=datetime.now().strftime("%Y-%m-%d"))
        regels.append(regel)
        _schrijf_leerregels(regels)


def verwijder_leerregel(regel_id: int) -> None:
    with slot:
        _schrijf_leerregels([r for r in laad_leerregels() if r.get("id") != regel_id])
