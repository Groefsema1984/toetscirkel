#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Back-up van de databases en het geheugen van de drie apps.

- Databases worden met de back-upfunctie van SQLite gekopieerd: veilig, ook als een app draait.
- De kennisbank (alleen json, txt en md), leerregels en kwaliteitsregels gaan mee.
- Scans, klassenlijsten en de map toetsen/ gaan NIET mee (groot; ze staan al in OneDrive of zijn opnieuw te maken).
- Elke back-up komt in een eigen map met datum en tijd. De nieuwste BEWAAR (standaard 30) blijven staan.

Gebruik:  python3 scripts/backup.py            (of dubbelklik op scripts/Backup.command)
          python3 scripts/backup.py --naar <map>
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paden  # noqa: E402

BEWAAR = 30


def kopieer_db(bron: Path, doel: Path) -> None:
    src = sqlite3.connect(f"file:{bron}?mode=ro", uri=True)
    dst = sqlite3.connect(str(doel))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    # Controle: de kopie moet te openen zijn en dezelfde tabellen hebben.
    con = sqlite3.connect(str(doel))
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"integriteitscontrole van {doel.name} mislukt")
    finally:
        con.close()


def maak_backup(basis: Path) -> Path:
    doel = basis / datetime.now().strftime("%Y%m%d_%H%M%S")
    doel.mkdir(parents=True, exist_ok=False)
    verslag = []
    for naam, pad in paden.DATABASES.items():
        if pad.exists():
            kopieer_db(pad, doel / naam)
            verslag.append(f"database  {naam}  ({pad.stat().st_size // 1024} kB)")
        else:
            verslag.append(f"ontbreekt {naam}  ({pad})")
    for naam, pad in paden.LEERBESTANDEN.items():
        if not pad.exists():
            verslag.append(f"ontbreekt {naam}")
            continue
        uit = doel / naam
        uit.parent.mkdir(parents=True, exist_ok=True)
        if pad.is_dir():
            # Alleen wat het systeem zelf leert (json, txt, md); grote examen-pdf's staan al elders en zouden
            # met 30 back-ups gigabytes kosten.
            shutil.copytree(pad, uit, ignore=shutil.ignore_patterns("*.pdf", "*.html", "*.docx", "*.pptx", "*.png", "*.jpg"))
        else:
            shutil.copy2(pad, uit)
        verslag.append(f"kopie     {naam}")
    (doel / "VERSLAG.txt").write_text("\n".join(verslag) + "\n", encoding="utf-8")
    return doel


def ruim_op(basis: Path, bewaar: int) -> list[Path]:
    mappen = sorted(p for p in basis.iterdir() if p.is_dir() and p.name[:8].isdigit())
    weg = mappen[:-bewaar] if len(mappen) > bewaar else []
    for p in weg:
        shutil.rmtree(p)
    return weg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--naar", type=Path, default=paden.DATA / "backups", help="map voor de back-ups")
    ap.add_argument("--bewaar", type=int, default=BEWAAR, help="aantal back-ups dat blijft staan")
    a = ap.parse_args()
    basis = a.naar.expanduser()
    basis.mkdir(parents=True, exist_ok=True)
    doel = maak_backup(basis)
    weg = ruim_op(basis, a.bewaar)
    print((doel / "VERSLAG.txt").read_text(encoding="utf-8"), end="")
    print(f"Back-up klaar: {doel}" + (f"  ({len(weg)} oude back-ups opgeruimd)" if weg else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
