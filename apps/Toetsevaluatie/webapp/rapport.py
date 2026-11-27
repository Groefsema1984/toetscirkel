# -*- coding: utf-8 -*-
"""Gegevens voor de feedbackstrook (fase 4) en de klassenrapportage (fase 5).

Leest de definitieve scores uit de database en vult aan met wat de sessie nog weet (namen,
niet-gemaakte vragen, AI-teksten).
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from functools import lru_cache

import analyse
import database
import sessie
from config import BASIS, LEERGAT_LEERLING, kern


@lru_cache(maxsize=1)
def _alle_namen() -> dict[str, dict]:
    lijst, _ = kern.laad_klassenlijst(BASIS / "_bestaat_niet_", None)
    return {lid: {"naam": l.naam, "klas": l.klas} for lid, l in lijst.items()}


def naam_van(toets_id: str, lid: str) -> str:
    s = sessie.lees(toets_id)
    if s and lid in s["leerlingen"]:
        return s["leerlingen"][lid]["naam"]
    if s and lid in s.get("klassenlijst", {}):
        return s["klassenlijst"][lid]["naam"]
    return _alle_namen().get(lid, {}).get("naam", "")


def toets_info(toets_id: str):
    try:
        return kern.lees_toetsmap(toets_id)
    except ValueError:
        return None


def _niet_gemaakt(toets_id: str, rijen: list[dict]) -> dict[tuple[str, int], bool]:
    s = sessie.lees(toets_id)
    if s and s["leerlingen"]:
        return {(lid, v["nr"]): bool(v.get("niet_gemaakt"))
                for lid, l in s["leerlingen"].items() for v in l["vragen"].values()}
    # Toets die via het oude script in de database kwam: afleiden uit de foutomschrijving.
    return {(r["leerling_id"], r["vraagnummer"]):
            (r["score"] == 0 and "niet gemaakt" in (r["omschrijving"] or "").lower()) for r in rijen}


def statistiek_voor(toets_id: str) -> dict | None:
    rijen = database.rijen(toets_id)
    if not rijen:
        return None
    info = toets_info(toets_id)
    s = sessie.lees(toets_id)
    niveau = info.niveau if info else (rijen[0]["niveau"] or "")
    n, n_havo = database.normering(toets_id)
    stat = analyse.statistiek(rijen, niveau, (s or {}).get("duur"), _niet_gemaakt(toets_id, rijen),
                              n, n_havo)
    stat["n_term"], stat["n_havo"] = n, n_havo
    for l in stat["leerlingen"]:
        l["naam"] = naam_van(toets_id, l["leerling_id"])
    stat["niveau"], stat["vak"] = niveau, info.vak if info else ""
    return stat


def trend_voor(toets_id: str) -> list[dict]:
    """Eerdere schoolexamens van hetzelfde vak en niveau, oudste eerst."""
    info = toets_info(toets_id)
    paren = []
    for t in database.toetsen():
        ander = toets_info(t["toets_id"])
        if info and ander and (ander.vak, ander.niveau) != (info.vak, info.niveau):
            continue
        rijen = database.rijen(t["toets_id"])
        if rijen:
            paren.append((t, rijen, database.normering(t["toets_id"])[0]))
    return analyse.trend(paren)


# ---------------------------------------------------------------------------- feedbackstrook
def _leerling_rijen(toets_id: str, lid: str) -> list[dict]:
    return [r for r in database.rijen(toets_id) if r["leerling_id"] == lid]


def _domeinen(rijen: list[dict]) -> list[dict]:
    som = defaultdict(lambda: [0.0, 0.0])
    for r in rijen:
        som[r["domein_eindterm"] or "Onbekend"][0] += r["score"]
        som[r["domein_eindterm"] or "Onbekend"][1] += r["max_score"]
    return [{"naam": k, "behaald": a, "max": b, "pct": round(100 * a / b) if b else 0}
            for k, (a, b) in som.items()]


def _vragen_tekst(nrs: list[int]) -> str:
    """[1, 2, 3, 6] -> 'vraag 1-3, 6'."""
    delen, nrs = [], sorted(set(nrs))
    for nr in nrs:
        if delen and nr == delen[-1][1] + 1:
            delen[-1][1] = nr
        else:
            delen.append([nr, nr])
    return ("vraag " if len(nrs) == 1 else "vragen ") + ", ".join(
        str(a) if a == b else f"{a}-{b}" for a, b in delen)


def _hoofdeindtermen(rijen: list[dict]) -> list[dict]:
    """Beheersing per hoofdeindterm: 'H1.2, H1.4' telt mee bij H1. Zonder codes: per domein."""
    som = {}
    for r in sorted(rijen, key=lambda r: r["vraagnummer"]):
        tekst = r["domein_eindterm"] or "Onbekend"
        groepen = list(dict.fromkeys(re.findall(r"\b([A-Z]{1,2}\d+)(?:\.\d+)?\b", tekst))) or [tekst]
        for g in groepen:
            d = som.setdefault(g, {"behaald": 0.0, "max": 0.0, "vragen": []})
            d["behaald"] += r["score"]
            d["max"] += r["max_score"]
            d["vragen"].append(r["vraagnummer"])
    return [{"naam": k, "vragen": _vragen_tekst(d["vragen"]), "behaald": d["behaald"],
             "max": d["max"], "pct": round(100 * d["behaald"] / d["max"]) if d["max"] else 0}
            for k, d in sorted(som.items(), key=lambda kv: min(kv[1]["vragen"]))]


def feedforward_gegevens(toets_id: str, lid: str) -> str:
    """Tekst met de resultaten van één leerling, als invoer voor de AI (zonder naam)."""
    rijen = _leerling_rijen(toets_id, lid)
    info = toets_info(toets_id)
    regels = [f"Toets: {toets_id} ({info.vak}, {info.niveau})", "", "Beheersing per domein:"]
    regels += [f"- {d['naam']}: {d['pct']}% ({d['behaald']:g} van {d['max']:g} punten)"
               for d in _domeinen(rijen)]
    verlies = [r for r in rijen if r["score"] < r["max_score"]]
    regels += ["", "Vragen met puntenverlies:" if verlies else "Geen puntenverlies."]
    for r in verlies:
        regels.append(
            f"- vraag {r['vraagnummer']} (domein {r['domein_eindterm']}, "
            + (f"leerdoel: {r['leerdoel']}, onderwerp: {r['onderwerp']}, " if r.get("leerdoel") else "")
            + f"{analyse.bloom_naam(r['bloom_niveau'])}): {r['score']:g} van {r['max_score']:g} "
            f"punten, leergat {r['leergat'] or 'onbekend'}. Wat ging mis: "
            f"{r['omschrijving'] or 'niet omschreven'}")
    vol = [str(r["vraagnummer"]) for r in rijen if r["score"] >= r["max_score"]]
    regels += ["", "Vragen met de volle score: " + (", ".join(vol) or "geen")]
    return "\n".join(regels)


def feedback(toets_id: str, lid: str) -> dict | None:
    """Alles wat de feedbackstrook van één leerling nodig heeft."""
    rijen = _leerling_rijen(toets_id, lid)
    if not rijen:
        return None
    info = toets_info(toets_id)
    s = sessie.lees(toets_id) or {}
    toets = database.toets(toets_id) or {}
    ff = (s.get("feedforward") or {}).get(lid)

    per_code = defaultdict(lambda: {"vragen": [], "punten": 0.0})
    for r in rijen:
        if r["score"] < r["max_score"] and r["leergat"] in LEERGAT_LEERLING:
            per_code[r["leergat"]]["vragen"].append(r["vraagnummer"])
            per_code[r["leergat"]]["punten"] += r["max_score"] - r["score"]
    ai_uitleg = {g["code"]: g["uitleg"] for g in (ff or {}).get("leergaten", [])}
    leergaten = []
    for code, d in sorted(per_code.items(), key=lambda kv: -kv[1]["punten"]):
        titel, uitleg, _ = LEERGAT_LEERLING[code]
        leergaten.append({"code": code, "titel": titel, "uitleg": ai_uitleg.get(code) or uitleg,
                          "vragen": d["vragen"]})

    domeinen = _domeinen(rijen)
    leerdoelen = []
    for r in sorted(rijen, key=lambda r: r["vraagnummer"]):
        pct = r["score"] / r["max_score"] if r["max_score"] else 0
        status = "ja" if pct >= 1 else ("bijna" if pct >= 0.5 else "nee")
        gat = LEERGAT_LEERLING.get(r["leergat"])
        leerdoelen.append({
            "nr": r["vraagnummer"], "eindterm": r["domein_eindterm"] or "",
            "leerdoel": r.get("leerdoel_leerling") or r.get("leerdoel") or "",
            "onderwerp": r.get("onderwerp") or "", "status": status,
            "punten": f"{r['score']:g} van {r['max_score']:g}",
            "wat": (r["omschrijving"] or "") if status != "ja" else "",
            "soort": gat[0] if gat and status != "ja" else ""})
    if ff and ff.get("actieplan"):
        actieplan = ff["actieplan"][:3]
        sterk = ff.get("sterk", "")
        reflectie = ff.get("reflectievragen", [])[:3]
    else:                                             # geen AI-tekst: vaste, bruikbare standaard
        actieplan = [{"stap": LEERGAT_LEERLING[g["code"]][2],
                      "hoe": "Begin met vraag " + ", ".join(map(str, g["vragen"][:3])) + "."}
                     for g in leergaten[:3]]
        if not actieplan:
            actieplan = [{"stap": "Kies een verdiepende opgave op examenniveau bij dit onderwerp.",
                          "hoe": "Vraag je docent om een opgave en leg je aanpak uit aan een "
                                 "klasgenoot."}]
        beste = max(domeinen, key=lambda d: d["pct"])
        sterk = f"Je sterkste onderdeel was {beste['naam']}."
        reflectie = ["Hoe heb je je op deze toets voorbereid, en wat werkte goed?",
                     "Bij welke vraag wist je tijdens de toets al dat het niet goed ging? "
                     "Wat deed je toen?"]

    tweefasen = []
    for label, naam in (("H+V", "Stof voor havo en vwo"), ("V", "Stof alleen voor vwo")):
        deel = [r for r in rijen if r["tweefasen_label"] == label]
        if deel:
            a, b = sum(r["score"] for r in deel), sum(r["max_score"] for r in deel)
            tweefasen.append({"naam": naam, "pct": round(100 * a / b) if b else 0})

    datum = toets.get("datum") or ""
    try:
        datum = datetime.strptime(datum, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        pass
    return {
        "leerling_id": lid, "naam": naam_van(toets_id, lid), "klas": rijen[0]["klas"] or "",
        "toets_id": toets_id, "titel": toets_id.replace("_", " "),
        "vak": info.vak if info else toets.get("vak", ""),
        "niveau": info.niveau if info else "", "datum": datum,
        "domeinen": domeinen, "tweefasen": tweefasen, "leergaten": leergaten,
        "leerdoelen": leerdoelen, "heeft_leerdoelen": any(d["leerdoel"] for d in leerdoelen),
        "eindtermen": _hoofdeindtermen(rijen),
        "sterk": sterk, "actieplan": actieplan, "reflectie": reflectie,
        "persoonlijk": bool(ff),
        "verlies": [{"nr": r["vraagnummer"], "omschrijving": r["omschrijving"] or "",
                     "leergat": r["leergat"]} for r in rijen if r["score"] < r["max_score"]],
    }


# ---------------------------------------------------------------------------- klassenadvies
def klas_gegevens(toets_id: str) -> str:
    """Tekst met de toetsanalyse per vraag en het foutenprofiel per leerling, voor de AI."""
    stat = statistiek_voor(toets_id)
    rijen = database.rijen(toets_id)
    info = toets_info(toets_id)
    regels = [f"Toets {toets_id}: {info.vak}, {info.niveau}, {stat['n']} leerlingen, "
              f"gemiddeld {stat['gem_pct']}% van de punten.", "", "PER VRAAG"]
    fouten = defaultdict(lambda: defaultdict(list))
    for r in rijen:
        if r["score"] < r["max_score"]:
            fouten[r["vraagnummer"]][r["leergat"] or "?"].append(r["omschrijving"] or "")
    for v in stat["vragen"]:
        delen = []
        for code, lijst in sorted(fouten[v["nr"]].items(), key=lambda kv: -len(kv[1])):
            voorbeelden = list(dict.fromkeys(x for x in lijst if x))[:3]
            delen.append(f"{code} x{len(lijst)}: " + " / ".join(voorbeelden))
        regels.append(
            f"vraag {v['nr']} | {v['domein']} | {v['leerdoel'] or '-'} | {v['bloom']} | max {v['max']} | "
            f"P {v['p']} | Rit {v['rit']} | fouten: " + ("; ".join(delen) or "geen"))
    regels += ["", "PER LEERLING (leerlingnummer | naam | % van de punten | verloren punten per "
                   "leergat met vraagnummers | zwakste domein)"]
    per_ll = defaultdict(list)
    for r in rijen:
        per_ll[r["leerling_id"]].append(r)
    for l in stat["leerlingen"]:
        mijn = per_ll[l["leerling_id"]]
        per_code = defaultdict(lambda: [0.0, []])
        for r in mijn:
            if r["score"] < r["max_score"]:
                per_code[r["leergat"] or "?"][0] += r["max_score"] - r["score"]
                per_code[r["leergat"] or "?"][1].append(r["vraagnummer"])
        profiel = "; ".join(f"{c} {p:g} (vraag {', '.join(map(str, nrs))})"
                            for c, (p, nrs) in sorted(per_code.items(), key=lambda kv: -kv[1][0]))
        zwak = min(_domeinen(mijn), key=lambda d: d["pct"])
        regels.append(f"{l['leerling_id']} | {l['naam'] or 'onbekend'} | {l['pct']}% | "
                      f"{profiel or 'geen puntenverlies'} | {zwak['naam']} ({zwak['pct']}%)")
    return "\n".join(regels)
