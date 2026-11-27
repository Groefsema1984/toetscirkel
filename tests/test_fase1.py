# -*- coding: utf-8 -*-
"""Tests voor fase 1: de drie apps samen op één adres. Met kopieën van de apps en een verzonnen database.

Draaien:  python3 -m unittest tests.test_fase1 -v
Elke situatie draait in een eigen Python-proces, omdat de apps bij het laden modules en mappen vastleggen.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fase0 import maak_db  # noqa: E402

CONTROLE = textwrap.dedent(r'''
    import json, re, sys
    sys.path.insert(0, sys.argv[1])
    from werkzeug.test import Client
    from cirkel.server import maak_app
    wsgi, status = maak_app()
    c = Client(wsgi)
    uit = {"status": status, "paginas": {}}
    for u in ["/", "/construeren/", "/evalueren/", "/oefenen/", "/construeren/api/status", "/construeren/static/app.js",
              "/api/toetscirkel", "/bestaat/niet"]:
        r = c.get(u)
        tekst = r.get_data(as_text=True) if r.mimetype.startswith("text/") or r.mimetype == "application/json" else ""
        uit["paginas"][u] = {"code": r.status_code, "menu": 'id="toetscirkel-menu"' in tekst,
                             "attrs": re.findall(r'(?:href|src|action)="([^"]*)"', tekst)[:80]}
    r = c.get("/review?toets=X", headers={"Referer": "http://127.0.0.1:5050/evalueren/voortgang"})
    uit["vangnet"] = [r.status_code, r.headers.get("Location")]
    r = c.post("/api/x", headers={"Referer": "http://127.0.0.1:5050/oefenen/"})
    uit["vangnet_post"] = [r.status_code, r.headers.get("Location")]
    print("UITKOMST" + json.dumps(uit))
''')


class Fase1(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        for app in ("Toetsconstructie", "Toetsevaluatie", "Oefentool"):
            shutil.copytree(ROOT / "apps" / app, self.t / app)
        maak_db(self.t / "Toetsevaluatie" / "toetsevaluatie.db", {"900001": [2, 3, 1], "900002": [1, 1, 0]})

    def tearDown(self):
        self.tmp.cleanup()

    def draai(self, **extra):
        env = dict(os.environ, TOETSCONSTRUCTIE_MAP=str(self.t / "Toetsconstructie"),
                   TOETSEVALUATIE_MAP=str(self.t / "Toetsevaluatie"), OEFENTOOL_MAP=str(self.t / "Oefentool"),
                   TOETSCIRKEL_DATA=str(self.t / "data"), HOME=str(self.t), **extra)
        env.pop("ANTHROPIC_API_KEY", None)
        r = subprocess.run([sys.executable, "-c", CONTROLE, str(ROOT)], cwd=self.t, env=env,
                           capture_output=True, text=True, timeout=180)
        regel = [x for x in r.stdout.splitlines() if x.startswith("UITKOMST")]
        self.assertTrue(regel, r.stdout[-2000:] + r.stderr[-3000:])
        return json.loads(regel[0][len("UITKOMST"):])

    def test_drie_apps_op_een_adres(self):
        u = self.draai()
        for basis in ("/construeren", "/evalueren", "/oefenen"):
            self.assertTrue(u["status"][basis]["ok"], u["status"][basis])
        p = u["paginas"]
        for pad in ("/", "/construeren/", "/evalueren/", "/oefenen/"):
            self.assertEqual(p[pad]["code"], 200, pad)
            self.assertTrue(p[pad]["menu"], f"menubalk ontbreekt op {pad}")
        self.assertEqual(p["/construeren/api/status"]["code"], 200)
        self.assertEqual(p["/construeren/static/app.js"]["code"], 200)
        self.assertEqual(p["/bestaat/niet"]["code"], 404)

    def test_adressen_blijven_binnen_de_app(self):
        p = self.draai()["paginas"]
        toegestaan = ("/construeren", "/evalueren", "/oefenen")
        for pad in ("/construeren/", "/evalueren/", "/oefenen/"):
            for a in p[pad]["attrs"]:
                if a.startswith("/") and not a.startswith("//") and a not in ("/", "/itembank"):
                    self.assertTrue(a.startswith(toegestaan), f"{pad}: adres zonder voorvoegsel: {a}")
                self.assertNotIn("127.0.0.1:505", a, f"{pad}: link naar een oude losse app: {a}")
        self.assertIn("/construeren/static/app.js?v=5", p["/construeren/"]["attrs"])

    def test_vangnet_stuurt_door_naar_de_juiste_app(self):
        u = self.draai()
        self.assertEqual(u["vangnet"], [307, "/evalueren/review?toets=X"])
        self.assertEqual(u["vangnet_post"], [307, "/oefenen/api/x"])

    def test_een_kapotte_app_houdt_de_rest_niet_tegen(self):
        (self.t / "Toetsevaluatie" / "toetsevaluatie.db").unlink()
        u = self.draai()
        self.assertFalse(u["status"]["/evalueren"]["ok"])
        self.assertIn("toetsevaluatie.db", u["status"]["/evalueren"]["fout"])
        self.assertTrue(u["status"]["/construeren"]["ok"])
        self.assertEqual(u["paginas"]["/construeren/"]["code"], 200)
        self.assertEqual(u["paginas"]["/"]["code"], 200)


if __name__ == "__main__":
    unittest.main()
