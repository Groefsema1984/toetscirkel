# -*- coding: utf-8 -*-
"""Leerprofiel per leerling en de export voor de oefentool.

Regels (staan ook in LEESMIJ.md van elke export):
- Een meting = alle vragen van één toets die bij hetzelfde leerdoel horen, samengeteld.
- Status van een meting: beheerst vanaf 80% van de punten, bijna vanaf 50%, anders nog niet.
- De laatste meting (op afnamedatum) bepaalt de huidige status. Ging het eerder niet goed en nu
  wel, dan staat 'wisselend' aan; ging het eerder goed en nu niet, dan 'teruggevallen'.
- Vragen zonder leerdoelcode komen apart in de export ('ongekoppeld'), zodat er niets verloren gaat.
- Er staan geen namen in de export: alleen leerlingnummers. Namen staan lokaal in de klassenlijst.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import register
from config import BASIS, LEERGAT_LEERLING, kern, soort_van

VERSIE = 1
BEHEERST, BIJNA = 0.8, 0.5
EXPORTS = BASIS / "Exports"


def _status(pct: float) -> str:
    return "beheerst" if pct >= BEHEERST - 1e-9 else ("bijna" if pct >= BIJNA - 1e-9 else "nog_niet")


def rijen(toets_ids: list[str]) -> list[dict]:
    if not toets_ids:
        return []
    con = kern.open_db()
    try:
        vt = ",".join("?" * len(toets_ids))
        uit = con.execute(
            "SELECT e.leerling_id, l.klas, l.niveau, m.toets_id, t.datum, t.vak, m.vraagnummer,"
            " m.max_score, e.behaalde_score AS score, e.type_leergat AS leergat,"
            " e.foutomschrijving AS omschrijving, m.leerdoel_code, m.leerdoel, m.leerdoel_leerling,"
            " m.domein_eindterm, m.bloom_niveau, m.onderwerp, m.tweefasen_label, m.koppel_status"
            " FROM evaluatieresultaten e JOIN toetsmatrijs m ON m.item_id = e.item_id"
            " JOIN toetsen t ON t.toets_id = m.toets_id"
            " LEFT JOIN leerlingen l ON l.leerling_id = e.leerling_id"
            f" WHERE m.toets_id IN ({vt}) ORDER BY t.datum, m.toets_id, m.vraagnummer",
            toets_ids).fetchall()
        return [dict(r) for r in uit]
    finally:
        con.close()


def profielen(rij_lijst: list[dict], codes: set[str] | None = None) -> dict[str, dict]:
    """Leerprofiel per leerlingnummer."""
    reg = register.alle_codes()
    per_ll = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    klas = {}
    for r in rij_lijst:
        sleutel = r["leerdoel_code"] or f"ONGEKOPPELD:{r['toets_id']}:{r['vraagnummer']}"
        if codes and sleutel not in codes:
            continue
        per_ll[r["leerling_id"]][sleutel][(r["datum"] or "", r["toets_id"])].append(r)
        klas[r["leerling_id"]] = (r["klas"] or "", r["niveau"] or "")

    uit = {}
    for lid, per_doel in per_ll.items():
        doelen = []
        for sleutel, per_toets in per_doel.items():
            metingen, leergaten = [], []
            for (datum, toets_id), vragen in sorted(per_toets.items()):
                behaald = sum(v["score"] for v in vragen)
                maximaal = sum(v["max_score"] for v in vragen)
                pct = behaald / maximaal if maximaal else 0.0
                metingen.append({"toets_id": toets_id, "datum": datum, "soort": soort_van(toets_id),
                                 "vragen": [v["vraagnummer"] for v in vragen],
                                 "behaald": behaald, "max": maximaal, "pct": round(pct, 3),
                                 "status": _status(pct)})
                for v in vragen:
                    if v["score"] < v["max_score"]:
                        leergaten.append({"toets_id": toets_id, "vraag": v["vraagnummer"],
                                          "leergat": v["leergat"], "omschrijving": v["omschrijving"] or "",
                                          "punten_verloren": v["max_score"] - v["score"]})
            laatste = metingen[-1]
            eerder = [m["status"] for m in metingen[:-1]]
            voorbeeld = next(iter(per_toets.values()))[0]
            info = reg.get(sleutel, {})
            doelen.append({
                "code": sleutel, "gekoppeld": sleutel in reg,
                "thema": info.get("thema", ""),
                "omschrijving": info.get("omschrijving") or voorbeeld["leerdoel"] or "",
                "leerling_zin": info.get("leerling_zin") or voorbeeld["leerdoel_leerling"] or "",
                "eindtermen": sorted({v["domein_eindterm"] for vs in per_toets.values() for v in vs
                                      if v["domein_eindterm"]}),
                "bloom": sorted({v["bloom_niveau"] for vs in per_toets.values() for v in vs
                                 if v["bloom_niveau"]}),
                "status": laatste["status"], "laatste_pct": laatste["pct"],
                "wisselend": laatste["status"] == "beheerst" and any(e != "beheerst" for e in eerder),
                "teruggevallen": laatste["status"] != "beheerst" and "beheerst" in eerder,
                "aantal_metingen": len(metingen), "metingen": metingen, "leergaten": leergaten,
            })
        for d in doelen:   # prioriteit: hoe ver van beheerst, vaker getoetst = belangrijker
            d["prioriteit"] = round((1 - d["laatste_pct"]) * (1 + 0.25 * (d["aantal_metingen"] - 1))
                                    + (0.3 if d["wisselend"] or d["teruggevallen"] else 0), 3) \
                if d["status"] != "beheerst" or d["wisselend"] else 0.0
        doelen.sort(key=lambda d: (-d["prioriteit"], d["code"]))
        tel = defaultdict(int)
        for d in doelen:
            tel[d["status"]] += 1
        per_gat = defaultdict(float)
        for d in doelen:
            for g in d["leergaten"]:
                per_gat[g["leergat"] or "onbekend"] += g["punten_verloren"]
        uit[lid] = {
            "leerling_id": lid, "klas": klas[lid][0], "niveau": klas[lid][1],
            "samenvatting": {"beheerst": tel["beheerst"], "bijna": tel["bijna"],
                             "nog_niet": tel["nog_niet"],
                             "wisselend": sum(1 for d in doelen if d["wisselend"]),
                             "teruggevallen": sum(1 for d in doelen if d["teruggevallen"]),
                             "punten_verloren_per_leergat": dict(sorted(per_gat.items(),
                                                                        key=lambda kv: -kv[1]))},
            "prioriteit": [d["code"] for d in doelen if d["prioriteit"] > 0][:3],
            "leerdoelen": doelen,
        }
    return uit


def groepen(profiel: dict[str, dict]) -> dict:
    per_doel = defaultdict(lambda: {"nog_niet": [], "bijna": [], "wisselend": []})
    per_gat = defaultdict(set)
    for lid, p in profiel.items():
        for d in p["leerdoelen"]:
            if d["status"] in ("nog_niet", "bijna"):
                per_doel[d["code"]][d["status"]].append(lid)
                for g in d["leergaten"]:
                    if g["leergat"] and g["toets_id"] == d["metingen"][-1]["toets_id"]:
                        per_gat[(d["code"], g["leergat"])].add(lid)
            elif d["wisselend"]:
                per_doel[d["code"]]["wisselend"].append(lid)
    return {
        "per_leerdoel": [dict(code=c, **{k: sorted(v) for k, v in g.items()},
                              aantal=len(g["nog_niet"]) + len(g["bijna"]))
                         for c, g in sorted(per_doel.items(),
                                            key=lambda kv: -(len(kv[1]["nog_niet"]) + len(kv[1]["bijna"])))],
        "per_leerdoel_en_leergat": [{"code": c, "leergat": gat, "leerlingen": sorted(ids)}
                                    for (c, gat), ids in sorted(per_gat.items(), key=lambda kv: -len(kv[1]))
                                    if len(ids) >= 2],
    }


def bouw_export(toets_ids: list[str], leerling_ids: set[str] | None = None,
                codes: set[str] | None = None, label: str = "") -> Path:
    """Schrijft een export-map in Exports/ en een zip ernaast. Geeft het pad van de map."""
    alle = rijen(toets_ids)
    if leerling_ids:
        alle = [r for r in alle if r["leerling_id"] in leerling_ids]
    if not alle:
        raise ValueError("Voor deze keuze zijn er geen resultaten.")
    profiel = profielen(alle, codes)
    reg = register.alle_codes()
    gebruikte = sorted({d["code"] for p in profiel.values() for d in p["leerdoelen"]})
    toetsen = {}
    for r in alle:
        t = toetsen.setdefault(r["toets_id"], {"toets_id": r["toets_id"], "vak": r["vak"],
                                               "soort": soort_van(r["toets_id"]),
                                               "datum": r["datum"], "vragen": set()})
        t["vragen"].add(r["vraagnummer"])
    meta = {
        "formaat": "toetsevaluatie-leerprofiel", "versie": VERSIE,
        "gemaakt": datetime.now().isoformat(timespec="seconds"),
        "filters": {"toetsen": sorted(toets_ids), "leerlingen": sorted(leerling_ids or []),
                    "leerdoelen": sorted(codes or []), "label": label},
        "drempels": {"beheerst": BEHEERST, "bijna": BIJNA, "regel": "laatste meting telt"},
        "leergaten": {c: {"naam": kern.LEERGATEN[c], "leerlingtaal": LEERGAT_LEERLING[c][1],
                          "oefentip": LEERGAT_LEERLING[c][2]} for c in kern.LEERGATEN},
        "toetsen": [dict(t, vragen=len(t["vragen"])) for t in sorted(toetsen.values(),
                                                                      key=lambda t: (t["datum"] or "", t["toets_id"]))],
        "leerdoelen": [{k: reg[c][k] for k in ("code", "vak", "sector", "thema", "omschrijving",
                                                "leerling_zin", "status")} for c in gebruikte if c in reg],
        "ongekoppeld": sum(1 for c in gebruikte if c not in reg),
    }
    stempel = datetime.now().strftime("%Y%m%d_%H%M")
    schoon = re.sub(r"[^A-Za-z0-9._-]+", "_", label.strip())[:40].strip("_")
    doel = EXPORTS / (stempel + (f"_{schoon}" if schoon else ""))
    if doel.exists():
        doel = Path(f"{doel}_{datetime.now():%S}")
    (doel / "leerlingen").mkdir(parents=True)
    (doel / "export.json").write_text(json.dumps(
        dict(meta, leerlingen=sorted(profiel.values(), key=lambda p: p["leerling_id"]),
             groepen=groepen(profiel)), ensure_ascii=False, indent=1), encoding="utf-8")
    for lid, p in profiel.items():
        eigen = {d["code"] for d in p["leerdoelen"]}
        (doel / "leerlingen" / f"{lid}.json").write_text(json.dumps(
            dict(meta, leerdoelen=[l for l in meta["leerdoelen"] if l["code"] in eigen], leerling=p),
            ensure_ascii=False, indent=1), encoding="utf-8")
    (doel / "LEESMIJ.md").write_text(LEESMIJ, encoding="utf-8")
    shutil.make_archive(str(doel), "zip", root_dir=doel)
    return doel


def exports() -> list[dict]:
    if not EXPORTS.is_dir():
        return []
    uit = []
    for pad in sorted(EXPORTS.iterdir(), reverse=True):
        if pad.is_dir() and (pad / "export.json").exists():
            try:
                meta = json.loads((pad / "export.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            uit.append({"naam": pad.name, "pad": str(pad), "gemaakt": meta.get("gemaakt", ""),
                        "toetsen": len(meta.get("toetsen", [])), "leerlingen": len(meta.get("leerlingen", [])),
                        "label": meta.get("filters", {}).get("label", "")})
    return uit[:20]


LEESMIJ = """# Export leerprofielen (formaat toetsevaluatie-leerprofiel, versie 1)

