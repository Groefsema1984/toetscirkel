#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""De "gouden toets": een afgeronde toets waarvan we de uitkomsten vastleggen, zodat we na elke
stap van de samenvoeging kunnen controleren dat er niets veranderd is.

Wat wordt vastgelegd (alleen samenvattingen, geen namen):
- de toetsmatrijs (vraag, max, Bloom, label, eindterm, leerdoelcode);
- aantal leerlingen, totaal en leergaten per vraag;
- de itemanalyse van Toetsconstructie (p, rit, rir, alfa);
- de cijfers uit cijferoverzicht (gesorteerd, zonder leerlingnummer);
- per leerdoel hoeveel leerlingen beheerst / bijna / nog niet scoren (regels van leerprofiel.py);
- een vingerafdruk (SHA-256) van alle scores per leerling en vraag.

De momentopname komt in ~/Documents/ToetsCirkel_data/gouden/<toets_id>.json: lokaal, nooit in Git.

Gebruik:
  python3 tests/gouden_toets.py lijst                     welke toetsen staan in de database
  python3 tests/gouden_toets.py vastleggen <toets_id>     momentopname maken (één keer)
  python3 tests/gouden_toets.py controleren <toets_id>    opnieuw berekenen en vergelijken
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import paden  # noqa: E402

BEHEERST, BIJNA = 0.8, 0.5          # zelfde grenzen als Toetsevaluatie/webapp/leerprofiel.py
TOLERANTIE = 1e-6


