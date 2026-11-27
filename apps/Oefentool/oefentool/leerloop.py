# -*- coding: utf-8 -*-
"""Leerloop: oefenen -> oefentoets -> scannen in de Toetsevaluatie -> nieuwe stand -> volgende ronde.

- stand(): per leerling en leerdoel van een opdracht de huidige status (laatste meting telt; een
  oefentoets telt mee als meting met het label 'oefen').
- resultaten_terug(): P-waarde en Rit van elke vraag uit een gescande oefentoets terug naar de
  opgave in de itembank. Goede opgaven worden daarna vaker hergebruikt.
- volgende_ronde(): nieuwe opdracht voor wie nog niet alles beheerst, alleen op die leerdoelen.
"""
from __future__ import annotations

from collections import defaultdict

from . import opdracht as opd
from . import opslag, te


def _toetsen_van_opdracht(o: dict) -> list[str]:
    """De oorspronkelijke toetsen plus latere toetsen van dezelfde leerlingen en hetzelfde register."""
    ids = list(o["toets_ids"])
    leerlingen = set(o["leerling_ids"])
    for t in te.toetsen():
        if t["toets_id"] in ids or t["vak"] != o["vak"] or t["sector"] != o["sector"]:
            continue
        if t["datum"] and t["datum"] >= o["gemaakt"][:10] and t["aantal"]:
            if set(x["leerling_id"] for x in te.leerlingen_van([t["toets_id"]])) & leerlingen:
                ids.append(t["toets_id"])
    return ids


def stand(o: dict) -> dict:
    codes = [d["code"] for d in o["doelen"]]
    toetsen = _toetsen_van_opdracht(o)
    prof = te.profielen(toetsen, set(o["leerling_ids"]), set(codes))
    rij = []
    tel = defaultdict(int)
    for lid in o["leerling_ids"]:
        p = prof.get(lid)
        cellen = {}
        for d in (p["leerdoelen"] if p else []):
            if d["code"] in codes:
                laatste = d["metingen"][-1]
                cellen[d["code"]] = {"status": d["status"], "pct": d["laatste_pct"], "soort": laatste.get("soort", "toets"),
                                     "metingen": [(m["toets_id"], m["status"], m.get("soort", "toets")) for m in d["metingen"]],
                                     "wisselend": d["wisselend"], "teruggevallen": d["teruggevallen"]}
                tel[d["status"]] += 1
        open_ = [c for c in codes if c in cellen and cellen[c]["status"] != "beheerst"]
        rij.append({"leerling_id": lid, "cellen": cellen, "open": open_, "foutloos": bool(cellen) and not open_})
    nieuwe = [t for t in toetsen if t not in o["toets_ids"]]
    return {"codes": codes, "rijen": rij, "tel": dict(tel), "nieuwe_toetsen": nieuwe,
            "foutloos": sum(1 for r in rij if r["foutloos"]), "totaal": len(rij)}


def gescande_oefentoetsen(o: dict) -> list[dict]:
    exams = (o.get("uitvoer") or {}).get("oefenexamens", [])
    if not exams:
        return []
    bekend = {t["toets_id"]: t for t in te.toetsen()}
    uit = []
    for ex in exams:
        gevonden = next((t for tid, t in bekend.items() if tid.startswith(ex["code"] + "_OE_")), None)
        uit.append(dict(ex, gescand=gevonden["toets_id"] if gevonden else None,
                        aantal=gevonden["aantal"] if gevonden else 0))
    return uit


def resultaten_terug(o: dict) -> int:
    """Zet P en Rit uit gescande oefentoetsen bij de opgaven. Geeft het aantal bijgewerkte opgaven."""
    n = 0
    for ex in gescande_oefentoetsen(o):
        if not ex["gescand"]:
            continue
        stats = te.vraagstatistiek(ex["gescand"])
        with opslag.db() as con:
            con.execute("UPDATE item_gebruik SET toets_id = ? WHERE opdracht_id = ? AND set_naam = ?",
                        (ex["gescand"], o["id"], "examen:" + ex["groep"]))
        for nr, uid in enumerate(ex["items"], 1):
            s = stats.get(nr)
            if not s or s["p"] is None:
                continue
            opslag.zet_item(uid, p_waarde=round(s["p"], 3), rit=round(s["rit"], 3) if s["rit"] is not None else None,
                            n_resultaten=s["n"])
            n += 1
    return n


def volgende_ronde(oid: str) -> str:
    o = opd.lees(oid)
    st = stand(o)
    nog = [r["leerling_id"] for r in st["rijen"] if r["open"]]
    if not nog:
        raise opd.Stop("Iedereen beheerst alle leerdoelen van deze opdracht. De leerloop is rond.")
    codes = sorted({c for r in st["rijen"] for c in r["open"]})
    inst = dict(o["instellingen"])
    return opd.maak(o["vak"], _toetsen_van_opdracht(o), o["doel"], o["klassen"], nog, codes, inst,
                    naam=f"{o['naam']} – ronde {o['leerloop']['ronde'] + 1}", vorige=oid)
