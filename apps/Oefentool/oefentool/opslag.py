# -*- coding: utf-8 -*-
"""oefentool.db: bibliotheek (bronnen en fragmenten) en de itembank (alle gemaakte opgaven).

Hier staan geen leerlingnummers: die staan alleen in de opdrachtmappen (Opdrachten/<id>/).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from .instellingen import DB

_slot = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS bronnen (
    bron_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pad TEXT UNIQUE NOT NULL,
    naam TEXT NOT NULL,
    map TEXT,
    vak TEXT, sector TEXT, niveau TEXT,
    soort TEXT,                 -- lesmateriaal, examen, correctievoorschrift, eindtermen, overig
    grootte INTEGER, mtime REAL,
    status TEXT NOT NULL DEFAULT 'nieuw',   -- nieuw, geindexeerd, gewijzigd, overgeslagen, fout
    tekens INTEGER, melding TEXT,
    partner TEXT,               -- correctievoorschrift bij een examen
    geindexeerd TEXT
);
CREATE TABLE IF NOT EXISTS fragmenten (
    fragment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bron_id INTEGER NOT NULL REFERENCES bronnen(bron_id) ON DELETE CASCADE,
    soort TEXT,                 -- uitleg, voorbeeld, opgave, examenvraag, begrippen
    titel TEXT, kern TEXT, vraag TEXT, antwoord TEXT,
    paginas TEXT, examen TEXT, vraagnr TEXT, punten INTEGER,
    leerdoel_codes TEXT,        -- JSON-lijst
    eindtermen TEXT, bloom TEXT, niveau TEXT
);
CREATE INDEX IF NOT EXISTS fr_bron ON fragmenten(bron_id);
CREATE TABLE IF NOT EXISTS items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT UNIQUE,
    opdracht_id TEXT,
    vak TEXT, sector TEXT, niveau TEXT,
    leerdoel_code TEXT, eindterm TEXT, bloom TEXT, soort TEXT, leergat TEXT, stap INTEGER,
    inhoud TEXT NOT NULL,       -- JSON: context, vraag, punten, cv, voorbeeld, uitleg, hints ...
    versie INTEGER NOT NULL DEFAULT 1,
    ouder_uid TEXT,
    rekencheck TEXT,            -- JSON: per berekening ok/fout
    kritiek_score REAL, kritiek TEXT,
    docent_oordeel TEXT,        -- goed, aanpassen, afgekeurd
    docent_opmerking TEXT,
    status TEXT NOT NULL DEFAULT 'concept',  -- concept, goedgekeurd, afgekeurd, vervangen
    gebruikt INTEGER NOT NULL DEFAULT 0,
    p_waarde REAL, rit REAL, n_resultaten INTEGER,
    gemaakt TEXT, gewijzigd TEXT
);
CREATE INDEX IF NOT EXISTS it_doel ON items(leerdoel_code, status);
CREATE TABLE IF NOT EXISTS item_gebruik (
    item_uid TEXT NOT NULL, opdracht_id TEXT NOT NULL, set_naam TEXT,
    toets_id TEXT,              -- bij een oefenexamen: toetsnaam in de Toetsevaluatie (…_OE_…)
    vraagnr INTEGER,
    PRIMARY KEY (item_uid, opdracht_id, set_naam)
);
CREATE TABLE IF NOT EXISTS logboek (
    moment TEXT, soort TEXT, tekst TEXT, invoer INTEGER, uitvoer INTEGER
);
"""


def verbind() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def db():
    with _slot:
        con = verbind()
        try:
            with con:
                yield con
        finally:
            con.close()


def maak() -> None:
    with db() as con:
        con.executescript(SCHEMA)
        kolommen = {r[1] for r in con.execute("PRAGMA table_info(logboek)")}
        for k, soort in (("opdracht", "TEXT"), ("model", "TEXT"), ("cache_schrijf", "INTEGER"), ("cache_lees", "INTEGER"),
                         ("kosten", "REAL")):
            if k not in kolommen:
                con.execute(f"ALTER TABLE logboek ADD COLUMN {k} {soort}")