def _db() -> sqlite3.Connection:
    pad = paden.DATABASES["toetsevaluatie.db"]
    if not pad.exists():
        sys.exit(f"Database niet gevonden: {pad}")
    con = sqlite3.connect(f"file:{pad}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _itemanalyse_module():
    pad = paden.TOETSCONSTRUCTIE / "bouw" / "itemanalyse.py"
    if not pad.exists():
        pad = Path(__file__).resolve().parent.parent / "apps" / "Toetsconstructie" / "bouw" / "itemanalyse.py"
    spec = importlib.util.spec_from_file_location("itemanalyse", pad)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _kolommen(con, tabel: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({tabel})")}


def _status(pct: float) -> str:
    return "beheerst" if pct >= BEHEERST - 1e-9 else ("bijna" if pct >= BIJNA - 1e-9 else "nog_niet")


def momentopname(toets_id: str) -> dict:
    con = _db()
    try:
        kol = _kolommen(con, "toetsmatrijs")
        code = "leerdoel_code" if "leerdoel_code" in kol else "NULL"
        matrijs = [dict(r) for r in con.execute(
            f"SELECT vraagnummer AS nr, max_score AS max, bloom_niveau AS bloom, tweefasen_label AS label, "
            f"domein_eindterm AS eindterm, {code} AS leerdoel_code FROM toetsmatrijs WHERE toets_id = ? "
            "ORDER BY vraagnummer", (toets_id,))]
        if not matrijs:
            sys.exit(f"Toets '{toets_id}' staat niet in de database. Kies er een uit: tests/gouden_toets.py lijst")
        rijen = con.execute(
            f"SELECT e.leerling_id, m.vraagnummer AS nr, m.max_score AS max, e.behaalde_score AS score, "
            f"e.type_leergat AS leergat, {('m.' + code) if code != 'NULL' else 'NULL'} AS leerdoel_code "
            "FROM evaluatieresultaten e JOIN toetsmatrijs m ON m.item_id = e.item_id "
            "WHERE m.toets_id = ? ORDER BY e.leerling_id, m.vraagnummer", (toets_id,)).fetchall()
        cijfers = []
        if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'cijferoverzicht'").fetchone():
            cijfers = sorted(r[0] for r in con.execute(
                "SELECT behaald_cijfer FROM cijferoverzicht WHERE toets_id = ?", (toets_id,)) if r[0] is not None)
    finally:
        con.close()

    leerlingen = sorted({r["leerling_id"] for r in rijen})
    per_vraag = defaultdict(lambda: {"totaal": 0.0, "leergaten": Counter()})
    for r in rijen:
        per_vraag[r["nr"]]["totaal"] += float(r["score"] or 0)
        if r["leergat"]:
            per_vraag[r["nr"]]["leergaten"][r["leergat"]] += 1

    per_doel = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for r in rijen:
        sleutel = r["leerdoel_code"] or f"ONGEKOPPELD:{r['nr']}"
        per_doel[sleutel][r["leerling_id"]][0] += float(r["score"] or 0)
        per_doel[sleutel][r["leerling_id"]][1] += float(r["max"] or 0)
    metingen = {doel: dict(Counter(_status(b / m if m else 0.0) for b, m in ll.values()))
                for doel, ll in sorted(per_doel.items())}

    vinger = hashlib.sha256(json.dumps(
        [[r["leerling_id"], r["nr"], float(r["score"] or 0), r["leergat"] or ""] for r in rijen]).encode()).hexdigest()

    ia = _itemanalyse_module().uit_db(paden.DATABASES["toetsevaluatie.db"], toets_id)
    ia_kort = {k: v for k, v in ia.items() if k not in ("gemaakt", "datum", "tijd", "bron")}

    return {
        "toets_id": toets_id,
        "matrijs": matrijs,
        "leerlingen": len(leerlingen),
        "per_vraag": {str(nr): {"totaal": round(v["totaal"], 6), "leergaten": dict(sorted(v["leergaten"].items()))}
                      for nr, v in sorted(per_vraag.items())},
        "cijfers": cijfers,
        "gemiddeld_cijfer": round(sum(cijfers) / len(cijfers), 4) if cijfers else None,
        "metingen_per_leerdoel": metingen,
        "itemanalyse": ia_kort,
        "vingerafdruk_scores": vinger,
    }


def verschillen(oud, nieuw, pad="") -> list[str]:
    if isinstance(oud, dict) and isinstance(nieuw, dict):
        uit = []
        for k in sorted(set(oud) | set(nieuw), key=str):
            if k not in nieuw:
                uit.append(f"{pad}/{k}: verdwenen")
            elif k not in oud:
                uit.append(f"{pad}/{k}: nieuw")
            else:
                uit += verschillen(oud[k], nieuw[k], f"{pad}/{k}")
        return uit
    if isinstance(oud, list) and isinstance(nieuw, list):
        if len(oud) != len(nieuw):
            return [f"{pad}: lengte {len(oud)} -> {len(nieuw)}"]
        uit = []
        for i, (a, b) in enumerate(zip(oud, nieuw)):
            uit += verschillen(a, b, f"{pad}[{i}]")
        return uit
    if isinstance(oud, (int, float)) and isinstance(nieuw, (int, float)) and not isinstance(oud, bool):
        return [] if abs(oud - nieuw) <= TOLERANTIE else [f"{pad}: {oud} -> {nieuw}"]
    return [] if oud == nieuw else [f"{pad}: {oud!r} -> {nieuw!r}"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="actie", required=True)
    sub.add_parser("lijst")
    for naam in ("vastleggen", "controleren"):
        p = sub.add_parser(naam)
        p.add_argument("toets_id")
        if naam == "vastleggen":
            p.add_argument("--overschrijven", action="store_true")
    a = ap.parse_args()

    if a.actie == "lijst":
        for t in _itemanalyse_module().db_toetsen(paden.DATABASES["toetsevaluatie.db"]):
            print(f"{t['toets_id']:<50} {t['vak'] or '':<17} {t['datum'] or '':<12} "
                  f"{t['vragen']:>3} vragen  {t['leerlingen']:>3} leerlingen")
        return 0

    bestand = paden.DATA / "gouden" / f"{a.toets_id}.json"
    nu = momentopname(a.toets_id)
    if a.actie == "vastleggen":
        if bestand.exists() and not a.overschrijven:
            sys.exit(f"Er is al een momentopname: {bestand}. Gebruik --overschrijven om hem te vervangen.")
        bestand.parent.mkdir(parents=True, exist_ok=True)
        bestand.write_text(json.dumps(nu, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Vastgelegd: {a.toets_id}  ({nu['leerlingen']} leerlingen, {len(nu['matrijs'])} vragen)\n{bestand}")
        return 0

    if not bestand.exists():
        sys.exit(f"Nog geen momentopname voor {a.toets_id}. Eerst: tests/gouden_toets.py vastleggen {a.toets_id}")
    oud = json.loads(bestand.read_text(encoding="utf-8"))
    vs = verschillen(oud, json.loads(json.dumps(nu)))
    if vs:
        print(f"AFWIJKING bij {a.toets_id}: {len(vs)} verschil(len)")
        for v in vs[:50]:
            print("  " + v)
        return 1
    print(f"OK: {a.toets_id} geeft dezelfde uitkomst als de momentopname.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
