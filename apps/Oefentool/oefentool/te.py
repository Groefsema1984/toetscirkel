# -*- coding: utf-8 -*-
"""Brug naar de Toetsevaluatie: leerprofielen, leerdoelenregister en klassenlijsten.

De oefentool leest alleen. De regels voor 'beheerst' en 'laatste meting telt' staan op één plek:
in leerprofiel.py van de Toetsevaluatie. Namen worden alleen opgehaald voor de printweergave.
"""
from __future__ import annotations

import os
import statistics
import sys
from collections import defaultdict

from .instellingen import TOETSEVALUATIE

_mod: dict = {}


class GeenToetsevaluatie(RuntimeError):
    pass


def _laad():
    if _mod:
        return _mod
    if not (TOETSEVALUATIE / "toetsevaluatie.db").exists():
        raise GeenToetsevaluatie(f"De database van de Toetsevaluatie is niet gevonden in {TOETSEVALUATIE}.")
    os.environ["TOETSEVALUATIE_MAP"] = str(TOETSEVALUATIE)
    for p in (TOETSEVALUATIE / "webapp", TOETSEVALUATIE):
        if str(p) not in sys.path:
            sys.path.append(str(p))
    import config as te_config          # noqa: E402  (webapp/config.py van de Toetsevaluatie)
    import leerprofiel                  # noqa: E402
    import register                     # noqa: E402
    register.zorg_voor_register()
    _mod.update(config=te_config, kern=te_config.kern, leerprofiel=leerprofiel, register=register)
    return _mod


def beschikbaar() -> tuple[bool, str]:
    try:
        _laad()
        return True, ""
    except Exception as fout:  # noqa: BLE001
        return False, str(fout)


def soort_van(toets_id: str) -> str:
    m = _laad()
    f = getattr(m["config"], "soort_van", None)
    if f:
        return f(toets_id)
    return "oefen" if "_OE_" in toets_id else "toets"


def info(toets_id: str):
    return _laad()["kern"].lees_toetsmap(toets_id)


def sector(niveau: str) -> str:
    return "mavo" if niveau == "mavo" else "havo-vwo"


def toetsen() -> list[dict]:
    m = _laad()
    con = m["kern"].open_db()
    try:
        rijen = [dict(r) for r in con.execute(
            "SELECT t.toets_id, t.vak, t.datum, COUNT(DISTINCT e.leerling_id) AS aantal,"
            " GROUP_CONCAT(DISTINCT l.klas) AS klassen,"
            " SUM(CASE WHEN m.leerdoel_code IS NULL THEN 1 ELSE 0 END) AS zonder_code"
            " FROM toetsen t JOIN toetsmatrijs m ON m.toets_id = t.toets_id"
            " LEFT JOIN evaluatieresultaten e ON e.item_id = m.item_id"
            " LEFT JOIN leerlingen l ON l.leerling_id = e.leerling_id"
            " GROUP BY t.toets_id ORDER BY t.datum DESC")]
    finally:
        con.close()
    for r in rijen:
        try:
            i = info(r["toets_id"])
            r.update(niveau=i.niveau, klas=i.klas, tweefasen=i.tweefasen, sector=sector(i.niveau))
        except ValueError:
            r.update(niveau="", klas="", tweefasen=False, sector="")
        r["soort"] = soort_van(r["toets_id"])
        r["klassen"] = sorted(set((r["klassen"] or "").split(",")) - {""})
    return rijen


def register_lijst(vak: str, sec: str) -> list[dict]:
    return _laad()["register"].register(vak, sec)


def alle_codes() -> dict[str, dict]:
    return _laad()["register"].alle_codes()


def profielen(toets_ids: list[str], leerling_ids: set[str] | None = None,
              codes: set[str] | None = None) -> dict[str, dict]:
    lp = _laad()["leerprofiel"]
    rijen = lp.rijen(toets_ids)
    if leerling_ids:
        rijen = [r for r in rijen if r["leerling_id"] in leerling_ids]
    return lp.profielen(rijen, codes)


def leerlingen_van(toets_ids: list[str]) -> list[dict]:
    """Leerlingnummer en klas van iedereen die een van deze toetsen heeft gemaakt."""
    m = _laad()
    if not toets_ids:
        return []
    con = m["kern"].open_db()
    try:
        vt = ",".join("?" * len(toets_ids))
        return [dict(r) for r in con.execute(
            "SELECT DISTINCT l.leerling_id, l.klas, l.niveau FROM leerlingen l"
            " JOIN evaluatieresultaten e ON e.leerling_id = l.leerling_id"
            f" JOIN toetsmatrijs m ON m.item_id = e.item_id WHERE m.toets_id IN ({vt})"
            " ORDER BY l.klas, l.leerling_id", toets_ids)]
    finally:
        con.close()


def namen(leerling_ids: list[str]) -> dict[str, str]:
    """Alleen voor de printweergave en de codelijst: namen uit de lokale klassenlijsten."""
    kern = _laad()["kern"]
    try:
        lijst, _ = kern.laad_klassenlijst(TOETSEVALUATIE / "_bestaat_niet_", None)
    except Exception:  # noqa: BLE001
        return {}
    return {lid: lijst[lid].naam for lid in leerling_ids if lid in lijst}


def vraagstatistiek(toets_id: str) -> dict[int, dict]:
    """P-waarde en Rit per vraag van een (oefen)toets, uit de resultaten in de Toetsevaluatie."""
    rijen = _laad()["leerprofiel"].rijen([toets_id])
    per_ll = defaultdict(dict)
    maxi = {}
    for r in rijen:
        per_ll[r["leerling_id"]][r["vraagnummer"]] = r["score"]
        maxi[r["vraagnummer"]] = r["max_score"]
    totaal = {lid: sum(v.values()) for lid, v in per_ll.items()}
    uit = {}
    for nr, mx in maxi.items():
        scores = [per_ll[lid].get(nr, 0) for lid in per_ll]
        tot = [totaal[lid] for lid in per_ll]
        p = statistics.mean(scores) / mx if mx and scores else None
        rit = None
        if len(scores) >= 3 and statistics.pstdev(scores) > 0 and statistics.pstdev(tot) > 0:
            rit = statistics.correlation(scores, tot)
        uit[nr] = {"p": p, "rit": rit, "n": len(scores), "max": mx}
    return uit