def nu() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_gebruik(soort: str, tekst: str, invoer: int, uitvoer: int, opdracht: str = "", model: str = "",
                schrijf: int = 0, lees: int = 0, kosten: float = 0.0) -> None:
    """invoer = gewone invoertokens (zonder cache); schrijf/lees = cache; kosten in dollar."""
    try:
        with db() as con:
            con.execute("INSERT INTO logboek (moment, soort, tekst, invoer, uitvoer, opdracht, model, cache_schrijf,"
                        " cache_lees, kosten) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (nu(), soort, tekst[:200], invoer, uitvoer, opdracht, model, schrijf, lees, round(kosten, 5)))
    except sqlite3.Error:
        pass


def kosten_van(opdracht: str) -> float:
    with db() as con:
        r = con.execute("SELECT SUM(kosten) FROM logboek WHERE opdracht = ?", (opdracht,)).fetchone()
    return r[0] or 0.0


KOSTEN_SQL = "COALESCE(kosten, (invoer * 2.0 + uitvoer * 10.0) / 1000000.0 * (CASE WHEN soort LIKE '%batch%' THEN 0.5 ELSE 1 END))"


def kosten_per_leerdoel() -> float:
    """Gemiddelde kosten van ronde 2 per leerdoel in eerdere opdrachten (voor de schatting vooraf)."""
    with db() as con:
        r = con.execute("SELECT SUM(kosten), COUNT(DISTINCT opdracht || tekst) FROM logboek "
                        "WHERE soort = 'genereren' AND kosten IS NOT NULL AND opdracht != '' "
                        "AND COALESCE(model, '') NOT LIKE 'abonnement%'").fetchone()
        tot = con.execute("SELECT SUM(kosten) FROM logboek WHERE soort IN ('genereren', 'herzien', 'kritiek') "
                          "AND kosten IS NOT NULL AND opdracht != '' AND COALESCE(model, '') NOT LIKE 'abonnement%'").fetchone()[0]
    return (tot / r[1]) if r[1] and tot else 0.25


# ---------------------------------------------------------------------------- items
def item_uit_rij(r) -> dict:
    d = dict(r)
    d["inhoud"] = json.loads(d["inhoud"])
    for k in ("rekencheck", "kritiek"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


def nieuw_item(con, opdracht_id: str, basis: dict, inhoud: dict, ouder: dict | None = None) -> dict:
    cur = con.execute(
        "INSERT INTO items (opdracht_id, vak, sector, niveau, leerdoel_code, eindterm, bloom, soort, leergat,"
        " stap, inhoud, versie, ouder_uid, gemaakt, gewijzigd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (opdracht_id, basis["vak"], basis["sector"], basis["niveau"], basis["leerdoel_code"],
         inhoud.get("eindterm", ""), inhoud.get("bloom", ""), inhoud.get("soort", ""), basis.get("leergat", ""),
         basis.get("stap", 1), json.dumps(inhoud, ensure_ascii=False),
         (ouder["versie"] + 1) if ouder else 1, ouder["uid"] if ouder else None, nu(), nu()))
    uid = f"IT-{cur.lastrowid:05d}"
    con.execute("UPDATE items SET uid = ? WHERE item_id = ?", (uid, cur.lastrowid))
    if ouder:
        con.execute("UPDATE items SET status = 'vervangen', gewijzigd = ? WHERE uid = ?", (nu(), ouder["uid"]))
    return item_uit_rij(con.execute("SELECT * FROM items WHERE uid = ?", (uid,)).fetchone())


def items_van(uids: list[str]) -> dict[str, dict]:
    if not uids:
        return {}
    with db() as con:
        vt = ",".join("?" * len(uids))
        return {r["uid"]: item_uit_rij(r) for r in con.execute(f"SELECT * FROM items WHERE uid IN ({vt})", uids)}


def item(uid: str) -> dict | None:
    return items_van([uid]).get(uid)


def zet_item(uid: str, **velden) -> None:
    if not velden:
        return
    for k in ("inhoud", "rekencheck", "kritiek"):
        if k in velden and not isinstance(velden[k], (str, type(None))):
            velden[k] = json.dumps(velden[k], ensure_ascii=False)
    velden["gewijzigd"] = nu()
    with db() as con:
        con.execute(f"UPDATE items SET {', '.join(f'{k} = ?' for k in velden)} WHERE uid = ?",
                    [*velden.values(), uid])


def bewezen_items(leerdoel_code: str, niveau: str, aantal: int = 3) -> list[dict]:
    """Beste eerdere opgaven bij dit leerdoel: goedgekeurd door de docent, liefst met goede P/Rit."""
    with db() as con:
        rijen = con.execute(
            "SELECT * FROM items WHERE leerdoel_code = ? AND status = 'goedgekeurd' AND niveau = ? "
            "ORDER BY (CASE WHEN rit IS NOT NULL AND rit >= 0.25 AND p_waarde BETWEEN 0.3 AND 0.85 THEN 0 ELSE 1 END),"
            " COALESCE(kritiek_score, 0) DESC, gewijzigd DESC LIMIT ?",
            (leerdoel_code, niveau, aantal)).fetchall()
    return [item_uit_rij(r) for r in rijen]
