# -*- coding: utf-8 -*-
"""Fase 3: de keten vanaf een scan in OneDrive, op de achtergrond.

Elke halve minuut kijkt ToetsCirkel in de map 'ToetsCirkel Scans':
1. een scan die al een minuut niet verandert (OneDrive is klaar) wordt herkend (zie kern/scans.py);
2. herkend: de analyse van de Toetsevaluatie start, net alsof je de scan zelf had geüpload. Bestaat de analyse van
   die toets al (tweede klas, vergeten blad), dan worden de pagina's bijgescand. De scan gaat naar Verwerkt/;
3. niet zeker: de scan gaat naar Twijfel/ met een .txt ernaast waarin staat waarom. Niets wordt geraden;
4. is een automatisch gestarte analyse klaar en staat er niets open (geen twijfel over wiens werk het is, geen groot
   verschil tussen jouw score en die van de AI), dan wordt de toets definitief gemaakt (feedback, rapportage,
   leerdoelen). Staat er wel iets open, dan wacht de keten op jou in Evalueren › Valideren; zodra jij het laatste
   punt hebt beslist, gaat hij vanzelf verder.

Handmatig uploaden in Evalueren blijft gewoon werken; die analyses worden niet automatisch gepubliceerd.
Claude werkt via je abonnement; API-tegoed alleen met jouw toestemming (dat regelt de Toetsevaluatie al).
"""
from __future__ import annotations

import logging
import shutil
import sys
import threading
import time
from pathlib import Path

from .kern import scans

log = logging.getLogger("toetscirkel.scans")
INTERVAL = 30
PAUZE_NA_FOUT = 600

SCHEMA_KOP = {
    "type": "object", "required": ["voorblad", "at_code", "versie", "titel", "leerlingnummers"],
    "properties": {"voorblad": {"type": "string"}, "at_code": {"type": "string"}, "versie": {"type": "string"},
                   "titel": {"type": "string"},
                   "leerlingnummers": {"type": "array", "items": {"type": "string"}}}}
SYSTEEM_KOP = ("Je ziet de eerste pagina van een stapel gescand toetswerk. Staat er een regel die begint met 'TC-VOORBLAD:', "
               "geef die hele regel dan letterlijk in 'voorblad' (anders leeg). Lees verder alleen wat gedrukt of geschreven staat "
               "over de toets en de leerlingnummers: AT-nummer of toetsnummer (bijv. AT5.21), versie (A of B), de titel van "
               "de toets, en de leerlingnummers (alleen cijfers) die je ziet. Geef geen namen. Laat een veld leeg als het er "
               "niet staat; verzin niets.")


def _te():
    """De modules van de Toetsevaluatie, zoals die bij het starten van ToetsCirkel zijn geladen."""
    m = sys.modules
    return m["sessie"], m["pipeline"], m["scores"], m["abonnement"], m["config"].kern


