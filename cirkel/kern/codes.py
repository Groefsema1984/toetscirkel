# -*- coding: utf-8 -*-
"""Leerdoelcodes aanvullen in bestaande matrijzen van Toetsconstructie, uit wat bij het nakijken is vastgesteld.

Alleen voor toetsprojecten die aan een nagekeken toets zijn gekoppeld (toets_koppeling, zie itembank.py). Per vraag
wordt de code overgenomen uit de toetsmatrijs van de evaluatie, maar alleen als die koppeling daar 'bevestigd' of
'auto' is en de code in het register staat. Er wordt niets geraden en een code die al in de matrijs staat blijft staan.
Het gebouwde correctievoorschrift (Word) wordt niet aangeraakt.

Een gewijzigde matrijs krijgt in toetsen/<id>/versies/ een kopie met '_toetscirkel_' in de naam, zodat de app (en
Claude bij het afronden) ziet dat deze wijziging niet van de docent kwam.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import database
from .database import paden

ZEKER = ("bevestigd", "auto")


def codes_uit_evaluatie(con, toets_id: str) -> dict[int, str]:
    kolommen = {r[1] for r in con.execute("PRAGMA table_info(toetsmatrijs)")}
    if not {"leerdoel_code", "koppel_status"} <= kolommen:
        return {}
    heeft_register = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'leerdoelen_register'").fetchone()
    sql = ("SELECT m.vraagnummer, m.leerdoel_code FROM toetsmatrijs m "
           + ("JOIN leerdoelen_register r ON r.code = m.leerdoel_code AND r.status != 'vervallen' " if heeft_register else "")
           + f"WHERE m.toets_id = ? AND m.leerdoel_code IS NOT NULL AND m.koppel_status IN ({', '.join('?' * len(ZEKER))})")
    return {int(nr): code for nr, code in con.execute(sql, (toets_id, *ZEKER)) if nr is not None}


def vul_aan(toetsen_map: Path | None = None, pad: Path | None = None) -> dict:
    """Vul ontbrekende leerdoelcodes aan. Geeft {'projecten': n, 'vragen': n, 'per_project': {...}} terug."""
    toetsen_map = toetsen_map or paden.TOETSCONSTRUCTIE / "toetsen"
    con = database.verbind(pad)
    uit = {"projecten": 0, "vragen": 0, "per_project": {}}
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'toets_koppeling'").fetchone():
            return uit
        koppelingen = con.execute("SELECT project, toets_id FROM toets_koppeling ORDER BY zekerheid = 'afname' DESC").fetchall()
        gedaan = set()
        for project, toets_id in koppelingen:
            if project in gedaan:
                continue
            gedaan.add(project)
            mpad = toetsen_map / project / "matrijs.json"
            if not mpad.exists():
                continue
            codes = codes_uit_evaluatie(con, toets_id)
            if not codes:
                continue
            try:
                matrijs = json.loads(mpad.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            n = 0
            for rij in matrijs.get("rijen", []):
                try:
                    nr = int(rij.get("nr"))
                except (TypeError, ValueError):
                    continue
                if not rij.get("leerdoel_code") and nr in codes:
                    rij["leerdoel_code"] = codes[nr]
                    n += 1
            if n:
                tekst = json.dumps(matrijs, ensure_ascii=False, indent=1)
                mpad.write_text(tekst, encoding="utf-8")
                versies = mpad.parent / "versies"
                versies.mkdir(exist_ok=True)
                (versies / f"{datetime.now():%Y%m%d_%H%M%S}_toetscirkel_matrijs.json").write_text(tekst, encoding="utf-8")
                uit["projecten"] += 1
                uit["vragen"] += n
                uit["per_project"][project] = n
    finally:
        con.close()
    return uit


# ------------------------------------------------------------------------------------------ met Claude
SCHEMA_KOPPEL = {
    "type": "object", "required": ["vragen"],
    "properties": {"vragen": {"type": "array", "items": {
        "type": "object", "required": ["nr", "code", "zekerheid", "toelichting"],
        "properties": {"nr": {"type": "integer"}, "code": {"type": "string"},
                       "zekerheid": {"type": "string", "enum": ["hoog", "middel", "laag"]},
                       "toelichting": {"type": "string"}}}}}}

SYSTEEM = ("Je koppelt toetsvragen aan een vast leerdoelenregister van een docent economie. Een code past alleen als de "
           "omschrijving in het register precies de vaardigheid van de vraag beschrijft; een andere context is geen "
           "bezwaar. Twijfel je, geef dan zekerheid middel of laag. Past geen enkele code, geef dan code \"GEEN\". "
           "Verzin nooit een code.")

TWIJFEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS codes_twijfel (
    project TEXT NOT NULL, nr INTEGER NOT NULL, leerdoel TEXT, voorstel TEXT, zekerheid TEXT, toelichting TEXT,
    moment TEXT, PRIMARY KEY (project, nr)
);
"""


