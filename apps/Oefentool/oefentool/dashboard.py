# -*- coding: utf-8 -*-
"""Docentdashboard: klasoverzicht, trend, signalen, eindtermen, leerlingprofiel en het effect van oefenen.

Alles komt live uit de Toetsevaluatie (laatste meting telt, oefentoetsen tellen mee) plus de opdrachten
van de oefentool. Niets hiervan gaat naar Claude. Namen alleen in de printversie van een leerlingprofiel.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import opdracht as opd
from . import opslag, te, zelfcheck
from .instellingen import BEHEERST, BIJNA, LEERGATEN

ONDERHOUD_NA_DAGEN = 28
_ET = re.compile(r"\b([A-Z])(\d{1,2})(?:\.(\d{1,2}))?\b")


def _status(p: float) -> str:
    return "beheerst" if p >= BEHEERST - 1e-9 else ("bijna" if p >= BIJNA - 1e-9 else "nog_niet")


def scopes() -> list[dict]:
    per = {}
    for t in te.toetsen():
        if not t["sector"]:
            continue
        s = per.setdefault((t["vak"], t["sector"]), {"vak": t["vak"], "sector": t["sector"], "klassen": set(), "toetsen": 0})
        s["klassen"] |= set(t["klassen"])
        s["toetsen"] += 1
    return [dict(s, klassen=sorted(s["klassen"])) for s in sorted(per.values(), key=lambda s: (s["vak"], s["sector"]))]


def _toetsen(vak: str, sector: str) -> list[dict]:
    return [t for t in te.toetsen() if t["vak"] == vak and t["sector"] == sector]


def eindtermen_van(teksten) -> tuple[set[str], set[str]]:
    """('H1', 'H1.2') uit teksten als 'havo H1.2, vwo H1.4' of '25.3 (F2)'."""
    hoofd, detail = set(), set()
    for t in teksten:
        for letter, nr, sub in _ET.findall(t or ""):
            hoofd.add(f"{letter}{nr}")
            if sub:
                detail.add(f"{letter}{nr}.{sub}")
    return hoofd, detail


def klas(vak: str, sector: str, klas_: str = "") -> dict:
    toetsen = _toetsen(vak, sector)
    ids = [t["toets_id"] for t in toetsen]
    prof = te.profielen(ids) if ids else {}
    if klas_:
        prof = {lid: p for lid, p in prof.items() if p["klas"] == klas_}
    reg = {d["code"]: d for d in te.register_lijst(vak, sector)}
    gebruikt = {d["code"] for p in prof.values() for d in p["leerdoelen"] if d["gekoppeld"]}
    codes = sorted(gebruikt, key=lambda c: (reg.get(c, {}).get("thema", ""), c))
    vandaag = date.today()

    # -- totaaltabel en per leerdoel
    rijen, per_code = [], {c: Counter() for c in codes}
    teruggevallen, zorg, onderhoud = [], [], defaultdict(list)
    for lid, p in sorted(prof.items(), key=lambda kv: (kv[1]["klas"], kv[0])):
        cellen, groei = {}, []
        for d in p["leerdoelen"]:
            if d["code"] not in per_code:
                continue
            m = d["metingen"]
            cellen[d["code"]] = {"status": d["status"], "pct": d["laatste_pct"], "soort": m[-1].get("soort", "toets"),
                                 "n": len(m), "wisselend": d["wisselend"], "teruggevallen": d["teruggevallen"]}
            per_code[d["code"]][d["status"]] += 1
            if len(m) > 1:
                groei.append(m[-1]["pct"] - m[0]["pct"])
            if d["teruggevallen"]:
                teruggevallen.append((lid, d["code"]))
            if d["status"] == "beheerst":
                try:
                    if (vandaag - date.fromisoformat(m[-1]["datum"])).days >= ONDERHOUD_NA_DAGEN:
                        onderhoud[d["code"]].append(lid)
                except (TypeError, ValueError):
                    pass
        tel = Counter(c["status"] for c in cellen.values())
        rij = {"leerling_id": lid, "klas": p["klas"], "cellen": cellen, "tel": tel,
               "pct": tel["beheerst"] / len(cellen) if cellen else 0,
               "groei": sum(groei) / len(groei) if groei else None}
        if cellen and tel["nog_niet"] / len(cellen) >= 0.5:
            zorg.append(lid)
        rijen.append(rij)
    cellen_tot = sum(len(r["cellen"]) for r in rijen)
    beheerst_tot = sum(r["tel"]["beheerst"] for r in rijen)

    per_leerdoel = []
    for c in codes:
        t = per_code[c]
        n = sum(t.values())
        per_leerdoel.append({"code": c, "omschrijving": reg.get(c, {}).get("omschrijving", c), "thema": reg.get(c, {}).get("thema", ""),
                             "n": n, "beheerst": t["beheerst"], "bijna": t["bijna"], "nog_niet": t["nog_niet"],
                             "pct": t["beheerst"] / n if n else 0})
    minst = sorted([x for x in per_leerdoel if x["n"]], key=lambda x: (x["pct"], -x["nog_niet"]))[:5]

    # -- trend: stand na elke toetsdatum
    datums = sorted({m["datum"] for p in prof.values() for d in p["leerdoelen"] for m in d["metingen"] if m["datum"]})
    trend = []
    for dt in datums:
        b = n = 0
        for p in prof.values():
            for d in p["leerdoelen"]:
                if d["code"] not in per_code:
                    continue
                eerder = [m for m in d["metingen"] if m["datum"] and m["datum"] <= dt]
                if eerder:
                    n += 1
                    b += eerder[-1]["status"] == "beheerst"
        toetsen_op = sorted({m["toets_id"] for p in prof.values() for d in p["leerdoelen"] for m in d["metingen"] if m["datum"] == dt})
        trend.append({"datum": dt, "pct": b / n if n else 0, "toetsen": toetsen_op,
                      "oefen": any(te.soort_van(t) == "oefen" for t in toetsen_op)})

    # -- eindtermen
    et = eindtermen(prof, codes, vak, sector)

    # -- signalen uit opdrachten
    open_werk = signalen_opdrachten(vak, sector, set(prof))
    return {
        "vak": vak, "sector": sector, "klas": klas_, "codes": codes, "reg": reg, "rijen": rijen,
        "kern": {"leerlingen": len(rijen), "leerdoelen": len(codes), "toetsen": len(toetsen),
                 "oefentoetsen": sum(1 for t in toetsen if t["soort"] == "oefen"),
                 "pct": beheerst_tot / cellen_tot if cellen_tot else 0,
                 "vorige": trend[-2]["pct"] if len(trend) > 1 else None},
        "per_leerdoel": per_leerdoel, "minst": minst, "trend": trend,
        "toets_ids": [t["toets_id"] for t in toetsen if not klas_ or klas_ in t["klassen"]],
        "signalen": {"teruggevallen": teruggevallen, "zorg": zorg, **open_werk},
        "onderhoud": sorted(({"code": c, "leerlingen": l, "omschrijving": reg.get(c, {}).get("omschrijving", c)}
                             for c, l in onderhoud.items()), key=lambda x: -len(x["leerlingen"])),
        "eindtermen": et, "effect": effect(vak, sector, set(prof)),
    }


def eindtermen(prof: dict, codes: list[str], vak: str, sector: str) -> dict:
    """Per hoofdeindterm het aandeel beheerst; plus eindtermen uit de bibliotheek die nog niet getoetst zijn."""
    per_hoofd = defaultdict(lambda: {"beheerst": 0, "totaal": 0, "leerdoelen": set(), "detail": set()})
    getoetst = set()
    for p in prof.values():
        for d in p["leerdoelen"]:
            if d["code"] not in codes:
                continue
            hoofd, detail = eindtermen_van(d["eindtermen"])
            getoetst |= detail | hoofd
            for h in hoofd:
                x = per_hoofd[h]
                x["totaal"] += 1
                x["beheerst"] += d["status"] == "beheerst"
                x["leerdoelen"].add(d["code"])
                x["detail"] |= {e for e in detail if e.startswith(h + ".")}
    rijen = [{"hoofd": h, "pct": x["beheerst"] / x["totaal"] if x["totaal"] else 0, "leerdoelen": sorted(x["leerdoelen"]),
              "detail": sorted(x["detail"], key=lambda e: [int(n) for n in re.findall(r"\d+", e)])}
             for h, x in per_hoofd.items()]
    rijen.sort(key=lambda r: (r["hoofd"][0], int(r["hoofd"][1:])))
    # eindtermen die in je bibliotheek voorkomen (eindtermen, lesmateriaal) maar nog niet getoetst zijn
    niveaus = ("mavo",) if sector == "mavo" else ("havo", "vwo")
    with opslag.db() as con:
        teksten = [r[0] for r in con.execute(
            "SELECT f.eindtermen FROM fragmenten f JOIN bronnen b ON b.bron_id = f.bron_id "
            f"WHERE (b.vak = ? OR b.vak = '') AND (b.niveau IN ({','.join('?' * len(niveaus))}) OR b.niveau = '')",
            (vak, *niveaus))]
    _, bib_detail = eindtermen_van(teksten)
    bib_hoofd, _ = eindtermen_van(teksten)
    niet = sorted((bib_detail | bib_hoofd) - getoetst - {h for h in bib_hoofd if any(e.startswith(h + ".") for e in getoetst)},
                  key=lambda e: (e[0], [int(n) for n in re.findall(r"\d+", e)]))
    return {"rijen": rijen, "niet_getoetst": niet, "bibliotheek_gelezen": bool(teksten)}


def signalen_opdrachten(vak: str, sector: str, leerlingen: set[str]) -> dict:
    """Oefentoets nog niet gemaakt / geen resultaatcode ingestuurd, uit recente opdrachten."""
    geen_toets, geen_code = defaultdict(list), defaultdict(list)
    grens = (datetime.now() - timedelta(days=42)).isoformat()
    gescand = {t["toets_id"]: t for t in te.toetsen() if t["soort"] == "oefen"}
    for o in opd.alle():
        if o["vak"] != vak or o["sector"] != sector or o["status"] != "klaar" or o["gemaakt"] < grens:
            continue
        for ex in o.get("uitvoer", {}).get("oefenexamens", []):
            tid = next((t for t in gescand if t.startswith(ex["code"] + "_OE_")), None)
            if not tid:
                continue                       # nog niet ingescand: dan weten we nog niets
            gemaakt = {r["leerling_id"] for r in te.leerlingen_van([tid])}
            for lid in ex["leerlingen"]:
                if lid in leerlingen and lid not in gemaakt:
                    geen_toets[lid].append(ex["code"])
        if o.get("uitvoer", {}).get("digitaal"):
            ingestuurd = set(zelfcheck.laad(o["id"]))
            if ingestuurd:                     # pas signaleren als er al codes binnen zijn
                for lid in o["leerling_ids"]:
                    if lid in leerlingen and lid not in ingestuurd:
                        geen_code[lid].append(o["naam"])
    return {"geen_oefentoets": dict(geen_toets), "geen_resultaatcode": dict(geen_code)}


def effect(vak: str, sector: str, leerlingen: set[str]) -> list[dict]:
    """Per opdracht met een gescande oefentoets: score vóór (laatste meting vóór de opdracht) en ná."""
    uit = []
    gescand = {t["toets_id"] for t in te.toetsen() if t["soort"] == "oefen"}
    for o in opd.alle():
        if o["vak"] != vak or o["sector"] != sector or o["status"] != "klaar":
            continue
        exams = o.get("uitvoer", {}).get("oefenexamens", [])
        tids = [t for ex in exams for t in gescand if t.startswith(ex["code"] + "_OE_")]
        if not tids:
            continue
        ids = list(dict.fromkeys(o["toets_ids"] + tids))
        prof = te.profielen(ids, set(o["leerling_ids"]) & (leerlingen or set(o["leerling_ids"])))
        zc = set(zelfcheck.laad(o["id"]))
        per_code = defaultdict(lambda: {"voor": [], "na": []})
        groepen = {"met resultaatcode": {"voor": [], "na": []}, "zonder resultaatcode": {"voor": [], "na": []}}
        for lid, p in prof.items():
            for d in p["leerdoelen"]:
                na = [m for m in d["metingen"] if m["toets_id"] in tids]
                voor = [m for m in d["metingen"] if m["toets_id"] not in tids and (m["datum"] or "") <= o["gemaakt"][:10]]
                if na and voor:
                    per_code[d["code"]]["voor"].append(voor[-1]["pct"])
                    per_code[d["code"]]["na"].append(na[-1]["pct"])
                    g = groepen["met resultaatcode" if lid in zc else "zonder resultaatcode"]
                    g["voor"].append(voor[-1]["pct"])
                    g["na"].append(na[-1]["pct"])
        gem = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
        codes = [{"code": c, "voor": gem(v["voor"]), "na": gem(v["na"]), "n": len(v["na"])} for c, v in sorted(per_code.items())]
        alle_v = [x for v in per_code.values() for x in v["voor"]]
        alle_n = [x for v in per_code.values() for x in v["na"]]
        if not alle_n:
            continue
        uit.append({"id": o["id"], "naam": o["naam"], "voor": gem(alle_v), "na": gem(alle_n), "n": len(alle_n),
                    "codes": codes, "groepen": {k: {"voor": gem(v["voor"]), "na": gem(v["na"]), "n": len(v["na"])}
                                                for k, v in groepen.items() if v["na"]}})
    return uit


# ---------------------------------------------------------------------------- leerlingprofiel
def leerling(lid: str) -> dict | None:
    toetsen = te.toetsen()
    ids = [t["toets_id"] for t in toetsen]
    prof = te.profielen(ids, {lid})
    p = prof.get(lid)
    if not p:
        return None
    reg = te.alle_codes()
    toets_info = {t["toets_id"]: t for t in toetsen}
    gebruikt = sorted({m["toets_id"] for d in p["leerdoelen"] for m in d["metingen"]},
                      key=lambda t: (toets_info.get(t, {}).get("datum") or "", t))
    vakken = sorted({toets_info[t]["vak"] for t in gebruikt if t in toets_info})
    doelen, gaten, gaten_laatst = [], Counter(), Counter()
    for d in sorted(p["leerdoelen"], key=lambda d: (reg.get(d["code"], {}).get("vak", ""), d.get("thema", ""), d["code"])):
        per_toets = {m["toets_id"]: m for m in d["metingen"]}
        m = d["metingen"]
        doelen.append({"code": d["code"], "omschrijving": d["omschrijving"], "zin": d["leerling_zin"], "thema": d["thema"],
                       "vak": reg.get(d["code"], {}).get("vak", ""), "status": d["status"], "pct": d["laatste_pct"],
                       "per_toets": per_toets, "groei": (m[-1]["pct"] - m[0]["pct"]) if len(m) > 1 else None,
                       "wisselend": d["wisselend"], "teruggevallen": d["teruggevallen"],
                       "eindtermen": d["eindtermen"]})
        for g in d["leergaten"]:
            if g["leergat"]:
                gaten[g["leergat"]] += g["punten_verloren"]
                if g["toets_id"] == m[-1]["toets_id"]:
                    gaten_laatst[g["leergat"]] += g["punten_verloren"]
    oefenen = []
    for o in opd.alle():
        if lid not in o.get("leerling_ids", []):
            continue
        s = next((s for s in o["sets"] if lid in s["leerlingen"]), None)
        ex = next((e for e in o.get("uitvoer", {}).get("oefenexamens", []) if lid in e["leerlingen"]), None)
        zc = zelfcheck.laad(o["id"]).get(lid, [])
        oefenen.append({"id": o["id"], "naam": o["naam"], "datum": o["gemaakt"][:10], "status": o["status"],
                        "set": s["naam"] if s else "", "opgaven": len(s.get("items", [])) if s else 0,
                        "code": (o.get("codes_leerling") or {}).get(lid, ""),
                        "oefentoets": ex["code"] if ex else "", "zelfcheck": zc[-1] if zc else None, "keer": len(zc)})
    tel = Counter(d["status"] for d in doelen)
    return {"lid": lid, "klas": p["klas"], "niveau": p["niveau"], "vakken": vakken, "doelen": doelen,
            "toetsen": [{"toets_id": t, "datum": toets_info.get(t, {}).get("datum", ""), "soort": te.soort_van(t)} for t in gebruikt],
            "tel": tel, "gaten": dict(gaten.most_common()), "gaten_laatst": dict(gaten_laatst.most_common()),
            "oefenen": oefenen, "leergaten_namen": LEERGATEN,
            "groei": [d["groei"] for d in doelen if d["groei"] is not None]}


def svg_trend(trend: list[dict], breed: int = 640, hoog: int = 180) -> str:
    """Kleine lijngrafiek (inline SVG) van het aandeel beheerst na elke toetsdatum."""
    if len(trend) < 1:
        return ""
    l, r, b, o = 42, 14, 12, 30
    w, h = breed - l - r, hoog - b - o
    n = len(trend)
    xs = [l + (w * i / (n - 1) if n > 1 else w / 2) for i in range(n)]
    ys = [b + h * (1 - t["pct"]) for t in trend]
    punten = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    delen = [f'<svg viewBox="0 0 {breed} {hoog}" role="img" aria-label="Aandeel leerdoelen beheerst per toetsdatum" class="trend">']
    for v in (0, .5, .8, 1):
        y = b + h * (1 - v)
        delen.append(f'<line x1="{l}" x2="{breed - r}" y1="{y:.1f}" y2="{y:.1f}" class="{"rooster80" if v == .8 else "rooster"}"/>'
                     f'<text x="{l - 6}" y="{y + 4:.1f}" text-anchor="end" class="as">{int(v * 100)}%</text>')
    if n > 1:
        delen.append(f'<polyline points="{punten}" class="lijn"/>')
    for x, y, t in zip(xs, ys, trend):
        titel = f"{t['datum']}: {round(t['pct'] * 100)}% beheerst ({', '.join(t['toetsen'])})"
        delen.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5 if t["oefen"] else 4}" class="{"punt oefen" if t["oefen"] else "punt"}">'
                     f'<title>{titel}</title></circle>')
        delen.append(f'<text x="{x:.1f}" y="{hoog - 10}" text-anchor="middle" class="as">{t["datum"][8:10]}-{t["datum"][5:7]}</text>')
    delen.append("</svg>")
    return "".join(delen)
