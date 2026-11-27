# -*- coding: utf-8 -*-
"""ToetsCirkel: start de drie bestaande apps samen op één adres.

    http://127.0.0.1:5050/              startpagina
    http://127.0.0.1:5050/construeren/  Toetsconstructie
    http://127.0.0.1:5050/evalueren/    Toetsevaluatie
    http://127.0.0.1:5050/oefenen/      Oefentool

De apps zelf blijven waar ze staan (~/Documents/<App>) en houden hun eigen gegevens. Lukt het laden van één app
niet, dan starten de andere gewoon en toont de startpagina wat er mis is.
Starten:  python3 -m cirkel.server   (vanuit de map ToetsCirkel), of dubbelklik scripts/Start ToetsCirkel.command
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import logging
import os
import sys
import traceback
from pathlib import Path

from werkzeug.middleware.dispatcher import DispatcherMiddleware

from .koppeling import koppel
from .portaal import maak_portaal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import paden  # noqa: E402

POORT = int(os.environ.get("TOETSCIRKEL_POORT", "5050"))
log = logging.getLogger("toetscirkel")


def _laad_bestand(naam: str, bestand: Path):
    spec = importlib.util.spec_from_file_location(naam, bestand)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[naam] = mod                      # Flask zoekt templates/static via sys.modules[naam]
    spec.loader.exec_module(mod)
    return mod


def _zonder_run(mod) -> None:
    """Roep de main() van een app aan voor de opstartstappen (database bijwerken, werk hervatten), maar zonder dat
    de app zelf een server start of zijn oude adres meldt."""
    mod.app.run = lambda *a, **k: None
    with contextlib.redirect_stdout(io.StringIO()):
        mod.main()


def _probeer(status: dict, basis: str, functie):
    try:
        app = functie()
        status[basis] = {"ok": True, "fout": ""}
        return app
    except BaseException as fout:  # noqa: BLE001  (ook sys.exit uit de apps opvangen)
        if isinstance(fout, KeyboardInterrupt):
            raise
        tekst = str(fout) if isinstance(fout, SystemExit) else "".join(traceback.format_exception_only(type(fout), fout)).strip()
        log.error("%s kon niet starten: %s", basis, tekst)
        log.debug("%s", traceback.format_exc())
        status[basis] = {"ok": False, "fout": tekst or type(fout).__name__}
        return None


def laad_construeren():
    mod = _laad_bestand("toetscirkel_construeren", paden.TOETSCONSTRUCTIE / "app.py")
    return mod.app


def laad_evalueren():
    sys.path.insert(0, str(paden.TOETSEVALUATIE / "webapp"))
    mod = _laad_bestand("toetscirkel_evalueren", paden.TOETSEVALUATIE / "webapp" / "app.py")
    _zonder_run(mod)
    return mod.app


def laad_oefenen():
    sys.path.insert(0, str(paden.OEFENTOOL))
    os.environ["OEFENTOOL_GEEN_BROWSER"] = "1"
    import oefentool.app as mod                 # noqa: E402  (pakket in ~/Documents/Oefentool)
    _zonder_run(mod)
    return mod.app


def _werk_itembank_bij() -> dict:
    from .kern import itembank
    try:
        return {"itembank": itembank.werk_bij(), "itembank_fout": ""}
    except Exception as fout:  # noqa: BLE001  (de apps moeten ook zonder itembank starten)
        log.error("Itembank bijwerken mislukt: %s", fout)
        return {"itembank": {}, "itembank_fout": str(fout)}


def _vul_codes_aan() -> dict:
    """Leerdoelcodes uit het nakijken overnemen in bestaande matrijzen (zeker, zonder AI)."""
    from .kern import codes
    try:
        uit = codes.vul_aan()
        if uit["vragen"]:
            log.info("Leerdoelcodes uit het nakijken overgenomen: %s vragen in %s matrijzen", uit["vragen"], uit["projecten"])
        return {"codes": uit}
    except Exception as fout:  # noqa: BLE001
        log.error("Codes aanvullen uit het nakijken mislukt: %s", fout)
        return {"codes": {"fout": str(fout)}}


def _start_codes_met_claude(kern: dict) -> None:
    """Op de achtergrond: Claude legt matrijzen zonder codes naast het register (via het abonnement van de
    Toetsevaluatie; API-tegoed alleen met toestemming, anders stopt het netjes)."""
    import threading

    from .kern import codes
    abonnement = sys.modules.get("abonnement")
    if abonnement is None:
        return
    staat = kern["codes_claude"] = {"bezig": True, "nu": "", "fout": ""}

    def werk():
        try:
            uit = codes.koppel_met_claude(lambda s, i, sc, m: abonnement.vraag_json(None, s, i, sc, m),
                                          voortgang=lambda naam: staat.update(nu=naam))
            staat.update(uit)
            log.info("Claude koppelde %s vragen in %s matrijzen; %s twijfelgevallen", uit["vragen"], uit["projecten"],
                     uit["twijfel"])
        except Exception as fout:  # noqa: BLE001  (bijv. abonnement op en geen toestemming voor API-tegoed)
            staat["fout"] = str(fout)[:300]
            log.warning("Codes koppelen met Claude gestopt: %s", fout)
        finally:
            staat.update(bezig=False, nu="")

    threading.Thread(target=werk, name="codes-claude", daemon=True).start()


def bereid_kern_voor() -> dict:
    """Fase 2: Oefentool-tabellen eenmalig naar de centrale database (met back-up vooraf). Moet vóór het laden van de
    Oefentool gebeuren, want die kiest bij het laden welke database hij gebruikt."""
    from .kern import database
    try:
        melding = database.voeg_oefentool_samen()
        log.info("%s", melding)
        return {"database": melding, "database_fout": "", "itembank": None}
    except Exception as fout:  # noqa: BLE001
        log.error("Samenvoegen van de databases mislukt (alles blijft zoals het was): %s", fout)
        return {"database": "", "database_fout": str(fout), "itembank": None}


def maak_app():
    """Bouw de gecombineerde WSGI-app. Geeft (wsgi_app, status) terug."""
    # Alle apps dezelfde mappen laten gebruiken (ze lezen deze variabelen al).
    os.environ["TOETSCONSTRUCTIE_MAP"] = str(paden.TOETSCONSTRUCTIE)
    os.environ["TOETSEVALUATIE_MAP"] = str(paden.TOETSEVALUATIE)
    os.environ["OEFENTOOL_MAP"] = str(paden.OEFENTOOL)
    status: dict = {}
    apps = {}
    kern = bereid_kern_voor()
    # Volgorde: oefenen leest modules van evalueren, dus evalueren eerst.
    for basis, laad, cookie in (("/construeren", laad_construeren, "tc_construeren"),
                                ("/evalueren", laad_evalueren, "tc_evalueren"),
                                ("/oefenen", laad_oefenen, "tc_oefenen")):
        app = _probeer(status, basis, laad)
        if app is not None:
            koppel(app, basis, cookie)
            apps[basis] = app
    if kern.get("itembank") is None:                # pas na het laden van evalueren bestaan alle kolommen
        kern.update(_werk_itembank_bij())
    kern.update(_vul_codes_aan())
    if status.get("/evalueren", {}).get("ok") and os.environ.get("TOETSCIRKEL_GEEN_CLAUDE") != "1":
        _start_codes_met_claude(kern)
    portaal = maak_portaal(status, kern)
    return DispatcherMiddleware(portaal, apps), status


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    wsgi, status = maak_app()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    from werkzeug.serving import run_simple
    for basis, s in status.items():
        print(f"  {basis:<14} {'klaar' if s['ok'] else 'NIET GESTART: ' + s['fout'][:200]}")
    print(f"\nToetsCirkel draait op http://127.0.0.1:{POORT}  (stoppen: Ctrl+C)\n")
    run_simple("127.0.0.1", POORT, wsgi, threaded=True, use_reloader=False, use_debugger=False)


if __name__ == "__main__":
    main()