Gemaakt door de webapp Toetsevaluatie. Bedoeld als invoer voor de oefentool.

## Bestanden
- `export.json`: alles in één bestand: leerprofielen van alle leerlingen plus groepen.
- `leerlingen/<leerlingnummer>.json`: hetzelfde profiel per leerling (met de gebruikte leerdoelen).

## Privacy
Er staan geen namen in deze export, alleen leerlingnummers. Namen staan lokaal in
`Klassenlijsten/`. Koppel ze pas bij het printen en stuur ze nooit mee naar een AI-dienst.

## Regels
- Leerdoelcode (bijv. `ECO-KRING-01`) komt uit het leerdoelenregister. Havo en vwo delen
  één register; mavo heeft een eigen register (`ECO-M-...`).
- Een **meting** = alle vragen van één toets bij hetzelfde leerdoel, samengeteld.
- Status van een meting: `beheerst` vanaf 80% van de punten, `bijna` vanaf 50%, anders `nog_niet`.
- De **laatste meting** (op afnamedatum) is de huidige `status`.
  - `wisselend`: nu beheerst, maar eerder niet: kort herhalen.
  - `teruggevallen`: eerder beheerst, nu niet.
- `prioriteit` (per leerdoel) = (1 - laatste percentage) x (1 + 0,25 per extra meting),
  plus 0,3 bij wisselend of teruggevallen. Het profiel noemt de top 3 als `prioriteit`.