def _sleutel(rijen: list[dict]) -> str:
    import hashlib
    return hashlib.sha256(json.dumps([[r.get("nr"), r.get("leerdoel"), r.get("leerdoel_code")] for r in rijen],
                                     ensure_ascii=False).encode()).hexdigest()[:16]


def _register(con, vak: str, niveau: str) -> list[dict]:
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'leerdoelen_register'").fetchone():
        return []
    sector = "mavo" if (niveau or "").lower() in ("mavo", "vmbo") else "havo-vwo"
    return [dict(r) for r in con.execute("SELECT code, thema, omschrijving FROM leerdoelen_register WHERE vak = ? AND "
                                         "sector = ? AND status != 'vervallen' ORDER BY thema, code", (vak, sector))]


def koppel_met_claude(vraag_json, toetsen_map: Path | None = None, pad: Path | None = None, voortgang=None) -> dict:
    """Laat Claude de matrijzen zonder (volledige) codes naast het register leggen. Alleen zekerheid 'hoog' wordt
    ingevuld; de rest komt in codes_twijfel voor de docent. vraag_json(systeem, inhoud, schema, max_tokens) -> dict.
    Een matrijs die al eens zo is bekeken en sindsdien niet is veranderd, wordt overgeslagen."""
    toetsen_map = toetsen_map or paden.TOETSCONSTRUCTIE / "toetsen"
    uit = {"projecten": 0, "vragen": 0, "twijfel": 0, "fout": ""}
    con = database.verbind(pad)
    con.isolation_level = None
    try:
        con.executescript(TWIJFEL_SCHEMA)
        for pj in sorted(toetsen_map.glob("*/project.json")):
            try:
                project = json.loads(pj.read_text(encoding="utf-8"))
                mpad = pj.parent / "matrijs.json"
                matrijs = json.loads(mpad.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            pid = project.get("id") or pj.parent.name
            rijen = matrijs.get("rijen", [])
            open_ = [r for r in rijen if not r.get("leerdoel_code") and r.get("leerdoel")]
            if not open_ or database.meta(con, f"codes_claude:{pid}") == _sleutel(rijen):
                continue
            reg = _register(con, project.get("vak"), project.get("niveau"))
            if not reg:
                continue
            if voortgang:
                voortgang(project.get("naam") or pid)
            bekend = {r["code"] for r in reg}
            opdracht = ("Register (code | thema | leerdoel):\n" + "\n".join(f"{r['code']} | {r['thema']} | {r['omschrijving']}" for r in reg)
                        + "\n\nVragen (nr | onderdeel | leerdoel | eindterm):\n"
                        + "\n".join(f"{r.get('nr')} | {r.get('onderdeel') or ''} | {r.get('leerdoel')} | {r.get('eindterm') or ''}" for r in open_))
            antwoord = vraag_json([{"type": "text", "text": SYSTEEM}], [{"type": "text", "text": opdracht}], SCHEMA_KOPPEL, 8000)
            per_nr = {v.get("nr"): v for v in antwoord.get("vragen", [])}
            n = 0
            con.execute("DELETE FROM codes_twijfel WHERE project = ?", (pid,))
            for r in open_:
                v = per_nr.get(r.get("nr"))
                if not v:
                    continue
                code = (v.get("code") or "").strip()
                if code in bekend and v.get("zekerheid") == "hoog":
                    r["leerdoel_code"] = code
                    n += 1
                else:
                    con.execute("INSERT OR REPLACE INTO codes_twijfel VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (pid, r.get("nr"), r.get("leerdoel"), code if code in bekend else "",
                                 v.get("zekerheid"), v.get("toelichting"), datetime.now().isoformat(timespec="seconds")))
                    uit["twijfel"] += 1
            if n:
                tekst = json.dumps(matrijs, ensure_ascii=False, indent=1)
                mpad.write_text(tekst, encoding="utf-8")
                versies = mpad.parent / "versies"
                versies.mkdir(exist_ok=True)
                (versies / f"{datetime.now():%Y%m%d_%H%M%S}_toetscirkel_matrijs.json").write_text(tekst, encoding="utf-8")
                uit["projecten"] += 1
                uit["vragen"] += n
            database.zet_meta(con, f"codes_claude:{pid}", _sleutel(rijen))
    finally:
        con.close()
    return uit


def twijfelgevallen(pad: Path | None = None) -> list[dict]:
    con = database.verbind(pad)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'codes_twijfel'").fetchone():
            return []
        return [dict(r) for r in con.execute("SELECT * FROM codes_twijfel ORDER BY project, nr")]
    finally:
        con.close()
