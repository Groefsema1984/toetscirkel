# -*- coding: utf-8 -*-
"""Eén itembank: alle vragen van ToetsCirkel in één overzicht, in de centrale database.

Drie bronnen, elk blijft de baas over zijn eigen gegevens:
- construeren: de vragenbank van Toetsconstructie (kennisbank/vragenbank.json). Bij elke start ingelezen in de
  tabel constructie_vragen; het JSON-bestand blijft leidend.
- evalueren:   de toetsmatrijs van afgenomen toetsen, met de werkelijke p-waarde uit de resultaten.
- oefenen:     de opgaven van de Oefentool (tabel items, sinds fase 2 in de centrale database).

toets_koppeling legt vast welk toetsproject uit Toetsconstructie bij welke toets in de Toetsevaluatie hoort. Zo krijgt
een gebouwde vraag de leerdoelcode en de p-waarde van dezelfde vraag na afname.

De view itembank zet alles onder elkaar met dezelfde kolommen:
    bron, sleutel, toets_id, project, vak, niveau, vraagnr, punten, leerdoel_code, leerdoel, eindterm, bloom,
    tekst, p_waarde, rit, status
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import database
from .database import paden

KOLOMMEN = ("bron", "sleutel", "toets_id", "project", "vak", "niveau", "vraagnr", "punten", "leerdoel_code", "leerdoel",
            "eindterm", "bloom", "tekst", "p_waarde", "rit", "status")

SCHEMA = """
CREATE TABLE IF NOT EXISTS constructie_vragen (
    vraag_id TEXT PRIMARY KEY,          -- <project>:<versie>:<nr>, zoals in vragenbank.json
    project TEXT, naam TEXT, vak TEXT, niveau TEXT, klas TEXT, at TEXT, versie TEXT, nr INTEGER, punten REAL,
    opgave TEXT, thema TEXT, onderdeel TEXT, leerdoel TEXT, leerdoel_code TEXT, eindterm TEXT, bloom TEXT,
    werkwoord TEXT, label TEXT, koppeling TEXT, vraagmodel TEXT, p_verwacht REAL, vraag TEXT,
    laatste_afname TEXT, p_waarde REAL, rit REAL, n INTEGER
);
CREATE INDEX IF NOT EXISTS cv_project ON constructie_vragen(project, versie, nr);
CREATE TABLE IF NOT EXISTS toets_koppeling (
    project TEXT NOT NULL,              -- map in Toetsconstructie/toetsen (of project-id in de vragenbank)
    toets_id TEXT NOT NULL,             -- toets in de Toetsevaluatie, bijv. AT5.11_SE_5H_Economie
    zekerheid TEXT NOT NULL,            -- 'afname' (uit de itemanalyse), 'naam' (AT-nummer, vak, niveau, soort), 'docent'
    PRIMARY KEY (project, toets_id)
);
"""


# ------------------------------------------------------------------------------------------- hulpjes
def vak_niveau_van_toets(toets_id: str) -> tuple[str | None, str | None]:
    """Vak en niveau uit een toetsnaam als AT5.11_SE_5H_Economie (zelfde regels als de Toetsevaluatie)."""
    klein = toets_id.lower()
    vak = ("Bedrijfseconomie" if "bedrijfseconomie" in klein or re.search(r"(^|[_\-\s])(beco|be)($|[_\-\s])", klein)
           else "Economie" if "economie" in klein or re.search(r"(^|[_\-\s])(eco|ec)($|[_\-\s])", klein) else None)
    niveau = None
    for deel in re.split(r"[_\-\s]+", toets_id):
        d = deel.lower()
        if "mavo" in d or "vmbo" in d or re.fullmatch(r"\d(m|tl|gt)|(m|tl|gt)\d", d):
            niveau = "mavo"
        elif "vwo" in d or re.fullmatch(r"\dv|v\d|\d(ath|gym)", d):
            niveau = "vwo"
        elif "havo" in d or re.fullmatch(r"\dh|h\d", d):
            niveau = "havo"
        if niveau:
            break
    if "tweefasen" in klein:
        niveau = "vwo"
    return vak, niveau


def _soortcode(soort: str | None) -> str:
    s = (soort or "").lower()
    return "OE" if s.startswith("oefen") else "SE"


def _kolommen(con: sqlite3.Connection, tabel: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({tabel})")}


def _laatste_afname(v: dict) -> dict:
    a = v.get("afnames") or []
    return a[-1] if a else {}


# ------------------------------------------------------------------------------------------- bijwerken
def lees_vragenbank(con: sqlite3.Connection, bestand: Path) -> int:
    """Lees vragenbank.json in constructie_vragen (volledig vervangen; het JSON-bestand is leidend)."""
    try:
        vragen = json.loads(bestand.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    bestaande_codes = dict(con.execute("SELECT vraag_id, leerdoel_code FROM constructie_vragen "
                                       "WHERE leerdoel_code IS NOT NULL").fetchall())
    rijen = []
    for v in vragen if isinstance(vragen, list) else []:
        a = _laatste_afname(v)
        rijen.append((v.get("id"), v.get("project"), v.get("naam"), v.get("vak"), (v.get("niveau") or "").lower() or None,
                      v.get("klas"), v.get("at"), v.get("versie"), v.get("nr"), v.get("punten"), v.get("opgave"),
                      v.get("thema"), v.get("onderdeel"), v.get("leerdoel"),
                      v.get("leerdoel_code") or bestaande_codes.get(v.get("id")),
                      v.get("eindterm"), v.get("bloom"), v.get("werkwoord"), v.get("label"), v.get("koppeling"),
                      v.get("vraagmodel"), v.get("p_verwacht"), v.get("vraag"), a.get("bron"), a.get("p"), a.get("rit"),
                      a.get("n")))
    con.execute("DELETE FROM constructie_vragen")
    con.executemany(f"INSERT OR REPLACE INTO constructie_vragen VALUES ({', '.join('?' * 27)})", rijen)
    return len(rijen)


def koppel_toetsen(con: sqlite3.Connection, projecten: list[dict]) -> int:
    """Vul toets_koppeling. projecten: [{'id', 'vak', 'niveau', 'at', 'soort'}] uit Toetsconstructie.
    1. Wat de itemanalyse al koppelde (afname in de vragenbank) is zeker.
    2. Anders: zelfde AT-nummer, vak, niveau en soort (SE/OE). Bij meer dan één kandidaat: niet koppelen."""
    con.execute("DELETE FROM toets_koppeling WHERE zekerheid != 'docent'")
    con.execute("INSERT OR IGNORE INTO toets_koppeling SELECT DISTINCT project, laatste_afname, 'afname' "
                "FROM constructie_vragen WHERE laatste_afname IS NOT NULL AND project IS NOT NULL")
    if "toetsen" not in _tabellen(con):
        return con.execute("SELECT COUNT(*) FROM toets_koppeling").fetchone()[0]
    toetsen = [r[0] for r in con.execute("SELECT toets_id FROM toetsen")]
    al = {r[0] for r in con.execute("SELECT project FROM toets_koppeling")}
    for p in projecten:
        if not p.get("id") or p["id"] in al or not p.get("at"):
            continue
        at = str(p["at"]).strip().upper()
        kandidaten = []
        for t in toetsen:
            delen = t.split("_")
            vak, niveau = vak_niveau_van_toets(t)
            if (delen[0].upper() == at and vak == p.get("vak") and niveau == (p.get("niveau") or "").lower()
                    and (len(delen) < 2 or delen[1].upper() == _soortcode(p.get("soort")))):
                kandidaten.append(t)
        if len(kandidaten) == 1:
            con.execute("INSERT OR IGNORE INTO toets_koppeling VALUES (?, ?, 'naam')", (p["id"], kandidaten[0]))
    return con.execute("SELECT COUNT(*) FROM toets_koppeling").fetchone()[0]


def maak_view(con: sqlite3.Connection) -> None:
    """(Her)maak de view itembank. Kolommen die (nog) niet bestaan worden NULL, zodat oudere databases ook werken."""
    delen = []
    if {"constructie_vragen", "toets_koppeling"} <= _tabellen(con):
        tm = _kolommen(con, "toetsmatrijs") if "toetsmatrijs" in _tabellen(con) else set()
        code_tm = ("(SELECT m.leerdoel_code FROM toets_koppeling k JOIN toetsmatrijs m ON m.toets_id = k.toets_id "
                   "AND m.vraagnummer = c.nr WHERE k.project = c.project LIMIT 1)") if "leerdoel_code" in tm else "NULL"
        delen.append(
            "SELECT 'construeren' AS bron, c.vraag_id AS sleutel,"
            " COALESCE(c.laatste_afname, (SELECT k.toets_id FROM toets_koppeling k WHERE k.project = c.project LIMIT 1)) AS toets_id,"
            f" c.project, c.vak, c.niveau, c.nr AS vraagnr, c.punten, COALESCE(c.leerdoel_code, {code_tm}) AS leerdoel_code,"
            " c.leerdoel, c.eindterm, c.bloom, c.vraag AS tekst, c.p_waarde, c.rit,"
            " CASE WHEN c.laatste_afname IS NULL THEN 'gebouwd' ELSE 'afgenomen' END AS status FROM constructie_vragen c")
    if {"toetsmatrijs", "evaluatieresultaten", "toetsen"} <= _tabellen(con):
        tm = _kolommen(con, "toetsmatrijs")
        k = lambda naam: f"m.{naam}" if naam in tm else "NULL"  # noqa: E731
        delen.append(
            "SELECT 'evalueren', CAST(m.item_id AS TEXT), m.toets_id,"
            " (SELECT k.project FROM toets_koppeling k WHERE k.toets_id = m.toets_id LIMIT 1),"
            " t.vak, NULL, m.vraagnummer, m.max_score,"
            f" {k('leerdoel_code')}, {k('leerdoel')}, {k('domein_eindterm')}, {k('bloom_niveau')}, NULL,"
            " (SELECT AVG(e.behaalde_score) / NULLIF(m.max_score, 0) FROM evaluatieresultaten e WHERE e.item_id = m.item_id),"
            " NULL, 'afgenomen' FROM toetsmatrijs m JOIN toetsen t ON t.toets_id = m.toets_id")
    if "items" in _tabellen(con) and {"uid", "inhoud", "leerdoel_code"} <= _kolommen(con, "items"):
        delen.append(
            "SELECT 'oefenen', i.uid, NULL, i.opdracht_id, i.vak, i.niveau, NULL,"
            " json_extract(i.inhoud, '$.punten'), i.leerdoel_code, NULL, i.eindterm, i.bloom,"
            " json_extract(i.inhoud, '$.vraag'), i.p_waarde, i.rit, i.status FROM items i")
    con.execute("DROP VIEW IF EXISTS itembank")
    if delen:
        con.execute("CREATE VIEW itembank AS " + " UNION ALL ".join(delen))


def _tabellen(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}


def projecten_uit_constructie(toetsen_map: Path) -> list[dict]:
    uit = []
    for pj in sorted(toetsen_map.glob("*/project.json")) if toetsen_map.exists() else []:
        try:
            p = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        uit.append({"id": p.get("id") or pj.parent.name, "vak": p.get("vak"), "niveau": p.get("niveau"),
                    "at": p.get("at"), "soort": p.get("soort")})
    return uit


def werk_bij(pad: Path | None = None) -> dict:
    """Werk de itembank bij: vragenbank inlezen, toetsen koppelen, view maken. Geeft aantallen terug."""
    con = database.verbind(pad)
    try:
        with con:
            con.executescript(SCHEMA)
            n_vragen = lees_vragenbank(con, paden.TOETSCONSTRUCTIE / "kennisbank" / "vragenbank.json")
            n_koppel = koppel_toetsen(con, projecten_uit_constructie(paden.TOETSCONSTRUCTIE / "toetsen"))
            maak_view(con)
        per_bron = dict(con.execute("SELECT bron, COUNT(*) FROM itembank GROUP BY bron").fetchall())
    finally:
        con.close()
    return {"constructie_vragen": n_vragen, "koppelingen": n_koppel, "itembank": per_bron}


def zoek(vak: str | None = None, leerdoel_code: str | None = None, bron: str | None = None, tekst: str | None = None,
         limiet: int = 200, pad: Path | None = None) -> list[dict]:
    voorwaarden, waarden = [], []
    for kolom, waarde in (("vak", vak), ("leerdoel_code", leerdoel_code), ("bron", bron)):
        if waarde:
            voorwaarden.append(f"{kolom} = ?")
            waarden.append(waarde)
    if tekst:
        voorwaarden.append("(tekst LIKE ? OR leerdoel LIKE ?)")
        waarden += [f"%{tekst}%"] * 2
    sql = "SELECT * FROM itembank" + (" WHERE " + " AND ".join(voorwaarden) if voorwaarden else "") + " LIMIT ?"
    con = database.verbind(pad)
    try:
        return [dict(r) for r in con.execute(sql, (*waarden, limiet))]
    finally:
        con.close()
