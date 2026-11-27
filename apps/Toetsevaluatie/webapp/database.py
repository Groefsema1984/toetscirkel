# -*- coding: utf-8 -*-
"""Schrijven naar en lezen uit toetsevaluatie.db. Schrijven gebeurt alleen bij 'Definitief maken'."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from config import kern
from scores import eind_leergat, eindscore


def publiceer(s: dict) -> int:
    """Zet toets, toetsmatrijs en definitieve scores in de database. Geeft het aantal leerlingen."""
    toets_id = s["toets_id"]
    info = kern.lees_toetsmap(toets_id)
    con = kern.open_db()
    try:
        with con:
            con.execute(
                "INSERT INTO toetsen (toets_id, vak, datum) VALUES (?, ?, ?) "
                "ON CONFLICT(toets_id) DO UPDATE SET vak=excluded.vak, datum=excluded.datum",
                (toets_id, info.vak, s["datum"]))
        kern.db_schrijf_matrijs(con, toets_id, s["matrijs"])
        _schrijf_leerdoelen(con, toets_id, s["matrijs"])
        item_id = {m["vraagnummer"]: m["item_id"] for m in kern.db_lees_matrijs(con, toets_id)}

        klaar = {lid: l for lid, l in s["leerlingen"].items() if l["status"] == "klaar"}
        # Resultaten van leerlingen die na een herkoppeling niet meer bij deze toets horen weghalen.
        with con:
            vraagtekens = ",".join("?" * len(klaar)) or "''"
            con.execute(
                "DELETE FROM evaluatieresultaten WHERE item_id IN "
                "(SELECT item_id FROM toetsmatrijs WHERE toets_id = ?) "
                f"AND leerling_id NOT IN ({vraagtekens})", [toets_id, *klaar])
        for lid, l in klaar.items():
            regels = []
            for v in l["vragen"].values():
                leergat, omschrijving = eind_leergat(v)
                regels.append({"item_id": item_id[v["nr"]], "score": eindscore(v),
                               "leergat": leergat, "omschrijving": omschrijving})
            kern.db_schrijf_resultaten(con, info, lid, regels, l.get("klas", ""))
        return len(klaar)
    finally:
        con.close()


# ---------------------------------------------------------------------------- normering (N-term)
def _formule(s: str, l: str, n: str) -> str:
    """Score naar cijfer in SQL, zelfde regel als analyse.cijfer()."""
    return (f"CASE WHEN {l} <= 0 THEN NULL "
            f"WHEN {n} > 1 THEN ROUND(MIN(9.0*{s}/{l} + {n}, 1.0 + {s}*9.0/{l}*2, "
            f"10.0 - ({l}-{s})*9.0/{l}*0.5) + 1e-9, 1) "
            f"WHEN {n} < 1 THEN ROUND(MAX(9.0*{s}/{l} + {n}, 1.0 + {s}*9.0/{l}*0.5, "
            f"10.0 - ({l}-{s})*9.0/{l}*2) + 1e-9, 1) "
            f"ELSE ROUND(9.0*{s}/{l} + 1.0 + 1e-9, 1) END")


VIEW_CIJFERS = f"""CREATE VIEW cijferoverzicht AS
SELECT leerling_id, klas, niveau, toets_id, vak, n_term,
    -- 1. HET STANDAARD CIJFER (voor mavo, regulier havo en 6 vwo), met de N-term van de toets
    {_formule('s', 'l_max', 'n_term')} AS behaald_cijfer,
    -- 2. TWEEFASEN: vwo-cijfer over alle vragen, havo-cijfer alleen over de H+V-vragen
    CASE WHEN tweefasen THEN {_formule('s', 'l_max', 'n_term')} END AS tweefasen_vwo_cijfer,
    CASE WHEN tweefasen AND l_hv > 0 THEN {_formule('s_hv', 'l_hv', 'n_havo')} END
        AS tweefasen_havo_cijfer
