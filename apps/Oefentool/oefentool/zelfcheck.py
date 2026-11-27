# -*- coding: utf-8 -*-
"""Formatieve check: resultaatcodes van de digitale oefenpagina terug in de oefentool.

Een leerling kijkt thuis zelf na en stuurt aan het eind een resultaatcode (via Teams). Daarin staan
geen namen: alleen de opdracht, de persoonlijke code of groep (met het leerlingnummer dat de leerling
zelf invult), en per leerdoel de zelfscore. De docent plakt de codes hier; ze worden lokaal bewaard in
Opdrachten/<id>/zelfcheck.json. Ze tellen NIET als meting in de Toetsevaluatie: het is zelf nagekeken.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime

from .instellingen import OPDRACHTEN

PATROON = re.compile(r"OEF-([A-Za-z0-9_-]{20,})-(\d{4})")


def controlegetal(t: str) -> str:
    s = 0
    for ch in t:
        s = (s * 31 + ord(ch)) % 9973
    return f"{s:04d}"


def lees_code(b64: str, controle: str) -> dict:
    if controlegetal(b64) != controle:
        raise ValueError("de code is niet volledig gekopieerd")
    try:
        d = json.loads(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as fout:
        raise ValueError("de code is niet te lezen") from fout
    if d.get("v") != 1 or not isinstance(d.get("r"), list):
        raise ValueError("onbekend formaat")
    return d


def _pad(oid: str):
    return OPDRACHTEN / oid / "zelfcheck.json"


def laad(oid: str) -> dict:
    try:
        return json.loads(_pad(oid).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def verwerk(o: dict, tekst: str) -> dict:
    """Leest alle resultaatcodes uit geplakte tekst. Geeft aantallen en meldingen."""
    gevonden = PATROON.findall(tekst or "")
    data = laad(o["id"])
    per_code = {c: lid for lid, c in (o.get("codes_leerling") or {}).items()}
    setnamen = {s["naam"]: s for s in o["sets"]}
    nieuw, dubbel, meldingen = 0, 0, []
    for b64, controle in gevonden:
        try:
            d = lees_code(b64, controle)
        except ValueError as fout:
            meldingen.append(f"Code …{b64[-8:]}: {fout}.")
            continue
        if d.get("o") != o["id"]:
            meldingen.append(f"Code …{b64[-8:]} hoort bij een andere opdracht en is overgeslagen.")
            continue
        lid = per_code.get(d.get("s"))
        if not lid:
            n = re.sub(r"\D", "", str(d.get("n") or ""))
            if n and n in o["leerling_ids"]:
                lid = n
            elif n:
                lid = n
                meldingen.append(f"Leerlingnummer {n} hoort niet bij deze opdracht; toch bewaard.")
            else:
                lid = f"zonder nummer ({d.get('s', '?')})"
                meldingen.append("Een code zonder leerlingnummer is bewaard onder 'zonder nummer'.")
        rij = {"datum": d.get("d", ""), "set": d.get("s", ""), "hints": d.get("h", 0), "gemaakt": d.get("g", 0),
               "totaal": d.get("t", 0), "doelen": {r[0]: {"score": r[1], "max": r[2], "gemaakt": r[3], "aantal": r[4]}
                                                    for r in d["r"] if len(r) >= 5},
               "ontvangen": datetime.now().isoformat(timespec="seconds"), "code": f"{b64}-{controle}"}
        lijst = data.setdefault(lid, [])
        if any(x["code"] == rij["code"] for x in lijst):
            dubbel += 1
            continue
        if setnamen.get(d.get("s")) and lid in o["leerling_ids"] and lid not in setnamen[d["s"]]["leerlingen"]:
            meldingen.append(f"Leerling {lid} heeft de oefenpagina van {d['s']} gemaakt, maar zit in een andere groep.")
        lijst.append(rij)
        nieuw += 1
    _pad(o["id"]).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"gevonden": len(gevonden), "nieuw": nieuw, "dubbel": dubbel, "meldingen": meldingen}


def overzicht(o: dict) -> dict:
    data = laad(o["id"])
    codes = [d["code"] for d in o["doelen"]]
    rijen, som = [], {c: [0.0, 0.0, 0] for c in codes}
    for lid, lijst in sorted(data.items()):
        laatste = lijst[-1]
        cellen = {}
        for c, v in laatste["doelen"].items():
            pct = v["score"] / v["max"] if v["max"] else 0.0
            cellen[c] = {"pct": pct, "status": "beheerst" if pct >= 0.8 else ("bijna" if pct >= 0.5 else "nog_niet"),
                         "open": v["aantal"] - v["gemaakt"]}
            if c in som and v["gemaakt"]:
                som[c][0] += v["score"]
                som[c][1] += v["max"]
                som[c][2] += 1
        rijen.append({"leerling_id": lid, "cellen": cellen, "datum": laatste["datum"], "gemaakt": laatste["gemaakt"],
                      "totaal": laatste["totaal"], "hints": laatste["hints"], "keer": len(lijst)})
    gemiddeld = {c: (s[0] / s[1] if s[1] else None, s[2]) for c, s in som.items()}
    ontbreekt = [lid for lid in o["leerling_ids"] if lid not in data]
    return {"codes": codes, "rijen": rijen, "gemiddeld": gemiddeld, "ontbreekt": ontbreekt}
