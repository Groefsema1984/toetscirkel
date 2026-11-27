# -*- coding: utf-8 -*-
"""Tests voor fase 2a: één database en één itembank. Met kopieën van de apps en verzonnen gegevens.

Draaien:  python3 -m unittest tests.test_fase2 -v
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fase0 import maak_db  # noqa: E402

TOETS = "AT5.11_SE_5H_Economie"

START = textwrap.dedent(r'''
    import json, sys
    sys.path.insert(0, sys.argv[1])
    from werkzeug.test import Client
    from cirkel.server import maak_app
    wsgi, status = maak_app()
    c = Client(wsgi)
    import oefentool.instellingen as oi
    from cirkel.kern import itembank
    uit = {"status": status, "oefentool_db": str(oi.DB),
           "codes": {p: c.get(p).status_code for p in ("/", "/oefenen/", "/oefenen/itembank", "/itembank",
                                                        "/itembank?bron=construeren&tekst=Testvraag")},
           "api": json.loads(c.get("/api/toetscirkel").get_data(as_text=True)),
           "zoek": itembank.zoek(tekst="Testvraag")}
    print("UITKOMST" + json.dumps(uit, default=str))
''')


def maak_oefentool_db(pad: Path) -> None:
    sys.path.insert(0, str(ROOT / "apps" / "Oefentool"))
    schema = (ROOT / "apps" / "Oefentool" / "oefentool" / "opslag.py").read_text(encoding="utf-8")
    sql = schema.split('SCHEMA = """', 1)[1].split('"""', 1)[0]
    con = sqlite3.connect(pad)
    con.executescript(sql)
    con.execute("INSERT INTO bronnen (bron_id, pad, naam, map, status) VALUES (7, 'les/a.pdf', 'a.pdf', 'les', 'geindexeerd')")
    con.execute("INSERT INTO fragmenten (bron_id, soort, titel) VALUES (7, 'uitleg', 'Kringloop')")
    for i in range(1, 4):
        con.execute("INSERT INTO items (uid, vak, niveau, leerdoel_code, bloom, inhoud, status) VALUES (?, 'Economie', 'havo',"
                    " 'ECO-TEST-01', 'Toepassen', ?, 'goedgekeurd')",
                    (f"oef-{i}", json.dumps({"context": "", "vraag": f"Oefenvraag {i}", "punten": 2, "cv": {}, "hints": []})))
    con.execute("INSERT INTO item_gebruik VALUES ('oef-1', 'opd1', 'set A', NULL, 1)")
    con.execute("INSERT INTO logboek VALUES ('2026-11-27T10:00:00', 'test', 'x', 1, 2)")
    con.commit()
    con.close()


class Fase2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.t = Path(self.tmp.name)
        for app in ("Toetsconstructie", "Toetsevaluatie", "Oefentool"):
            shutil.copytree(ROOT / "apps" / app, t / app)
        self.centraal = t / "Toetsevaluatie" / "toetsevaluatie.db"
        maak_db(self.centraal, {"900001": [2, 3, 1], "900002": [1, 1, 0], "900003": [2, 2, 1]})
        self.oefendb = t / "Oefentool" / "oefentool.db"
        maak_oefentool_db(self.oefendb)
        # Een toetsproject in Toetsconstructie dat bij de afgenomen toets hoort, met één vraag in de vragenbank.
        pr = t / "Toetsconstructie" / "toetsen" / "test_project"
        pr.mkdir(parents=True)
        (pr / "project.json").write_text(json.dumps({"id": "test_project", "vak": "Economie", "niveau": "Havo",
                                                     "at": "AT5.11", "soort": "schoolexamen"}), encoding="utf-8")
        bank = t / "Toetsconstructie" / "kennisbank" / "vragenbank.json"
        vragen = json.loads(bank.read_text(encoding="utf-8"))
        self.n_bank = len(vragen) + 1
        vragen.append({"id": "test_project:A:1", "project": "test_project", "vak": "Economie", "niveau": "Havo",
                       "versie": "A", "nr": 1, "punten": 2, "leerdoel": "Testleerdoel", "vraag": "Testvraag over de kringloop",
                       "afnames": []})
        bank.write_text(json.dumps(vragen), encoding="utf-8")
        self.env = dict(os.environ, TOETSCONSTRUCTIE_MAP=str(t / "Toetsconstructie"),
                        TOETSEVALUATIE_MAP=str(t / "Toetsevaluatie"), OEFENTOOL_MAP=str(t / "Oefentool"),
                        TOETSCIRKEL_DATA=str(t / "data"), HOME=str(t))
        self.env.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self):
        r = subprocess.run([sys.executable, "-c", START, str(ROOT)], cwd=self.t, env=self.env,
                           capture_output=True, text=True, timeout=180)
        regel = [x for x in r.stdout.splitlines() if x.startswith("UITKOMST")]
        self.assertTrue(regel, r.stdout[-2000:] + r.stderr[-3000:])
        return json.loads(regel[0][len("UITKOMST"):])

    def gouden(self, actie):
        r = subprocess.run([sys.executable, "tests/gouden_toets.py", actie, TOETS], cwd=ROOT, env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tel(self, pad, tabel):
        con = sqlite3.connect(pad)
        try:
            return con.execute(f"SELECT COUNT(*) FROM {tabel}").fetchone()[0]
        finally:
            con.close()

    def test_oefentool_gaat_naar_de_centrale_database(self):
        voor = {t: self.tel(self.oefendb, t) for t in ("bronnen", "fragmenten", "items", "item_gebruik", "logboek")}
        # Zoals op de Mac: de Toetsevaluatie heeft zijn database al eens bijgewerkt (cijferview, leerdoelkolommen).
        r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import database, register;"
                            " database.zorg_voor_normering(); database.zorg_voor_leerdoelen(); register.zorg_voor_register()",
                            str(self.t / "Toetsevaluatie" / "webapp")], cwd=self.t, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.gouden("vastleggen")
        u = self.start()
        self.assertEqual(u["oefentool_db"], str(self.centraal))
        for t, n in voor.items():
            self.assertEqual(self.tel(self.centraal, t), n, t)
        self.assertFalse(self.oefendb.exists())
        self.assertTrue((self.t / "Oefentool" / "oefentool.voor_fase2.db").exists())
        self.assertEqual(len(list((self.t / "data" / "backups").iterdir())), 1, "vooraf één back-up")
        for p, code in u["codes"].items():
            self.assertEqual(code, 200, p)
        self.gouden("controleren")                   # de afgenomen toets is precies hetzelfde gebleven

    def test_tweede_start_doet_niets_dubbel(self):
        self.start()
        u = self.start()
        self.assertEqual(self.tel(self.centraal, "items"), 3)
        self.assertEqual(len(list((self.t / "data" / "backups").iterdir())), 1, "geen nieuwe back-up bij tweede start")
        self.assertIn("al", u["api"]["kern"]["database"])

    def test_itembank_en_koppeling(self):
        u = self.start()
        ib = u["api"]["kern"]["itembank"]
        self.assertEqual(ib["constructie_vragen"], self.n_bank)
        self.assertEqual(ib["itembank"], {"construeren": self.n_bank, "evalueren": 3, "oefenen": 3})
        vraag = [r for r in u["zoek"] if r["bron"] == "construeren"][0]
        self.assertEqual(vraag["toets_id"], TOETS)                  # project gekoppeld op AT-nummer, vak, niveau, soort
        self.assertEqual(vraag["leerdoel_code"], "ECO-TEST-01")     # leerdoelcode uit de evaluatie van dezelfde vraag
        con = sqlite3.connect(self.centraal)
        p = con.execute("SELECT p_waarde FROM itembank WHERE bron = 'evalueren' AND vraagnr = 1").fetchone()[0]
        con.close()
        self.assertAlmostEqual(p, (2 + 1 + 2) / 3 / 2)

    def test_mislukte_omzetting_laat_alles_staan(self):
        con = sqlite3.connect(self.centraal)
        con.execute("CREATE TABLE items (x INTEGER)")              # botsende tabel: omzetten moet stoppen
        con.commit()
        con.close()
        u = self.start()
        self.assertTrue(u["api"]["kern"]["database_fout"])
        self.assertTrue(self.oefendb.exists())
        self.assertEqual(u["oefentool_db"], str(self.oefendb))      # oefentool blijft zijn eigen database gebruiken
        self.assertEqual(self.tel(self.oefendb, "items"), 3)
        con = sqlite3.connect(self.centraal)
        self.assertFalse(con.execute("SELECT 1 FROM sqlite_master WHERE name = 'bronnen'").fetchone())
        con.close()
        self.assertEqual(u["codes"]["/oefenen/"], 200)


if __name__ == "__main__":
    unittest.main()
