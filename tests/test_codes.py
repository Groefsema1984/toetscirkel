# -*- coding: utf-8 -*-
"""Tests voor het aanvullen van leerdoelcodes in bestaande matrijzen (uit het nakijken, en met Claude).

Draaien:  python3 -m unittest tests.test_codes -v
Claude wordt hier vervangen door een nep-functie: er gaat niets naar buiten.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fase0 import maak_db  # noqa: E402
from test_fase2b import maak_register  # noqa: E402
from cirkel.kern import codes, itembank  # noqa: E402

TOETS = "AT5.11_SE_5H_Economie"


def matrijs(rijen):
    return {"meta": {}, "rijen": [dict(nr=nr, leerdoel=ld, punten=2, **({"leerdoel_code": c} if c else {}))
                                  for nr, ld, c in rijen]}


class Aanvullen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.db = t / "toetsevaluatie.db"
        maak_db(self.db, {"900001": [2, 3, 1]})
        maak_register(self.db)
        con = sqlite3.connect(self.db)
        con.execute("ALTER TABLE toetsmatrijs ADD COLUMN koppel_status TEXT")
        # vraag 1: bevestigd en in het register; vraag 2: alleen 'controleren'; vraag 3: code die niet in het register staat
        con.execute("UPDATE toetsmatrijs SET leerdoel_code = 'ECO-KRING-01', koppel_status = 'bevestigd' WHERE vraagnummer = 1")
        con.execute("UPDATE toetsmatrijs SET leerdoel_code = 'ECO-KRING-02', koppel_status = 'controleren' WHERE vraagnummer = 2")
        con.execute("UPDATE toetsmatrijs SET leerdoel_code = 'ECO-WEG-01', koppel_status = 'bevestigd' WHERE vraagnummer = 3")
        con.commit()
        self.toetsen = t / "toetsen"
        self.maak_project("afgenomen", "AT5.11", matrijs([(1, "Kringloop", None), (2, "Lorenz", None), (3, "X", None)]))
        self.maak_project("nieuw", "AT5.12", matrijs([(1, "Investeren en sparen in de kringloop", None),
                                                      (2, "Iets heel anders", None),
                                                      (3, "Lorenzcurve", "ECO-KRING-02")]))
        con.executescript(itembank.SCHEMA)
        itembank.koppel_toetsen(con, itembank.projecten_uit_constructie(self.toetsen))
        con.commit()
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def maak_project(self, pid, at, m):
        d = self.toetsen / pid
        d.mkdir(parents=True)
        (d / "project.json").write_text(json.dumps({"id": pid, "naam": pid, "vak": "Economie", "niveau": "Havo", "at": at,
                                                    "soort": "schoolexamen"}))
        (d / "matrijs.json").write_text(json.dumps(m))

    def lees(self, pid):
        return {r["nr"]: r.get("leerdoel_code") for r in
                json.loads((self.toetsen / pid / "matrijs.json").read_text())["rijen"]}

    def test_uit_het_nakijken_alleen_wat_zeker_is(self):
        uit = codes.vul_aan(self.toetsen, self.db)
        self.assertEqual(uit["vragen"], 1)
        self.assertEqual(self.lees("afgenomen"), {1: "ECO-KRING-01", 2: None, 3: None})
        self.assertEqual(len(list((self.toetsen / "afgenomen" / "versies").glob("*_toetscirkel_matrijs.json"))), 1)
        self.assertEqual(codes.vul_aan(self.toetsen, self.db)["vragen"], 0)       # tweede keer: niets meer te doen

    def test_met_claude_alleen_hoge_zekerheid(self):
        vragen = []

        def nep(systeem, inhoud, schema, max_tokens):
            tekst = inhoud[0]["text"]
            vragen.append(tekst)
            self.assertIn("ECO-KRING-01", tekst)                                    # register gaat mee
            self.assertNotIn("ECO-M-GELD-01", tekst)                                # mavo-register niet
            vraagdeel = tekst.split("\n\nVragen", 1)[1]
            self.assertNotIn("| Lorenzcurve |", vraagdeel)                          # vraag met code wordt niet gevraagd
            if "Investeren en sparen" not in vraagdeel:                             # het afgenomen project: niets zeker
                return {"vragen": []}
            return {"vragen": [{"nr": 1, "code": "ECO-KRING-01", "zekerheid": "hoog", "toelichting": ""},
                               {"nr": 2, "code": "GEEN", "zekerheid": "laag", "toelichting": "past nergens"}]}

        codes.vul_aan(self.toetsen, self.db)
        uit = codes.koppel_met_claude(nep, self.toetsen, self.db)
        self.assertEqual(self.lees("nieuw"), {1: "ECO-KRING-01", 2: None, 3: "ECO-KRING-02"})
        twijfel = {(r["project"], r["nr"]) for r in codes.twijfelgevallen(self.db)}
        self.assertIn(("nieuw", 2), twijfel)
        self.assertNotIn(("nieuw", 1), twijfel)
        self.assertGreaterEqual(uit["twijfel"], 1)
        aantal = len(vragen)
        codes.koppel_met_claude(nep, self.toetsen, self.db)                         # ongewijzigd: niet opnieuw vragen
        self.assertEqual(len(vragen), aantal)

    def test_verzonnen_code_wordt_niet_ingevuld(self):
        def nep(systeem, inhoud, schema, max_tokens):
            return {"vragen": [{"nr": 1, "code": "ECO-VERZONNEN-01", "zekerheid": "hoog", "toelichting": ""}]}
        codes.koppel_met_claude(nep, self.toetsen, self.db)
        self.assertIsNone(self.lees("nieuw")[1])


if __name__ == "__main__":
    unittest.main()
