# -*- coding: utf-8 -*-
"""Doelgroep van een opdracht: welke leerdoelen, welke leerlingen, welke groepen.

Alles hier gebeurt lokaal. Naar Claude gaan alleen de samengevoegde aantallen per leerdoel."""
from __future__ import annotations

from collections import Counter, defaultdict

from . import te

GEWICHT = {"nog_niet": 1.0, "bijna": 0.6, "wisselend": 0.3, "onderhoud": 0.2}
STAPPEN = {"nog_niet": [1, 2, 3], "bijna": [2, 3], "wisselend": [3], "onderhoud": [3]}
ONDERHOUD_NA_DAGEN = 28


def _stand(d: dict) -> str | None:
    if d["status"] in ("nog_niet", "bijna"):
        return d["status"]
    if d.get("wisselend"):
        return "wisselend"
    return None


def bouw(toets_ids: list[str], leerling_ids: set[str] | None, klassen: set[str] | None,
         codes: set[str] | None) -> tuple[dict[str, dict], list[dict]]:
    """Geeft (profielen per leerling, leerdoelen met aantallen) voor deze keuze."""
    prof = te.profielen(toets_ids, leerling_ids or None, codes or None)
    if klassen:
        prof = {lid: p for lid, p in prof.items() if p["klas"] in klassen}
    per = {}
    for p in prof.values():
        for d in p["leerdoelen"]:
            if not d["gekoppeld"]:
                continue
            a = per.setdefault(d["code"], {
                "code": d["code"], "omschrijving": d["omschrijving"], "leerling_zin": d["leerling_zin"],
                "thema": d["thema"], "eindtermen": set(), "bloom": set(), "nog_niet": 0, "bijna": 0,
                "wisselend": 0, "beheerst": 0, "totaal": 0, "leergaten": Counter(), "fouten": [],
                "pct": []})
            a["eindtermen"] |= set(d["eindtermen"])
            a["bloom"] |= set(d["bloom"])
            a["totaal"] += 1
            a["pct"].append(d["laatste_pct"])
            stand = _stand(d)
            a[stand or "beheerst"] += 1
            laatste = d["metingen"][-1]["toets_id"]
            for g in d["leergaten"]:
                if g["toets_id"] == laatste:
                    if g["leergat"]:
                        a["leergaten"][g["leergat"]] += 1
                    o = (g.get("omschrijving") or "").strip()
                    if o and o not in a["fouten"] and len(a["fouten"]) < 12:
                        a["fouten"].append(o[:220])
    doelen = []
    for a in per.values():
        a["eindtermen"] = sorted(a["eindtermen"])
        a["bloom"] = sorted(a["bloom"])
        a["leergaten"] = dict(a["leergaten"].most_common())
        a["gem_pct"] = round(sum(a["pct"]) / len(a["pct"]), 3) if a["pct"] else 0
        del a["pct"]
        a["nodig"] = a["nog_niet"] + a["bijna"] + a["wisselend"]
        a["score"] = a["nog_niet"] * 1.0 + a["bijna"] * 0.6 + a["wisselend"] * 0.3
        doelen.append(a)
    doelen.sort(key=lambda a: (-a["score"], a["code"]))
    return prof, doelen


def _vector(p: dict) -> dict[str, float]:
    return {d["code"]: GEWICHT[s] for d in p["leerdoelen"] if d["gekoppeld"] and (s := _stand(d))}


def _afstand(a: dict, b: dict) -> float:
    sleutels = set(a) | set(b)
    if not sleutels:
        return 0.0
    return sum(abs(a.get(k, 0) - b.get(k, 0)) for k in sleutels) / len(sleutels)


def groepeer(prof: dict[str, dict], k: int) -> list[list[str]]:
    """Leerlingen met dezelfde zwakke leerdoelen bij elkaar (k-medoids, voorspelbaar)."""
    ids = sorted(prof)
    vec = {lid: _vector(prof[lid]) for lid in ids}
    ids = [lid for lid in ids if vec[lid]] + [lid for lid in ids if not vec[lid]]
    k = max(1, min(k, len([i for i in ids if vec[i]]) or 1))
    if len(ids) <= k:
        return [[i] for i in ids]
    # start: de leerling met de meeste zwakke punten, daarna steeds de verst verwijderde
    medoids = [max(ids, key=lambda i: (sum(vec[i].values()), i))]
    while len(medoids) < k:
        medoids.append(max((i for i in ids if i not in medoids),
                           key=lambda i: (min(_afstand(vec[i], vec[m]) for m in medoids), i)))
    for _ in range(10):
        groepen = defaultdict(list)
        for i in ids:
            groepen[min(medoids, key=lambda m: (_afstand(vec[i], vec[m]), m))].append(i)
        nieuw = []
        for m, leden in groepen.items():
            nieuw.append(min(leden, key=lambda c: (sum(_afstand(vec[c], vec[o]) for o in leden), c)))
        if sorted(nieuw) == sorted(medoids):
            break
        medoids = nieuw
    uit = [sorted(groepen[m]) for m in medoids if groepen[m]]
    # te kleine groepjes (minder dan 3) samenvoegen met de dichtstbijzijnde groep
    while len(uit) > 1 and min(len(g) for g in uit) < 3:
        klein = min(uit, key=len)
        uit.remove(klein)
        doel = min(uit, key=lambda g: sum(_afstand(vec[a], vec[b]) for a in klein for b in g) / len(g))
        doel += klein
        doel.sort()
    uit.sort(key=lambda g: -sum(sum(vec[i].values()) for i in g))
    return uit


