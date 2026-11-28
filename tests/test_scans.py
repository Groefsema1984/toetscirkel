# -*- coding: utf-8 -*-
"""Tests voor fase 3: een scan in 'ToetsCirkel Scans' herkennen en de keten vanzelf laten lopen.

Draaien:  python3 -m unittest tests.test_scans -v
Claude wordt vervangen door nep-functies; er gaat niets naar buiten.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fase0 import maak_db  # noqa: E402
from cirkel.kern import scans  # noqa: E402

UUR = 3600


def maak_toets(toetsen: Path, pid: str, at: str, niveau: str, klas: str, vak: str, datum: str, versies=("A", "B")):
    d = toetsen / pid
    (d / "uit").mkdir(parents=True)
    (d / "project.json").write_text(json.dumps({"id": pid, "naam": pid.replace("_", " "), "vak": vak, "niveau": niveau,
                                                "klas": klas, "at": at, "status": "gebouwd"}))
    jaar = klas.split()[0]
    for v in versies:
        basis = f"{at}_SE_{niveau.lower()}{jaar}_{vak.lower()}_versie_{v}"
        (d / f"toets_{v}.json").write_text(json.dumps({"meta": {"vak": vak, "niveau": niveau, "klas": klas, "at": at,
                                                                "versie": v, "datum": datum, "minuten": 100,
                                                                "bestandsbasis": basis}}))
        (d / "uit" / f"{basis}_correctievoorschrift.docx").write_bytes(b"cv " + v.encode())


def oud(pad: Path, uren: float = 2) -> Path:
    t = time.time() - uren * UUR
    for p in [pad, *pad.rglob("*")] if pad.is_dir() else [pad]:
        os.utime(p, (t, t))
    return pad


class Herkennen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.toetsen = self.t / "toetsen"
        maak_toets(self.toetsen, "Groei_5h", "AT5.21", "Havo", "5 havo", "Economie", "10 november 2026")
        maak_toets(self.toetsen, "Markt_5h", "AT5.22", "Havo", "5 havo", "Economie", "1 maart 2027", versies=("A",))
        maak_toets(self.toetsen, "NV_5v", "AT5.21", "Vwo", "5 vwo", "Bedrijfseconomie", "10 november 2026", versies=("A",))
        self.inbox = self.t / "inbox"
        self.inbox.mkdir()
        self.alle = scans.gebouwde_toetsen(self.toetsen)

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, naam: str) -> Path:
        p = self.inbox / naam
        p.write_bytes(b"%PDF-1.4")
        return oud(p)

    def test_gebouwde_toetsen_per_versie(self):
        self.assertEqual(len(self.alle), 4)
        groei = [t for t in self.alle if t.project == "Groei_5h"]
        self.assertEqual({t.versie for t in groei}, {"A", "B"})
        self.assertEqual({t.klas for t in self.alle}, {"5H", "5V"})
        self.assertEqual(groei[0].datum, date(2026, 11, 10))

    def test_bestandsnaam_met_at_en_versie(self):
        h = scans.herken(self.scan("AT5.21 5H versie B.pdf"), self.alle)
        self.assertTrue(h.ok, h.reden)
        self.assertEqual(h.toets_id, "AT5.21_SE_5H_Economie_vB")
        self.assertTrue(h.cv.name.endswith("versie_B_correctievoorschrift.docx"))
        self.assertEqual(h.toets.minuten, 100)

    def test_vwo_krijgt_tweefasen_zoals_het_uploadscherm(self):
        h = scans.herken(self.scan("AT5.21 beco 5V.pdf"), self.alle)
        self.assertTrue(h.ok, h.reden)
        self.assertEqual(h.toets_id, "AT5.21_SE_5V_Bedrijfseconomie_tweefasen")
        h = scans.herken(self.scan("AT5.21 beco 5V geen tweefasen.pdf"), self.alle)
        self.assertEqual(h.toets_id, "AT5.21_SE_5V_Bedrijfseconomie")

    def test_twee_versies_zonder_versie_is_twijfel(self):
        h = scans.herken(self.scan("AT5.21 5H.pdf"), self.alle)
        self.assertFalse(h.ok)
        self.assertIn("versie A", h.reden)

    def test_onbekend_at_is_twijfel(self):
        h = scans.herken(self.scan("AT9.99 5H.pdf"), self.alle)
        self.assertFalse(h.ok)
        self.assertIn("AT9.99", h.reden)

    def test_eerste_pagina_en_leerlingnummers_met_datum(self):
        # Kale scannernaam. Claude leest alleen leerlingnummers; de klas komt uit de klassenlijst, de toets uit de datum.
        p = self.scan("Scan_0001.pdf")
        t = datetime(2026, 11, 12, 10).timestamp()
        os.utime(p, (t, t))
        gelezen = []

        def kop(pdf):
            gelezen.append(pdf.name)
            return {"at_code": "", "versie": "A", "titel": "", "leerlingnummers": ["900001", "900002", "123"]}
        klassen = {"900001": "5H2", "900002": "5H2"}
        h = scans.herken(p, self.alle, lees_kop=kop, klas_van=klassen.get)
        self.assertEqual(gelezen, ["Scan_0001.pdf"])
        self.assertTrue(h.ok, h.reden)
        self.assertEqual(h.toets_id, "AT5.21_SE_5H_Economie_vA")       # AT5.22 ligt maanden later

    def test_zonder_enig_bewijs_wordt_niets_gekozen(self):
        alleen = [t for t in self.alle if t.project == "Markt_5h"]
        h = scans.herken(self.scan("Scan_0002.pdf"), alleen, lees_kop=lambda pdf: {})
        self.assertFalse(h.ok)
        h = scans.herken(self.scan("Scan_0003.pdf"), alleen)
        self.assertFalse(h.ok)

    def test_map_met_eigen_correctievoorschrift(self):
        m = self.inbox / "AT5.11_SE_5H_Economie"
        m.mkdir()
        (m / "scan.pdf").write_bytes(b"%PDF")
        (m / "correctievoorschrift AT5.11.docx").write_bytes(b"cv")
        h = scans.herken(oud(m), self.alle, lees_toetsmap=lambda naam: None)
        self.assertTrue(h.ok, h.reden)
        self.assertEqual((h.toets_id, h.scan.name, h.cv.name), ("AT5.11_SE_5H_Economie", "scan.pdf", "correctievoorschrift AT5.11.docx"))

    def test_voorblad_wint(self):
        t = next(x for x in self.alle if x.project == "Groei_5h" and x.versie == "B")
        code = scans.voorblad_code(t, date(2026, 11, 20))
        self.assertEqual(scans.lees_voorblad("rommel\n" + code.replace(" / ", "/") + "\n")["project"], "Groei_5h")
        h = scans.herken(self.scan("Scan_0009.pdf"), self.alle, lees_kop=lambda pdf: {"voorblad": code, "at_code": "AT9.99"})
        self.assertTrue(h.ok, h.reden)
        self.assertTrue(h.voorblad)
        self.assertEqual((h.toets_id, h.datum), ("AT5.21_SE_5H_Economie_vB", date(2026, 11, 20)))
        h = scans.herken(self.scan("Scan_0010.pdf"), self.alle, tekstlaag=code)          # tekstlaag: zonder Claude
        self.assertTrue(h.ok and h.voorblad)
        h = scans.herken(self.scan("Scan_0011.pdf"), self.alle, tekstlaag="TC-VOORBLAD: Weg_1234 / A / 5H / 2026-11-20")
        self.assertFalse(h.ok)
        self.assertIn("Weg_1234", h.reden)

    def test_alleen_scans_die_klaar_zijn(self):
        self.scan("oud.pdf")
        (self.inbox / "nieuw.pdf").write_bytes(b"%PDF")                     # OneDrive is nog bezig
        (self.inbox / "notities.txt").write_text("x")
        oud(self.inbox / "notities.txt")
        (self.inbox / scans.TWIJFEL).mkdir()
        oud(self.inbox / scans.TWIJFEL)
        self.assertEqual([p.name for p in scans.klaar_in_inbox(self.inbox)], ["oud.pdf"])

    def test_verplaats_met_reden(self):
        doel = scans.verplaats(self.scan("x.pdf"), self.inbox, scans.TWIJFEL, "zomaar")
        self.assertTrue(doel.exists())
        self.assertFalse((self.inbox / "x.pdf").exists())
        self.assertEqual(doel.with_suffix(".txt").read_text().strip(), "zomaar")


KETEN = textwrap.dedent(r'''
    import json, sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from cirkel.server import maak_app
    import paden
    wsgi, status = maak_app()
    import pipeline, sessie
    from cirkel.scanwacht import Scanwacht
    gestart, gepubliceerd = [], []
    pipeline.start_analyse = lambda tid: gestart.append(tid)
    pipeline.start_publicatie = lambda tid: gepubliceerd.append(tid) or 1
    w = Scanwacht(paden.SCANS, paden.TOETSCONSTRUCTIE / "toetsen")
    w._lees_kop = lambda pdf: {}
    uit = {}
    uit["verwerk"] = w.verwerk(paden.SCANS / "AT5.21 5H versie A.pdf")
    uit["twijfel"] = w.verwerk(paden.SCANS / "Scan_kaal.pdf")
    s = sessie.lees("AT5.21_SE_5H_Economie_vA")
    uit["sessie"] = {k: s.get(k) for k in ("automatisch", "cv_bestand", "duur", "klas", "vak", "project")}
    uit["gestart"] = gestart
    uit["mappen"] = sorted(p.name.split("_", 2)[-1] for p in (paden.SCANS / "Verwerkt").iterdir()) + \
                    sorted(p.name.split("_", 2)[-1] for p in (paden.SCANS / "Twijfel").iterdir())
    # analyse klaar: één punt open -> wachten; daarna beslist -> automatisch definitief
    with sessie.bewerk("AT5.21_SE_5H_Economie_vA") as s2:
        s2["status"] = "review"
        s2["leerlingen"] = {"900001": {"status": "klaar", "koppeling": {}, "vragen": {"1": {
            "nr": 1, "max": 2, "docent": 2.0, "ai": 0.0, "zekerheid": "hoog", "besluit": None}}}}
    uit["wacht"] = [x["toets_id"] for x in w.wacht_op_docent()]
    uit["publiceer_1"] = w.publiceer_klaar()
    with sessie.bewerk("AT5.21_SE_5H_Economie_vA") as s2:
        s2["leerlingen"]["900001"]["vragen"]["1"].update(besluit="docent", definitief=2.0)
    uit["publiceer_2"] = w.publiceer_klaar()
    from cirkel.kern import scans
    import fitz
    t = next(x for x in scans.gebouwde_toetsen(paden.TOETSCONSTRUCTIE / "toetsen") if x.versie == "B")
    code = scans.voorblad_code(t, __import__("datetime").date(2026, 11, 20))
    d = fitz.open()
    for i in range(3):
        d.new_page().insert_text((72, 72), code if i == 0 else f"werk {i}")
    d.save(str(paden.SCANS / "Scan_voorblad.pdf"))
    uit["voorblad"] = w.verwerk(paden.SCANS / "Scan_voorblad.pdf")
    with fitz.open(str(sessie.map_van("AT5.21_SE_5H_Economie_vB") / "scan.pdf")) as d2:
        uit["paginas_zonder_voorblad"] = d2.page_count
    from werkzeug.test import Client
    c = Client(wsgi)
    uit["voorblad_keuze"] = c.get("/voorblad").status_code
    uit["voorblad_print"] = code in c.get("/voorblad?t=Groei_5h|B&datum=2026-11-20").get_data(as_text=True)
    uit["log"] = [(r["bestand"], r["status"]) for r in scans.logboek()]
    print("UITKOMST" + json.dumps(uit))
''')


class Keten(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        for app in ("Toetsconstructie", "Toetsevaluatie", "Oefentool"):
            shutil.copytree(ROOT / "apps" / app, self.t / app, ignore=shutil.ignore_patterns("node_modules"))
        maak_db(self.t / "Toetsevaluatie" / "toetsevaluatie.db", {"900001": [2, 3, 1]})
        maak_toets(self.t / "Toetsconstructie" / "toetsen", "Groei_5h", "AT5.21", "Havo", "5 havo", "Economie", "10 november 2026")
        self.inbox = self.t / "inbox"
        self.inbox.mkdir()
        for naam in ("AT5.21 5H versie A.pdf", "Scan_kaal.pdf"):
            (self.inbox / naam).write_bytes(b"%PDF-1.4")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_tot_definitief(self):
        env = dict(os.environ, TOETSCONSTRUCTIE_MAP=str(self.t / "Toetsconstructie"),
                   TOETSEVALUATIE_MAP=str(self.t / "Toetsevaluatie"), OEFENTOOL_MAP=str(self.t / "Oefentool"),
                   TOETSCIRKEL_DATA=str(self.t / "data"), TOETSCIRKEL_SCANS=str(self.inbox), HOME=str(self.t),
                   TOETSCIRKEL_GEEN_CLAUDE="1", TOETSCIRKEL_GEEN_SCANS="1")
        env.pop("ANTHROPIC_API_KEY", None)
        r = subprocess.run([sys.executable, "-c", KETEN, str(ROOT)], cwd=self.t, env=env, capture_output=True, text=True,
                           timeout=180)
        regel = [x for x in r.stdout.splitlines() if x.startswith("UITKOMST")]
        self.assertTrue(regel, r.stdout[-2000:] + r.stderr[-4000:])
        uit = json.loads(regel[0][8:])
        self.assertEqual((uit["verwerk"], uit["twijfel"]), ("gestart", "twijfel"))
        self.assertEqual(uit["gestart"], ["AT5.21_SE_5H_Economie_vA", "AT5.21_SE_5H_Economie_vB"])
        self.assertEqual(uit["sessie"], {"automatisch": True, "cv_bestand": "correctievoorschrift.docx", "duur": 100,
                                         "klas": "5H", "vak": "Economie", "project": "Groei_5h"})
        self.assertEqual(uit["mappen"], ["AT5.21 5H versie A.pdf", "Scan_kaal.pdf", "Scan_kaal.txt"])
        self.assertEqual(uit["wacht"], ["AT5.21_SE_5H_Economie_vA"])
        self.assertEqual(uit["publiceer_1"], [])                       # verschil docent/AI: eerst de docent
        self.assertEqual(uit["publiceer_2"], ["AT5.21_SE_5H_Economie_vA"])
        self.assertEqual((uit["voorblad"], uit["paginas_zonder_voorblad"]), ("gestart", 2))
        self.assertEqual((uit["voorblad_keuze"], uit["voorblad_print"]), (200, True))
        self.assertEqual([s for _, s in uit["log"]], ["gestart", "definitief", "twijfel", "gestart"])


if __name__ == "__main__":
    unittest.main()
