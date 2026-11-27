# -*- coding: utf-8 -*-
"""Eén database voor ToetsCirkel.

De centrale database is het bestaande bestand toetsevaluatie.db (naam en plek blijven gelijk, zodat de gouden toets,
de back-up en de Toetsevaluatie gewoon blijven werken). Daar komen bij:
- de tabellen van de Oefentool (bronnen, fragmenten, items, item_gebruik, logboek), eenmalig overgezet uit oefentool.db;
- toetscirkel_meta: wat er wanneer is omgezet;
- de itembank (zie itembank.py).

De overzetting:
1. maakt eerst een back-up (scripts/backup.py);
2. kopieert alle rijen met dezelfde nummers, zodat verwijzingen (item_uid, bron_id) blijven kloppen;
3. controleert per tabel of het aantal rijen gelijk is; zo niet, dan wordt niets vastgelegd;
4. hernoemt oefentool.db naar oefentool.voor_fase2.db (niet verwijderd: terugzetten kan altijd).
Daarna leest de Oefentool zijn tabellen uit de centrale database (zie Oefentool/oefentool/instellingen.py).
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import paden  # noqa: E402

OEFENTOOL_TABELLEN = ("bronnen", "fragmenten", "items", "item_gebruik", "logboek")
MIGRATIE_FASE2 = "fase2_oefentool_samengevoegd"


def centrale_db() -> Path:
    return paden.TOETSEVALUATIE / "toetsevaluatie.db"


def oude_oefentool_db() -> Path:
    return paden.OEFENTOOL / "oefentool.db"


def verbind(pad: Path | None = None) -> sqlite3.Connection:
    """Open de centrale database. Bestaat die niet, dan een fout (nooit stilletjes een lege database aanmaken)."""
    pad = pad or centrale_db()
    if not pad.exists():
        raise FileNotFoundError(f"De centrale database ontbreekt: {pad}")
    con = sqlite3.connect(f"file:{pad}?mode=rw", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def zorg_voor_meta(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS toetscirkel_meta (sleutel TEXT PRIMARY KEY, waarde TEXT, moment TEXT)")


def meta(con: sqlite3.Connection, sleutel: str) -> str | None:
    zorg_voor_meta(con)
    r = con.execute("SELECT waarde FROM toetscirkel_meta WHERE sleutel = ?", (sleutel,)).fetchone()
    return r[0] if r else None


def zet_meta(con: sqlite3.Connection, sleutel: str, waarde: str) -> None:
    zorg_voor_meta(con)
    con.execute("INSERT OR REPLACE INTO toetscirkel_meta VALUES (?, ?, ?)",
                (sleutel, waarde, datetime.now().isoformat(timespec="seconds")))


def is_samengevoegd(pad: Path | None = None) -> bool:
    pad = pad or centrale_db()
    if not pad.exists():
        return False
    con = sqlite3.connect(f"file:{pad}?mode=ro", uri=True, timeout=30)
    try:
        heeft = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'toetscirkel_meta'").fetchone()
        return bool(heeft and con.execute("SELECT 1 FROM toetscirkel_meta WHERE sleutel = ?",
                                          (MIGRATIE_FASE2,)).fetchone())
    finally:
        con.close()


def _tabeldefinitie(con: sqlite3.Connection, schema: str, tabel: str) -> str | None:
    r = con.execute(f"SELECT sql FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?", (tabel,)).fetchone()
    return r[0] if r else None


def voeg_oefentool_samen(maak_backup=True) -> str:
    """Zet de tabellen van oefentool.db over naar de centrale database. Geeft een korte uitleg terug.
    Veilig om vaker aan te roepen: is het al gebeurd, dan doet het niets."""
    centraal, oud = centrale_db(), oude_oefentool_db()
    if not centraal.exists():
        return f"Centrale database ontbreekt ({centraal}); niets omgezet."
    if is_samengevoegd(centraal):
        return "Oefentool staat al in de centrale database."
    con = verbind(centraal)
    con.isolation_level = None                      # transacties hieronder zelf beheren
    try:
        if not oud.exists():
            # Nog nooit een Oefentool-database gehad: de Oefentool maakt zijn tabellen straks zelf in de centrale database.
            zet_meta(con, MIGRATIE_FASE2, "geen oefentool.db aanwezig")
            return "Geen oefentool.db gevonden; de Oefentool gebruikt vanaf nu de centrale database."
        if maak_backup:
            import backup  # scripts/backup.py
            map_ = backup.maak_backup(paden.DATA / "backups")
        else:
            map_ = None
        con.execute("ATTACH DATABASE ? AS oud", (str(oud),))
        aantallen = {}
        con.execute("BEGIN IMMEDIATE")
        try:
            for tabel in OEFENTOOL_TABELLEN:
                sql = _tabeldefinitie(con, "oud", tabel)
                if not sql:
                    continue
                if _tabeldefinitie(con, "main", tabel):
                    raise RuntimeError(f"De centrale database heeft al een tabel '{tabel}'; omzetten afgebroken.")
                con.execute(sql)                                    # zelfde kolommen en sleutels
                kolommen = [r[1] for r in con.execute(f"PRAGMA oud.table_info({tabel})")]
                lijst = ", ".join(f'"{k}"' for k in kolommen)
                con.execute(f"INSERT INTO main.{tabel} ({lijst}) SELECT {lijst} FROM oud.{tabel}")
                n_oud = con.execute(f"SELECT COUNT(*) FROM oud.{tabel}").fetchone()[0]
                n_nieuw = con.execute(f"SELECT COUNT(*) FROM main.{tabel}").fetchone()[0]
                if n_oud != n_nieuw:
                    raise RuntimeError(f"Tabel {tabel}: {n_oud} rijen in oefentool.db maar {n_nieuw} overgezet.")
                aantallen[tabel] = n_nieuw
            for r in con.execute("SELECT sql FROM oud.sqlite_master WHERE type = 'index' AND sql IS NOT NULL").fetchall():
                con.execute(r[0].replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1))
            zet_meta(con, MIGRATIE_FASE2, ", ".join(f"{t}={n}" for t, n in aantallen.items()))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("DETACH DATABASE oud")
    finally:
        con.close()
    bewaard = oud.with_name("oefentool.voor_fase2.db")
    oud.rename(bewaard)
    for extra in ("-wal", "-shm", "-journal"):
        p = oud.with_name(oud.name + extra)
        if p.exists():
            p.rename(bewaard.with_name(bewaard.name + extra))
    regels = ", ".join(f"{n} {t}" for t, n in aantallen.items())
    return (f"Oefentool overgezet naar de centrale database ({regels}). Oude bestand bewaard als {bewaard.name}."
            + (f" Back-up vooraf: {map_}." if map_ else ""))
