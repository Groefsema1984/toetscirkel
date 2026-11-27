"""Leerdoelenregister: de vaste leerdoelcodes (bijv. ECO-KRING-01) uit de database van ToetsCirkel / Toetsevaluatie.

Een code in de matrijs (veld leerdoel_code) komt in het correctievoorschrift en wordt bij het nakijken overgenomen.
Zo weet de evaluatie zonder te raden welk leerdoel elke vraag toetst, en kan de oefentool er gericht op oefenen.

Gebruik (vanuit de map Toetsconstructie):
  python3 bouw/leerdoelen.py --vak Economie --niveau Havo          codes voor deze toets
  python3 bouw/leerdoelen.py --vak Economie --niveau Havo --zoek kringloop
Het register hoort bij vak en sector: havo en vwo delen één register, mavo heeft een eigen register.
"""
import argparse, json, os, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def db_pad():
    """Instelling van de app, anders TOETSEVALUATIE_MAP, anders ~/Documents/Toetsevaluatie."""
    try:
        s = json.loads((ROOT / "instellingen.json").read_text(encoding="utf-8"))
        if s.get("toetsevaluatie_db"):
            return Path(s["toetsevaluatie_db"]).expanduser()
    except Exception:
        pass
    basis = os.environ.get("TOETSEVALUATIE_MAP") or str(Path.home() / "Documents" / "Toetsevaluatie")
    return Path(basis).expanduser() / "toetsevaluatie.db"


def sector(niveau):
    return "mavo" if (niveau or "").strip().lower() in ("mavo", "vmbo") else "havo-vwo"


def register(vak=None, niveau=None, zoek=None, pad=None):
    """Lijst van {code, vak, sector, thema, omschrijving, leerling_zin, status}; leeg als er (nog) geen register is."""
    pad = Path(pad) if pad else db_pad()
    if not pad.exists():
        return []
    con = sqlite3.connect(f"file:{pad}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'leerdoelen_register'").fetchone():
            return []
        sql, w = "SELECT code, vak, sector, thema, omschrijving, leerling_zin, status FROM leerdoelen_register WHERE status != 'vervallen'", []
        if vak:
            sql += " AND vak = ?"; w.append(vak)
        if niveau:
            sql += " AND sector = ?"; w.append(sector(niveau))
        if zoek:
            sql += " AND (omschrijving LIKE ? OR thema LIKE ? OR code LIKE ?)"; w += [f"%{zoek}%"] * 3
        return [dict(r) for r in con.execute(sql + " ORDER BY thema, code", w)]
    finally:
        con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vak"); ap.add_argument("--niveau"); ap.add_argument("--zoek")
    a = ap.parse_args()
    rijen = register(a.vak, a.niveau, a.zoek)
    if not rijen:
        print("Geen leerdoelenregister gevonden voor deze keuze. Laat leerdoel_code dan leeg; de evaluatie koppelt later.")
    thema = None
    for r in rijen:
        if r["thema"] != thema:
            thema = r["thema"]; print(f"\n# {thema or 'zonder thema'}")
        print(f"{r['code']:<18} {r['omschrijving']}" + ("  (nieuw, nog niet goedgekeurd)" if r["status"] == "nieuw" else ""))
