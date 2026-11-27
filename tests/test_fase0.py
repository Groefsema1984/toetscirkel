# -*- coding: utf-8 -*-
"""Tests voor de hulpmiddelen van fase 0, met een verzonnen database (geen echte leerlingdata).

Draaien:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def maak_db(pad: Path, scores: dict[str, list[float]]) -> None:
    con = sqlite3.connect(pad)
    con.executescript("""
        CREATE TABLE leerlingen (leerling_id TEXT PRIMARY KEY, klas TEXT, niveau TEXT, cohort TEXT);
        CREATE TABLE toetsen (toets_id TEXT PRIMARY KEY, vak TEXT, datum TEXT);
        CREATE TABLE toetsmatrijs (item_id INTEGER PRIMARY KEY, toets_id TEXT, vraagnummer INTEGER,
            domein_eindterm TEXT, tweefasen_label TEXT, bloom_niveau TEXT, max_score REAL, leerdoel_code TEXT);
        CREATE TABLE evaluatieresultaten (leerling_id TEXT, item_id INTEGER, behaalde_score REAL,
            type_leergat TEXT, foutomschrijving TEXT);
    """)
    t = "AT5.11_SE_5H_Economie"
    con.execute("INSERT INTO toetsen VALUES (?, 'Economie', '2026-10-20')", (t,))
    maxima = [2, 3, 1]
    for nr, mx in enumerate(maxima, 1):
        con.execute("INSERT INTO toetsmatrijs VALUES (?, ?, ?, 'D1', NULL, 'Toepassen', ?, ?)",
                    (nr, t, nr, mx, "ECO-TEST-01" if nr < 3 else "ECO-TEST-02"))
    for lid, rij in scores.items():
        con.execute("INSERT INTO leerlingen VALUES (?, '5Ha', 'havo', '2026-2027')", (lid,))
        for nr, s in enumerate(rij, 1):
            con.execute("INSERT INTO evaluatieresultaten VALUES (?, ?, ?, ?, '')",
                        (lid, nr, s, None if s == maxima[nr - 1] else "RF"))
    con.commit()
    con.close()


class Fase0(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "Toetsevaluatie").mkdir()
        (t / "Oefentool").mkdir()
        self.db = t / "Toetsevaluatie" / "toetsevaluatie.db"
        maak_db(self.db, {"900001": [2, 3, 1], "900002": [1, 1, 0], "900003": [2, 2, 1], "900004": [0, 1, 0]})
        sqlite3.connect(t / "Oefentool" / "oefentool.db").close()
        self.env = dict(os.environ, TOETSEVALUATIE_MAP=str(t / "Toetsevaluatie"), OEFENTOOL_MAP=str(t / "Oefentool"),
                        TOETSCONSTRUCTIE_MAP=str(ROOT / "apps" / "Toetsconstructie"), TOETSCIRKEL_DATA=str(t / "data"))
        self.data = t / "data"

    def tearDown(self):
        self.tmp.cleanup()

    def run_py(self, *args):
        return subprocess.run([sys.executable, *args], cwd=ROOT, env=self.env, capture_output=True, text=True)

    def test_gouden_toets_vastleggen_en_controleren(self):
        r = self.run_py("tests/gouden_toets.py", "vastleggen", "AT5.11_SE_5H_Economie")
        self.assertEqual(r.returncode, 0, r.stderr)
        snap = json.loads((self.data / "gouden" / "AT5.11_SE_5H_Economie.json").read_text())
        self.assertEqual(snap["leerlingen"], 4)
        self.assertEqual(snap["metingen_per_leerdoel"]["ECO-TEST-01"], {"beheerst": 2, "nog_niet": 2})
        self.assertNotIn("900001", json.dumps(snap))   # geen leerlingnummers in de momentopname
        r = self.run_py("tests/gouden_toets.py", "controleren", "AT5.11_SE_5H_Economie")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)

    def test_gouden_toets_merkt_verandering(self):
        self.run_py("tests/gouden_toets.py", "vastleggen", "AT5.11_SE_5H_Economie")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE evaluatieresultaten SET behaalde_score = 3 WHERE leerling_id = '900002' AND item_id = 2")
        con.commit()
        con.close()
        r = self.run_py("tests/gouden_toets.py", "controleren", "AT5.11_SE_5H_Economie")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("AFWIJKING", r.stdout)

    def test_metingen_volgen_leerprofielregels(self):
        sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "scripts")]
        os.environ.update({k: v for k, v in self.env.items() if k.endswith(("_MAP", "_DATA"))})
        import paden, gouden_toets  # noqa: E401
        importlib.reload(paden)
        importlib.reload(gouden_toets)
        m = gouden_toets.momentopname("AT5.11_SE_5H_Economie")["metingen_per_leerdoel"]
        # ECO-TEST-01 = vraag 1 + 2 (max 5): 5/5 beheerst, 2/5 nog niet, 4/5 beheerst, 1/5 nog niet
        self.assertEqual(m["ECO-TEST-01"], {"beheerst": 2, "nog_niet": 2})
        # ECO-TEST-02 = vraag 3 (max 1): 1, 0, 1, 0
        self.assertEqual(m["ECO-TEST-02"], {"beheerst": 2, "nog_niet": 2})

    def test_backup(self):
        r = self.run_py("scripts/backup.py", "--bewaar", "1")
        self.assertEqual(r.returncode, 0, r.stderr)
        mappen = list((self.data / "backups").iterdir())
        self.assertEqual(len(mappen), 1)
        kopie = mappen[0] / "toetsevaluatie.db"
        con = sqlite3.connect(kopie)
        n = con.execute("SELECT COUNT(*) FROM evaluatieresultaten").fetchone()[0]
        con.close()
        self.assertEqual(n, 12)
        self.assertIn("kopie     Toetsconstructie/kennisbank", (mappen[0] / "VERSLAG.txt").read_text())


if __name__ == "__main__":
    unittest.main()