FROM (
    SELECT l.leerling_id, l.klas, l.niveau, t.toets_id, t.vak,
        SUM(e.behaalde_score) AS s, TOTAL(m.max_score) AS l_max,
        TOTAL(CASE WHEN m.tweefasen_label = 'H+V' THEN e.behaalde_score ELSE 0 END) AS s_hv,
        TOTAL(CASE WHEN m.tweefasen_label = 'H+V' THEN m.max_score ELSE 0 END) AS l_hv,
        (t.toets_id LIKE '%tweefasen%' OR EXISTS (SELECT 1 FROM toetsmatrijs tm
            WHERE tm.toets_id = t.toets_id AND tm.tweefasen_label = 'V')) AS tweefasen,
        COALESCE(nt.n_term, 1.0) AS n_term,
        COALESCE(nt.n_term_havo, nt.n_term, 1.0) AS n_havo
    FROM evaluatieresultaten e
    JOIN leerlingen l ON e.leerling_id = l.leerling_id
    JOIN toetsmatrijs m ON e.item_id = m.item_id
    JOIN toetsen t ON m.toets_id = t.toets_id
    LEFT JOIN normering nt ON nt.toets_id = t.toets_id
    GROUP BY l.leerling_id, t.toets_id
)"""


def zorg_voor_normering() -> None:
    """Maakt de tabel normering en zet de N-term in de view cijferoverzicht (eenmalig).

    Voor de eerste wijziging komt er een reservekopie van de database naast het origineel."""
    con = kern.open_db()
    try:
        oud = con.execute("SELECT sql FROM sqlite_master WHERE name = 'cijferoverzicht'").fetchone()
        heeft_tabel = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'normering'").fetchone()
        if heeft_tabel and oud and "n_term" in (oud[0] or ""):
            return
        reserve = kern.DB_PAD.with_name(f"toetsevaluatie_reserve_{datetime.now():%Y%m%d}.db")
        if not reserve.exists():
            con.execute("VACUUM INTO ?", (str(reserve),))
        with con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS normering ("
                " toets_id TEXT PRIMARY KEY REFERENCES toetsen(toets_id),"
                " n_term REAL NOT NULL DEFAULT 1.0,"
                " n_term_havo REAL,"
                " gewijzigd TEXT)")
            con.execute("DROP VIEW IF EXISTS cijferoverzicht")
            con.execute(VIEW_CIJFERS)
    finally:
        con.close()


# ---------------------------------------------------------------------------- leerdoelen
LEERDOEL_KOLOMMEN = ("leerdoel", "leerdoel_leerling", "onderwerp")


def zorg_voor_leerdoelen() -> None:
    """Voegt (eenmalig) de kolommen leerdoel, leerdoel_leerling en onderwerp toe aan toetsmatrijs."""
    con = kern.open_db()
    try:
        bestaand = {r[1] for r in con.execute("PRAGMA table_info(toetsmatrijs)")}
        with con:
            for kolom in LEERDOEL_KOLOMMEN:
                if kolom not in bestaand:
                    con.execute(f"ALTER TABLE toetsmatrijs ADD COLUMN {kolom} TEXT")
    finally:
        con.close()


def _schrijf_leerdoelen(con, toets_id: str, matrijs: list[dict]) -> None:
    with con:
        con.executemany(
            "UPDATE toetsmatrijs SET leerdoel = ?, leerdoel_leerling = ?, onderwerp = ? "
            "WHERE toets_id = ? AND vraagnummer = ?",
            [(m.get("leerdoel") or None, m.get("leerdoel_leerling") or None,
              m.get("onderwerp") or None, toets_id, m["vraagnummer"]) for m in matrijs])
        # Oefentoets uit de oefentool: de leerdoelcode staat al in het correctievoorschrift.
        vast = [m for m in matrijs if m.get("leerdoel_code")]
        if vast:
            con.executemany(
                "UPDATE toetsmatrijs SET leerdoel_code = ?, koppel_status = 'bevestigd', "
                "koppel_toelichting = 'Leerdoelcode uit het correctievoorschrift (oefentool)' "
                "WHERE toets_id = ? AND vraagnummer = ?",
                [(m["leerdoel_code"], toets_id, m["vraagnummer"]) for m in vast])


def zet_leerdoelen(toets_id: str, matrijs: list[dict]) -> None:
    con = kern.open_db()
    try:
        _schrijf_leerdoelen(con, toets_id, matrijs)
    finally:
        con.close()


def normering(toets_id: str) -> tuple[float, float | None]:
    """(N-term, N-term havo-cijfer of None als die gelijk is aan de N-term)."""
    con = kern.open_db()
    try:
        r = con.execute("SELECT n_term, n_term_havo FROM normering WHERE toets_id = ?",
                        (toets_id,)).fetchone()
        return (r["n_term"], r["n_term_havo"]) if r else (1.0, None)
    except sqlite3.OperationalError:                  # tabel bestaat nog niet
        return (1.0, None)
    finally:
        con.close()


def zet_normering(toets_id: str, n: float, n_havo: float | None) -> None:
    con = kern.open_db()
    try:
        with con:
            con.execute(
                "INSERT INTO normering (toets_id, n_term, n_term_havo, gewijzigd) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(toets_id) DO UPDATE SET n_term = excluded.n_term, "
                "n_term_havo = excluded.n_term_havo, gewijzigd = excluded.gewijzigd",
                (toets_id, n, n_havo, datetime.now().isoformat(timespec="seconds")))
    finally:
        con.close()


def toetsen() -> list[dict]:
    con = kern.open_db()
    try:
        rijen = con.execute(
            "SELECT t.toets_id, t.vak, t.datum, COUNT(DISTINCT e.leerling_id) AS aantal "
            "FROM toetsen t LEFT JOIN toetsmatrijs m ON m.toets_id = t.toets_id "
            "LEFT JOIN evaluatieresultaten e ON e.item_id = m.item_id "
            "GROUP BY t.toets_id ORDER BY t.datum DESC, t.toets_id").fetchall()
        return [dict(r) for r in rijen]
    finally:
        con.close()


def rijen(toets_id: str) -> list[dict]:
    """Eén rij per leerling per vraag, met de gegevens uit de toetsmatrijs erbij."""
    con = kern.open_db()
    try:
        uit = con.execute(
            "SELECT e.leerling_id, l.klas, l.niveau, m.vraagnummer, m.domein_eindterm, "
            "m.tweefasen_label, m.bloom_niveau, m.max_score, e.behaalde_score AS score, "
            "m.leerdoel, m.leerdoel_leerling, m.onderwerp, "
            "e.type_leergat AS leergat, e.foutomschrijving AS omschrijving "
            "FROM evaluatieresultaten e JOIN toetsmatrijs m ON m.item_id = e.item_id "
            "LEFT JOIN leerlingen l ON l.leerling_id = e.leerling_id "
            "WHERE m.toets_id = ? ORDER BY e.leerling_id, m.vraagnummer", (toets_id,)).fetchall()
        return [dict(r) for r in uit]
    finally:
        con.close()


def toets(toets_id: str) -> dict | None:
    con = kern.open_db()
    try:
        r = con.execute("SELECT toets_id, vak, datum FROM toetsen WHERE toets_id = ?",
                        (toets_id,)).fetchone()
        return dict(r) if r else None
    finally:
        con.close()
