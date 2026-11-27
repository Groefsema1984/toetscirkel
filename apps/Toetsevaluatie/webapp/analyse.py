# -*- coding: utf-8 -*-
"""Toetsanalyse: cijfers, P- en Rit-waarden, Bloom, domeinen, tweefasen, leergaten en tijdnood.

Alles wordt berekend uit de definitieve scores in de database. Er zit geen AI in dit bestand.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

from config import BLOOM, BLOOM_ALIAS, NORM_MIN_PER_PUNT, kern


def cijfer(score: float, maximaal: float, n: float = 1.0) -> float | None:
    """Score naar cijfer met normeringsterm N, volgens de Regeling omzetting scores in cijfers VO.

    Hoofdrelatie C = 9 x S/L + N. Bij N > 1 gelden de grensrelaties 1 + S x 9/L x 2 (laagste
    scores) en 10 - (L-S) x 9/L x 0,5 (hoogste scores): de laagste van de drie telt. Bij N < 1
    gelden 1 + S x 9/L x 0,5 en 10 - (L-S) x 9/L x 2: de hoogste van de drie telt. Zo levert
    0 punten altijd een 1,0 op en de volle score altijd een 10,0. Afronding op één decimaal.
    """
    if not maximaal:
        return None
    s, l, n = float(score), float(maximaal), float(n)
    c = 9.0 * s / l + n
    if n > 1:
        c = min(c, 1.0 + s * 9.0 / l * 2, 10.0 - (l - s) * 9.0 / l * 0.5)
    elif n < 1:
        c = max(c, 1.0 + s * 9.0 / l * 0.5, 10.0 - (l - s) * 9.0 / l * 2)
    return math.floor(c * 10 + 0.5 + 1e-9) / 10


def cesuur(maximaal: float, n: float = 1.0) -> float | None:
    """Het laagste aantal hele punten waarmee een leerling een 5,5 haalt."""
    if not maximaal:
        return None
    for s in range(int(maximaal) + 1):
        if cijfer(s, maximaal, n) >= 5.5:
            return s
    return None


def pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None                                   # geen spreiding: correlatie bestaat niet
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sxx * syy)


def bloom_naam(ruw: str | None) -> str:
    if not ruw:
        return "Onbekend"
    eerste = ruw.strip().lower().split()[0].strip(".,:;()") if ruw.strip() else ""
    return BLOOM_ALIAS.get(eerste, ruw.strip().capitalize())


def _pct(behaald: float, maximaal: float) -> float | None:
    return round(100.0 * behaald / maximaal, 1) if maximaal else None


def statistiek(rijen: list[dict], niveau: str, duur: int | None,
               niet_gemaakt: dict[tuple[str, int], bool] | None = None,
               n_term: float = 1.0, n_havo: float | None = None) -> dict | None:
    if not rijen:
        return None
    per_ll: dict[str, dict[int, dict]] = defaultdict(dict)
    items: dict[int, dict] = {}
    for r in rijen:
        per_ll[r["leerling_id"]][r["vraagnummer"]] = r
        items.setdefault(r["vraagnummer"], r)
    nrs = sorted(items)
    lids = sorted(per_ll)
    n = len(lids)

    # ---- per leerling
    leerlingen = []
    for lid in lids:
        v = per_ll[lid]
        score = sum(x["score"] for x in v.values())
        maximaal = sum(x["max_score"] for x in v.values())
        hv = [x for x in v.values() if x["tweefasen_label"] == "H+V"]
        verlies = defaultdict(float)
        for x in v.values():
            if x["score"] < x["max_score"]:
                verlies[x["leergat"] or "geen label"] += x["max_score"] - x["score"]
        leerlingen.append({
            "leerling_id": lid, "klas": next(iter(v.values()))["klas"] or "",
            "score": score, "max": maximaal, "pct": _pct(score, maximaal),
            "cijfer": cijfer(score, maximaal, n_term),
            "havo_cijfer": cijfer(sum(x["score"] for x in hv), sum(x["max_score"] for x in hv),
                                  n_term if n_havo is None else n_havo) if hv else None,
            "hv_score": sum(x["score"] for x in hv), "hv_max": sum(x["max_score"] for x in hv),
            "verlies": dict(verlies),
        })
    totalen = {l["leerling_id"]: l["score"] for l in leerlingen}
    cijfers = [l["cijfer"] for l in leerlingen if l["cijfer"] is not None]

    # ---- per vraag
    vragen = []
    for nr in nrs:
        wie = [lid for lid in lids if nr in per_ll[lid]]
        sc = [per_ll[lid][nr]["score"] for lid in wie]
        tot = [totalen[lid] for lid in wie]
        mx = items[nr]["max_score"]
        p = (sum(sc) / len(sc) / mx) if sc and mx else None
        rit = pearson(sc, tot) if len(sc) >= 5 else None
        rir = pearson(sc, [t - s for t, s in zip(tot, sc)]) if len(sc) >= 5 else None
        ng = None
        if niet_gemaakt is not None and wie:
            ng = round(100.0 * sum(1 for lid in wie if niet_gemaakt.get((lid, nr))) / len(wie), 1)
        signalen = []
        if p is not None and p < 0.30:
            signalen.append("meer dan 70% van de punten gemist")
        if rit is not None and rit < 0:
            signalen.append("negatieve Rit: sterke leerlingen scoren hier lager dan zwakke")
        vragen.append({
            "nr": nr, "domein": items[nr]["domein_eindterm"] or "Onbekend",
            "bloom": bloom_naam(items[nr]["bloom_niveau"]), "label": items[nr]["tweefasen_label"],
            "leerdoel": items[nr].get("leerdoel") or "", "onderwerp": items[nr].get("onderwerp") or "",
            "max": mx, "gem": round(sum(sc) / len(sc), 2) if sc else None,
            "p": round(p, 2) if p is not None else None,
            "rit": round(rit, 2) if rit is not None else None,
            "rir": round(rir, 2) if rir is not None else None,
            "niet_gemaakt": ng, "signalen": signalen,
        })

    # ---- groeperingen
    def groep(sleutel) -> list[dict]:
        som = defaultdict(lambda: [0.0, 0.0, set()])
        for r in rijen:
            g = som[sleutel(r)]
            g[0] += r["score"]
            g[1] += r["max_score"]
            g[2].add(r["vraagnummer"])
        return [{"naam": k, "pct": _pct(a, b), "uitval": round(100 - _pct(a, b), 1) if b else None,
                 "vragen": len(v)} for k, (a, b, v) in som.items()]

    bloom = groep(lambda r: bloom_naam(r["bloom_niveau"]))
    volgorde = {naam: i for i, naam in enumerate(BLOOM)}
    bloom.sort(key=lambda b: volgorde.get(b["naam"], 99))
    domein = sorted(groep(lambda r: r["domein_eindterm"] or "Onbekend"), key=lambda d: d["naam"])
    tweefasen = None
    if any(r["tweefasen_label"] for r in rijen):
        tf = {g["naam"]: g for g in groep(lambda r: r["tweefasen_label"] or "geen label")}
        havo = [l["havo_cijfer"] for l in leerlingen if l["havo_cijfer"] is not None]
        tweefasen = {
            "hv": tf.get("H+V"), "v": tf.get("V"),
            "gem_havo_cijfer": round(statistics.mean(havo), 2) if havo else None,
            "gem_vwo_cijfer": round(statistics.mean(cijfers), 2) if cijfers else None,
        }

    leergaten = []
    for code, uitleg in kern.LEERGATEN.items():
        deel = [r for r in rijen if r["leergat"] == code and r["score"] < r["max_score"]]
        leergaten.append({"code": code, "naam": uitleg.split(":")[0],
                          "punten": round(sum(r["max_score"] - r["score"] for r in deel), 1),
                          "keer": len(deel),
                          "leerlingen": len({r["leerling_id"] for r in deel})})

    verdeling = [0] * 9                                # cijfers 1-2, 2-3, ..., 9-10
    for c in cijfers:
        verdeling[min(int(c) - 1, 8)] += 1

    # ---- toetslengte en tijdnood
    totaal_max = sum(items[nr]["max_score"] for nr in nrs)
    norm = NORM_MIN_PER_PUNT.get(niveau)
    tijd = {"duur": duur, "punten": totaal_max, "norm": norm,
            "min_per_punt": round(duur / totaal_max, 2) if duur and totaal_max else None,
            "te_krap": None, "eind_ng": None, "rest_ng": None, "eind_p": None, "rest_p": None,
            "eind_vragen": [], "signaal": False}
    if tijd["min_per_punt"] is not None and norm:
        tijd["te_krap"] = tijd["min_per_punt"] < norm
    if len(nrs) >= 8:
        staart = nrs[-max(2, math.ceil(len(nrs) / 4)):]
        kop = [nr for nr in nrs if nr not in staart]
        per_nr = {v["nr"]: v for v in vragen}

        def gem(lijst, veld):
            w = [per_nr[nr][veld] for nr in lijst if per_nr[nr][veld] is not None]
            return round(sum(w) / len(w), 2) if w else None
        tijd.update(eind_vragen=staart, eind_ng=gem(staart, "niet_gemaakt"),
                    rest_ng=gem(kop, "niet_gemaakt"), eind_p=gem(staart, "p"), rest_p=gem(kop, "p"))
        if tijd["eind_ng"] is not None and tijd["eind_ng"] >= 15 \
                and tijd["eind_ng"] >= 2 * (tijd["rest_ng"] or 0):
            tijd["signaal"] = True

    return {
        "n": n, "klein": n < 8,
        "gem_cijfer": round(statistics.mean(cijfers), 2) if cijfers else None,
        "sd_cijfer": round(statistics.stdev(cijfers), 2) if len(cijfers) >= 2 else None,
        "gem_pct": round(statistics.mean(l["pct"] for l in leerlingen), 1),
        "hoogste": max(cijfers) if cijfers else None, "laagste": min(cijfers) if cijfers else None,
        "onvoldoende": sum(1 for c in cijfers if c < 5.5),
        "leerlingen": leerlingen, "vragen": vragen, "bloom": bloom, "domein": domein,
        "tweefasen": tweefasen, "leergaten": leergaten, "verdeling": verdeling, "tijd": tijd,
    }


def trend(toetsen_met_rijen: list[tuple[dict, list[dict], float]]) -> list[dict]:
    """Klassengemiddelde per toets, oudste eerst. Invoer: [(toets, rijen, n_term), ...]."""
    uit = []
    for toets, rijen, n in toetsen_met_rijen:
        som = defaultdict(lambda: [0.0, 0.0])
        for r in rijen:
            som[r["leerling_id"]][0] += r["score"]
            som[r["leerling_id"]][1] += r["max_score"]
        cijfers = [cijfer(a, b, n) for a, b in som.values() if b]
        if cijfers:
            uit.append({"toets_id": toets["toets_id"], "datum": toets["datum"] or "",
                        "gem": round(statistics.mean(cijfers), 2),
                        "sd": round(statistics.stdev(cijfers), 2) if len(cijfers) >= 2 else None,
                        "n": len(cijfers)})
    return sorted(uit, key=lambda t: (t["datum"], t["toets_id"]))