def set_doelen(prof: dict[str, dict], leden: list[str], doelen: list[dict], drempel: float) -> list[dict]:
    """Leerdoelen van een set: die bij minstens 'drempel' van de leden nog niet beheerst zijn."""
    uit = []
    for d in doelen:
        standen = Counter()
        for lid in leden:
            for x in prof[lid]["leerdoelen"]:
                if x["code"] == d["code"]:
                    s = _stand(x)
                    if s:
                        standen[s] += 1
        n = sum(standen.values())
        if n and n / max(1, len(leden)) >= drempel - 1e-9:
            meest = "nog_niet" if standen["nog_niet"] >= max(standen["bijna"], 1) else (
                "bijna" if standen["bijna"] else "wisselend")
            uit.append({"code": d["code"], "stand": meest, "aantal": n})
    uit.sort(key=lambda x: (-GEWICHT[x["stand"]] * x["aantal"], x["code"]))
    return uit


def maak_sets(doel: str, prof: dict[str, dict], doelen: list[dict], groepen: int) -> list[dict]:
    if doel == "leerling":
        sets = []
        for lid in sorted(prof):
            eigen = [{"code": d["code"], "stand": _stand(d), "aantal": 1}
                     for d in prof[lid]["leerdoelen"] if d["gekoppeld"] and _stand(d)]
            if eigen:
                sets.append({"naam": lid, "soort": "leerling", "leerlingen": [lid], "doelen": eigen})
        return sets
    if doel == "groep":
        sets = []
        for i, leden in enumerate(groepeer(prof, groepen)):
            sets.append({"naam": f"Groep {chr(65 + i)}", "soort": "groep", "leerlingen": leden,
                         "doelen": set_doelen(prof, leden, doelen, 0.4) or set_doelen(prof, leden, doelen, 0.01)[:3]})
        return [s for s in sets if s["doelen"]]
    if doel == "onderhoud":
        return _onderhoud_sets(prof, doelen)
    drempel = 0.15 if doel == "examentraining" else 0.25
    per_klas = defaultdict(list)
    for lid, p in prof.items():
        per_klas[p["klas"] or "klas"].append(lid)
    return [{"naam": klas, "soort": "klas", "leerlingen": sorted(leden),
             "doelen": set_doelen(prof, leden, doelen, drempel)} for klas, leden in sorted(per_klas.items())]


def examengroepen(opdracht: dict, prof: dict[str, dict], doelen: list[dict]) -> list[dict]:
    """Oefenexamen per groep. Bij 'per leerling' worden voor het examen groepen gemaakt."""
    if opdracht["doel"] in ("groep",):
        return [{"naam": s["naam"], "leerlingen": s["leerlingen"], "doelen": s["doelen"]} for s in opdracht["sets"]]
    if opdracht["doel"] == "leerling":
        k = max(1, min(4, round(len(prof) / 8)))
        return [{"naam": f"Groep {chr(65 + i)}", "leerlingen": leden,
                 "doelen": set_doelen(prof, leden, doelen, 0.4) or set_doelen(prof, leden, doelen, 0.01)[:3]}
                for i, leden in enumerate(groepeer(prof, k))]
    return [{"naam": s["naam"], "leerlingen": s["leerlingen"], "doelen": s["doelen"]} for s in opdracht["sets"]]


def _onderhoud_sets(prof: dict[str, dict], doelen: list[dict]) -> list[dict]:
    """Per klas de leerdoelen die de meeste leerlingen al minstens vier weken beheersen."""
    from datetime import date
    vandaag = date.today()
    per_klas = defaultdict(list)
    for lid, p in prof.items():
        per_klas[p["klas"] or "klas"].append(lid)
    sets = []
    for klas, leden in sorted(per_klas.items()):
        tel = Counter()
        for lid in leden:
            for d in prof[lid]["leerdoelen"]:
                if not d["gekoppeld"] or d["status"] != "beheerst":
                    continue
                try:
                    oud = (vandaag - date.fromisoformat(d["metingen"][-1]["datum"])).days >= ONDERHOUD_NA_DAGEN
                except (TypeError, ValueError):
                    oud = False
                if oud:
                    tel[d["code"]] += 1
        eigen = [{"code": c, "stand": "onderhoud", "aantal": n} for c, n in tel.most_common() if n / len(leden) >= 0.5]
        sets.append({"naam": klas, "soort": "klas", "leerlingen": sorted(leden), "doelen": eigen})
    return [s for s in sets if s["doelen"]]