- Elke meting heeft een `soort`: `toets` (schoolexamen of toets) of `oefen` (oefentoets uit de
  oefentool, toetsnaam met `_OE_`). Een oefenmeting telt gewoon mee als laatste meting.
- Vragen zonder leerdoelcode staan als `ONGEKOPPELD:<toets>:<vraag>`; koppel ze in de app via
  Leerdoelen en maak de export opnieuw.

## Belangrijkste velden per leerling
- `samenvatting`: aantallen per status en verloren punten per leergat.
- `leerdoelen[]`: `code`, `omschrijving` (docent), `leerling_zin` ("Ik kan ..."), `thema`,
  `eindtermen`, `bloom`, `status`, `laatste_pct`, `wisselend`, `teruggevallen`,
  `metingen[]` (toets, datum, vragen, behaald, max, pct, status) en
  `leergaten[]` (toets, vraag, leergat KD/PR/RF/LI/DW/MC, omschrijving van de fout,
  punten_verloren).

## Groepen (alleen in export.json)
- `groepen.per_leerdoel[]`: per leerdoel wie `nog_niet`, `bijna` of `wisselend` is.
- `groepen.per_leerdoel_en_leergat[]`: leerlingen met hetzelfde leerdoel en hetzelfde leergat
  in hun laatste meting (minimaal twee): geschikt voor een groepsset oefenmateriaal.
"""
