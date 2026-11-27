# -*- coding: utf-8 -*-
"""Tests voor fase 2b: leerdoelcodes al bij het maken van de matrijs.

De keten die hier getest wordt:
  register in de centrale database -> keuzelijst in de matrijs (Toetsconstructie)
  -> regel LEERDOELCODES in het gebouwde correctievoorschrift -> de Toetsevaluatie leest de codes terug.

Draaien:  python3 -m unittest tests.test_fase2b -v
De bouwtest heeft Node.js met de module docx nodig (bouw/node_modules, of NODE_PATH); anders wordt hij overgeslagen.
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
TC = ROOT / "apps" / "Toetsconstructie"
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(TC / "bouw"))
from test_fase0 import maak_db  # noqa: E402
import leerdoelen  # noqa: E402

REGISTER = [("ECO-KRING-01", "Economie", "havo-vwo", "Kringloop", "Investeringen en besparingen in de kringloop", "actief"),
            ("ECO-KRING-02", "Economie", "havo-vwo", "Kringloop", "Inkomensverdeling met de Lorenzcurve", "actief"),
            ("ECO-M-GELD-01", "Economie", "mavo", "Geld", "Sparen en lenen", "actief"),
            ("BECO-KOST-01", "Bedrijfseconomie", "havo-vwo", "Kosten", "Kostprijs berekenen", "actief"),
            ("ECO-OUD-01", "Economie", "havo-vwo", "Oud", "Vervallen leerdoel", "vervallen")]


def maak_register(pad: Path) -> None:
    con = sqlite3.connect(pad)
    con.execute("CREATE TABLE IF NOT EXISTS leerdoelen_register (code TEXT PRIMARY KEY, vak TEXT NOT NULL, sector TEXT NOT NULL,"
                " thema TEXT, omschrijving TEXT NOT NULL, leerling_zin TEXT, status TEXT NOT NULL DEFAULT 'actief',"
                " aangemaakt TEXT, gewijzigd TEXT)")
    con.executemany("INSERT INTO leerdoelen_register (code, vak, sector, thema, omschrijving, status) VALUES (?, ?, ?, ?, ?, ?)",
                    REGISTER)
    con.commit()
    con.close()


def node_met_docx() -> dict | None:
    if not shutil.which("node"):
        return None
    for kandidaat in (os.environ.get("NODE_PATH"), str(TC / "bouw" / "node_modules")):
        if kandidaat and Path(kandidaat, "docx").exists():
            return dict(os.environ, NODE_PATH=kandidaat)
    return None


class Register(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "toetsevaluatie.db"
        maak_db(self.db, {"900001": [2, 3, 1]})
        maak_register(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_per_vak_en_sector(self):
        codes = lambda **k: [r["code"] for r in leerdoelen.register(pad=self.db, **k)]  # noqa: E731
        self.assertEqual(codes(vak="Economie", niveau="Havo"), ["ECO-KRING-01", "ECO-KRING-02"])
        self.assertEqual(codes(vak="Economie", niveau="Vwo"), ["ECO-KRING-01", "ECO-KRING-02"])   # havo en vwo delen
        self.assertEqual(codes(vak="Economie", niveau="Mavo"), ["ECO-M-GELD-01"])
        self.assertEqual(codes(vak="Bedrijfseconomie", niveau="Havo"), ["BECO-KOST-01"])
        self.assertEqual(codes(vak="Economie", niveau="Havo", zoek="Lorenz"), ["ECO-KRING-02"])
        self.assertNotIn("ECO-OUD-01", codes())                                                     # vervallen telt niet

    def test_zonder_register_een_lege_lijst(self):
        leeg = Path(self.tmp.name) / "leeg.db"
        maak_db(leeg, {})
        self.assertEqual(leerdoelen.register("Economie", "Havo", pad=leeg), [])
        self.assertEqual(leerdoelen.register("Economie", "Havo", pad=Path(self.tmp.name) / "bestaat_niet.db"), [])


class Keten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        for app in ("Toetsconstructie", "Toetsevaluatie", "Oefentool"):
            shutil.copytree(ROOT / "apps" / app, self.t / app, ignore=shutil.ignore_patterns("node_modules"))
        self.db = self.t / "Toetsevaluatie" / "toetsevaluatie.db"
        maak_db(self.db, {"900001": [2, 3, 1]})
        maak_register(self.db)
        (self.t / "Toetsconstructie" / "instellingen.json").write_text(json.dumps({"toetsevaluatie_db": str(self.db)}))
        self.env = dict(os.environ, TOETSCONSTRUCTIE_MAP=str(self.t / "Toetsconstructie"),
                        TOETSEVALUATIE_MAP=str(self.t / "Toetsevaluatie"), OEFENTOOL_MAP=str(self.t / "Oefentool"),
                        TOETSCIRKEL_DATA=str(self.t / "data"), HOME=str(self.t))
        self.env.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_keuzelijst_in_construeren(self):
        code = textwrap.dedent('''
            import json, sys
            sys.path.insert(0, sys.argv[1])
            from werkzeug.test import Client
            from cirkel.server import maak_app
            c = Client(maak_app()[0])
            r = c.get("/construeren/api/leerdoelenregister?vak=Economie&niveau=Havo")
            print("UITKOMST" + json.dumps([x["code"] for x in r.get_json()["leerdoelen"]]))
        ''')
        r = subprocess.run([sys.executable, "-c", code, str(ROOT)], cwd=self.t, env=self.env, capture_output=True, text=True,
                           timeout=180)
        regel = [x for x in r.stdout.splitlines() if x.startswith("UITKOMST")]
        self.assertTrue(regel, r.stderr[-3000:])
        self.assertEqual(json.loads(regel[0][8:]), ["ECO-KRING-01", "ECO-KRING-02"])

    def test_codes_gaan_via_het_correctievoorschrift_naar_de_evaluatie(self):
        env = node_met_docx()
        if env is None:
            self.skipTest("Node.js met de module docx ontbreekt")
        bron = TC / "voorbeelden" / "economie_5havo_AT5.11"
        werk = self.t / "bouwtest"
        shutil.copytree(bron, werk)
        toets = json.loads((werk / "toets_A.json").read_text(encoding="utf-8"))
        toets["matrijs"]["rijen"][0]["leerdoel_code"] = "ECO-KRING-01"
        toets["matrijs"]["rijen"][2]["leerdoel_code"] = "ECO-KRING-02"
        toets["matrijs"]["rijen"][3]["leerdoel_code"] = "ECO-VERZONNEN-09"   # staat niet in het register
        (werk / "toets_A.json").write_text(json.dumps(toets, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(["node", str(TC / "bouw" / "build.js"), str(werk / "toets_A.json"), str(werk / "uit")],
                           cwd=TC / "bouw", env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        cv = next((werk / "uit").glob("*_correctievoorschrift.docx"))
        lees = textwrap.dedent('''
            import json, sys
            from pathlib import Path
            sys.path.insert(0, sys.argv[1] + "/webapp"); sys.path.insert(0, sys.argv[1])
            import toets_evaluator as kern, register
            tekst = kern.lees_correctievoorschrift(Path(sys.argv[2]))
            print("REGEL" + json.dumps([r for r in tekst.splitlines() if r.startswith("LEERDOELCODES")]))
            print("UITKOMST" + json.dumps(register.codes_uit_cv(tekst)))
        ''')
        r = subprocess.run([sys.executable, "-c", lees, str(self.t / "Toetsevaluatie"), str(cv)], cwd=self.t, env=self.env,
                           capture_output=True, text=True, timeout=120)
        regel = [x for x in r.stdout.splitlines() if x.startswith("UITKOMST")]
        self.assertTrue(regel, r.stdout + r.stderr[-3000:])
        # Nummers zijn de vraagnummers uit de matrijs; een code die niet in het register staat wordt niet overgenomen.
        nrs = [toets["matrijs"]["rijen"][i]["nr"] for i in (0, 2)]
        self.assertEqual(json.loads(regel[0][8:]), {str(nrs[0]): "ECO-KRING-01", str(nrs[1]): "ECO-KRING-02"})


if __name__ == "__main__":
    unittest.main()