class Scanwacht:
    def __init__(self, inbox: Path, toetsen_map: Path):
        self.inbox, self.toetsen_map = inbox, toetsen_map
        self.staat = {"actief": False, "inbox": str(inbox), "fout": "", "laatste": "", "bezig": ""}
        self._gezien: dict[Path, tuple] = {}
        self._pauze: dict[Path, float] = {}          # na een fout (bijv. abonnement op) even niet opnieuw

    # ---------------------------------------------------------------- Claude en klassenlijsten
    def _lees_kop(self, pdf: Path) -> dict:
        sessie, pipeline, _, abonnement, kern = _te()
        with pipeline.PDF_SLOT, kern.fitz.open(str(pdf)) as d:
            beelden = [kern._render(d, 0, 1500, 70)] if d.page_count else []     # één pagina: zuinig
        inhoud = [kern._beeldblok(b) for b in beelden] + [{"type": "text", "text": "Wat staat er over de toets en welke leerlingnummers zie je?"}]
        return abonnement.vraag_json(None, [{"type": "text", "text": SYSTEEM_KOP}], inhoud, SCHEMA_KOP, 2000)

    def _klas_van(self):
        *_, kern = _te()
        lijst, _ = kern.laad_klassenlijst(Path(kern.KLASSENLIJST_MAP) / "_bestaat_niet_", None)
        return lambda nr: (lijst.get(nr).klas or lijst.get(nr).groep) if lijst.get(nr) else ""

    def _tekstlaag(self, pdf: Path) -> str:
        _, pipeline, _, _, kern = _te()
        try:
            with pipeline.PDF_SLOT, kern.fitz.open(str(pdf)) as d:
                return d.load_page(0).get_text()[:3000] if d.page_count else ""
        except Exception:  # noqa: BLE001
            return ""

    def _kopieer(self, h, doel: Path) -> None:
        """Kopieer de scan; het voorblad (pagina 1) gaat er niet mee in, want dat is geen leerlingwerk."""
        if not h.voorblad:
            shutil.copy2(h.scan, doel)
            return
        _, pipeline, _, _, kern = _te()
        with pipeline.PDF_SLOT, kern.fitz.open(str(h.scan)) as d:
            if d.page_count < 2:
                raise ValueError("De scan bevat alleen het voorblad.")
            d.delete_page(0)
            d.save(str(doel))

    # ---------------------------------------------------------------- één scan
    def verwerk(self, pad: Path) -> str:
        sessie, pipeline, _, _, kern = _te()
        scan = pad if pad.is_file() else None
        h = scans.herken(pad, scans.gebouwde_toetsen(self.toetsen_map),
                         tekstlaag=self._tekstlaag(scan) if scan else "", lees_kop=self._lees_kop,
                         klas_van=self._klas_van(), lees_toetsmap=kern.lees_toetsmap)
        if not h.ok:
            scans.verplaats(pad, self.inbox, scans.TWIJFEL, h.reden)
            scans.log_scan(pad.name, "twijfel", reden=h.reden)
            log.warning("Scan %s: twijfel: %s", pad.name, h.reden)
            return "twijfel"
        toets_id = h.toets_id
        info = kern.lees_toetsmap(toets_id)
        project = h.toets.project if h.toets else ""
        s = sessie.lees(toets_id)
        if s:
            if pipeline.loopt(toets_id) or s["status"] not in ("review", "gepubliceerd") \
                    or any(l["status"] == "bezig" for l in s["leerlingen"].values()):
                if s["status"] == "fout" and not pipeline.loopt(toets_id):
                    reden = (f"De analyse van {toets_id} is eerder vastgelopen ({s.get('fout', '')[:200]}). Open die in "
                             "Evalueren en kies Hervatten; zet de scan daarna terug.")
                    scans.verplaats(pad, self.inbox, scans.TWIJFEL, reden)
                    scans.log_scan(pad.name, "twijfel", toets_id, project, reden)
                    return "twijfel"
                return "later"                       # er loopt nog iets: volgende ronde opnieuw
            doel = sessie.map_van(toets_id) / f"bijscan_{time.strftime('%Y%m%d_%H%M%S')}.pdf"
            self._kopieer(h, doel)
            with sessie.bewerk(toets_id) as s2:
                s2["automatisch"] = True
            aantal = pipeline.start_aanvulling(toets_id, doel)
            scans.verplaats(pad, self.inbox, scans.VERWERKT)
            scans.log_scan(pad.name, "bijgescand", toets_id, project, f"{aantal} pagina's toegevoegd aan de bestaande analyse")
            log.info("Scan %s: %s pagina's bijgescand bij %s", pad.name, aantal, toets_id)
            return "bijgescand"

        smap = sessie.map_van(toets_id)
        smap.mkdir(parents=True, exist_ok=True)
        self._kopieer(h, smap / "scan.pdf")
        cv_naam = "correctievoorschrift" + h.cv.suffix.lower()
        shutil.copy2(h.cv, smap / cv_naam)
        if h.klassenlijst:
            shutil.copy2(h.klassenlijst, smap / ("_klassenlijst" + h.klassenlijst.suffix.lower()))
        sessie.nieuw(toets_id, {"vak": info.vak, "klas": info.klas, "niveau": info.niveau, "tweefasen": info.tweefasen,
                                "datum": h.datum.isoformat(), "duur": h.toets.minuten if h.toets else None,
                                "scan_bestand": "scan.pdf", "cv_bestand": cv_naam,
                                "automatisch": True, "bron_scan": pad.name, "project": project})
        pipeline.start_analyse(toets_id)
        scans.verplaats(pad, self.inbox, scans.VERWERKT)
        scans.log_scan(pad.name, "gestart", toets_id, project,
                       f"herkend{' aan het voorblad' if h.voorblad else ''} als {h.toets.naam}, versie {h.toets.versie or '-'}" if h.toets else "map met correctievoorschrift")
        log.info("Scan %s: analyse gestart als %s", pad.name, toets_id)
        return "gestart"

    # ---------------------------------------------------------------- automatisch definitief maken
    def publiceer_klaar(self) -> list[str]:
        sessie, pipeline, scores, _, _ = _te()
        gedaan = []
        for kort in sessie.alle():
            toets_id = kort["toets_id"]
            if kort["status"] != "review" or pipeline.loopt(toets_id):
                continue
            s = sessie.lees(toets_id)
            if not s or not s.get("automatisch") or s.get("toestemming_nodig"):
                continue
            if scores.open_punten(s)["totaal"] or not any(l["status"] == "klaar" for l in s["leerlingen"].values()):
                continue
            try:
                aantal = pipeline.start_publicatie(toets_id)
                scans.log_scan(s.get("bron_scan") or toets_id, "definitief", toets_id, s.get("project", ""),
                               f"{aantal} leerlingen; niets open, automatisch definitief gemaakt")
                log.info("%s: automatisch definitief gemaakt (%s leerlingen)", toets_id, aantal)
                gedaan.append(toets_id)
            except Exception as fout:  # noqa: BLE001
                log.exception("%s: automatisch definitief maken mislukt", toets_id)
                with sessie.bewerk(toets_id) as s2:
                    s2["automatisch"] = False      # niet elke ronde opnieuw proberen; de docent ziet de fout
                    s2["melding"] = f"Automatisch definitief maken mislukte: {fout}"
        return gedaan

    def wacht_op_docent(self) -> list[dict]:
        """Automatisch gestarte analyses die op een beslissing van de docent wachten."""
        sessie, pipeline, scores, _, _ = _te()
        uit = []
        for kort in sessie.alle():
            s = sessie.lees(kort["toets_id"]) if kort["status"] in ("review", "fout") else None
            if not s or not s.get("automatisch") or pipeline.loopt(kort["toets_id"]):
                continue
            tel = scores.open_punten(s) if s["status"] == "review" else {"totaal": 0}
            if s["status"] == "fout" or tel["totaal"] or s.get("toestemming_nodig"):
                uit.append({"toets_id": kort["toets_id"], "status": s["status"], "open": tel, "fout": s.get("fout", "")})
        return uit

    # ---------------------------------------------------------------- de lus
    def ronde(self) -> None:
        for pad in scans.klaar_in_inbox(self.inbox):
            if self._pauze.get(pad, 0) > time.time():
                continue
            sleutel = tuple(sorted((str(f), f.stat().st_size, f.stat().st_mtime) for f in scans._bestanden(pad)))
            if self._gezien.get(pad) != sleutel:       # twee rondes gelijk: OneDrive is echt klaar
                self._gezien[pad] = sleutel
                continue
            self.staat["bezig"] = pad.name
            try:
                if self.verwerk(pad) != "later":
                    self._gezien.pop(pad, None)
                    self.staat["fout"] = ""
            except Exception as fout:  # noqa: BLE001  (bijv. abonnement op): scan blijft staan, volgende keer opnieuw
                self.staat["fout"] = f"{pad.name}: {fout}"[:300]
                self._pauze[pad] = time.time() + PAUZE_NA_FOUT
                log.warning("Scan %s niet verwerkt (over %d minuten opnieuw): %s", pad.name, PAUZE_NA_FOUT // 60, fout)
            finally:
                self.staat["bezig"] = ""
        self.publiceer_klaar()
        self.staat["laatste"] = time.strftime("%H:%M:%S")

    def start(self) -> None:
        if not self.inbox.is_dir():
            if self.inbox.parent.is_dir():
                self.inbox.mkdir(exist_ok=True)
            else:
                self.staat["fout"] = f"De map {self.inbox} bestaat niet (staat OneDrive aan?)."
                log.warning("%s", self.staat["fout"])
                return
        self.staat["actief"] = True

        def lus():
            while True:
                try:
                    self.ronde()
                except Exception as fout:  # noqa: BLE001
                    self.staat["fout"] = str(fout)[:300]
                    log.exception("Scanwacht")
                time.sleep(INTERVAL)

        threading.Thread(target=lus, name="scanwacht", daemon=True).start()
        log.info("Bewaak %s", self.inbox)
