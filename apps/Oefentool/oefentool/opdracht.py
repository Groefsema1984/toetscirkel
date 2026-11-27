# -*- coding: utf-8 -*-
"""Een opdracht doorloopt drie rondes:

  Ronde 1  Plan       doelgroep bepalen (lokaal), Claude stelt per leerdoel een plan op; de docent
                      geeft commentaar (nieuw plan) of akkoord.
  Ronde 2  Opgaven    Claude maakt per leerdoel nieuwe opgaven, de app rekent ze na, een tweede
                      Claude-beurt beoordeelt ze streng en laat ze herzien. De docent keurt goed,
                      laat aanpassen of keurt af (met reden: wordt een kwaliteitsregel).
  Ronde 3  Klaarzetten sets per leerling/groep/klas samenstellen, papier en/of digitaal maken en
                      per groep een oefenexamen met correctievoorschrift voor de Toetsevaluatie.

Alles wordt direct bewaard in Opdrachten/<id>/opdracht.json (lokaal, met leerlingnummers) en de
opgaven in oefentool.db (zonder leerlingnummers).
"""
from __future__ import annotations

import json
import logging
import random
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import bibliotheek, claude, doelgroep, kwaliteit, opslag, te
from .instellingen import DOELEN, OPDRACHTEN, WERKERS

log = logging.getLogger("oefentool")
_sloten: dict[str, threading.RLock] = {}
_taken: dict[str, threading.Thread] = {}
_algemeen = threading.Lock()
MIN_PER_PUNT = {"havo": 3.2, "vwo": 3.1, "mavo": 2.5}


class Stop(Exception):
    """Fout met een boodschap voor de docent."""


# ---------------------------------------------------------------------------- opslag van een opdracht
def _pad(oid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", oid or ""):
        raise KeyError(oid)
    return OPDRACHTEN / oid / "opdracht.json"


def _slot(oid: str) -> threading.RLock:
    with _algemeen:
        return _sloten.setdefault(oid, threading.RLock())


def lees(oid: str) -> dict | None:
    try:
        p = _pad(oid)
    except KeyError:
        return None
    with _slot(oid):
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))


@contextmanager
def bewerk(oid: str):
    with _slot(oid):
        o = lees(oid)
        if o is None:
            raise KeyError(oid)
        yield o
        o["gewijzigd"] = opslag.nu()
        tmp = _pad(oid).with_suffix(".tmp")
        tmp.write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_pad(oid))


def alle() -> list[dict]:
    uit = []
    for p in sorted(OPDRACHTEN.glob("*/opdracht.json"), reverse=True):
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        o["bezig"] = loopt(o["id"])
        uit.append(o)
    return sorted(uit, key=lambda o: o.get("gemaakt", ""), reverse=True)


def verwijder(oid: str) -> None:
    if loopt(oid):
        raise Stop("Deze opdracht is nog bezig.")
    shutil.rmtree(OPDRACHTEN / oid, ignore_errors=True)


def loopt(oid: str) -> bool:
    t = _taken.get(oid)
    return t is not None and t.is_alive()


def _voortgang(oid: str, tekst: str, gedaan: int = 0, totaal: int = 0) -> None:
    with bewerk(oid) as o:
        o["voortgang"] = {"tekst": tekst, "gedaan": gedaan, "totaal": totaal}


def _tel(oid: str) -> None:
    with bewerk(oid) as o:
        o["voortgang"]["gedaan"] = o["voortgang"].get("gedaan", 0) + 1


def _start(oid: str, werk, status_na_fout: str) -> None:
    def veilig():
        claude.zet_opdracht(oid, bool(lees(oid).get("api_toestemming")))
        toestemming = False
        try:
            werk(oid)
            with bewerk(oid) as o:
                o["api_toestemming"] = False
            return
        except Stop as fout:
            melding = str(fout)
        except claude.ToestemmingNodig as fout:
            toestemming = True
            melding = ("Je Claude-abonnement kan nu niet verder (limiet bereikt of niet ingelogd). Er is geen API-tegoed "
                       f"gebruikt. Melding: {str(fout)[:200]}")
        except claude.ApiFout as fout:
            melding = f"Claude: {fout}"
        except Exception as fout:  # noqa: BLE001
            naam = type(fout).__name__
            if naam == "AuthenticationError":
                melding = "De API-sleutel is geweigerd. Start de oefentool opnieuw via 'Start Oefentool.command'."
            elif "anthropic" in type(fout).__module__:
                melding = f"De Anthropic API gaf een fout: {fout}"
            else:
                log.exception("Fout in opdracht %s", oid)
                melding = f"Onverwachte fout: {naam}: {fout}"
        with bewerk(oid) as o:
            o["status"], o["fout"] = status_na_fout, melding
            o["voortgang"] = {"tekst": "", "gedaan": 0, "totaal": 0}
            o["api_toestemming"] = False
            o["toestemming_nodig"] = {"stap": werk.__name__} if toestemming else None

    if loopt(oid):
        raise Stop("Er loopt al een stap voor deze opdracht.")
    with bewerk(oid) as o:
        o["toestemming_nodig"] = None
        if not o.get("api_toestemming"):
            claude.abonnement_weer_proberen()      # zelf op een knop geklikt: eerst weer het abonnement proberen
    t = threading.Thread(target=veilig, daemon=True, name=f"opdracht-{oid}")
    _taken[oid] = t
    t.start()


# ---------------------------------------------------------------------------- nieuwe opdracht
def maak(vak: str, toets_ids: list[str], doel: str, klassen: list[str], leerling_ids: list[str],
         codes: list[str], instellingen: dict, naam: str = "", vorige: str | None = None) -> str:
    if doel not in DOELEN:
        raise Stop("Kies wat voor soort materiaal je wilt maken.")
    if not toets_ids:
        raise Stop("Kies minstens één toets.")
    infos = [te.info(t) for t in toets_ids]
    niveaus = {i.niveau for i in infos}
    if len({te.sector(n) for n in niveaus}) > 1 or len({i.vak for i in infos}) > 1:
        raise Stop("Kies toetsen van één vak en van mavo óf havo/vwo: die hebben elk een eigen leerdoelenregister.")
    niveau = "vwo" if "vwo" in niveaus else sorted(niveaus)[0]
    prof, doelen = doelgroep.bouw(toets_ids, set(leerling_ids), set(klassen), set(codes))
    if not prof:
        raise Stop("Voor deze keuze zijn er geen resultaten in de Toetsevaluatie.")
    if not doelen:
        raise Stop("Er zijn geen gekoppelde leerdoelen. Koppel de vragen eerst in de Toetsevaluatie (Leerdoelen).")
    sets = doelgroep.maak_sets(doel, prof, doelen, int(instellingen.get("groepen") or 3))
    if not any(s["doelen"] for s in sets):
        if doel == "onderhoud":
            raise Stop(f"Er zijn geen leerdoelen die de klas al {doelgroep.ONDERHOUD_NA_DAGEN} dagen of langer beheerst.")
        raise Stop("Iedereen beheerst de gekozen leerdoelen al. Kies een examentraining, onderhoud of andere toetsen.")
    in_sets = {d["code"] for s in sets for d in s["doelen"]}
    max_ld = int(instellingen.get("max_leerdoelen") or 12)
    gekozen = [d for d in doelen if d["code"] in in_sets][:max_ld]
    keep = {d["code"] for d in gekozen}
    for s in sets:
        s["doelen"] = [d for d in s["doelen"] if d["code"] in keep]
    sets = [s for s in sets if s["doelen"]]
    klas = sorted({p["klas"] for p in prof.values() if p["klas"]})
    stempel = datetime.now().strftime("%Y%m%d_%H%M%S")
    oid = f"{stempel}_{re.sub(r'[^A-Za-z0-9]+', '', (klas[0] if len(klas) == 1 else niveau))[:8]}_{doel[:5]}"
    (OPDRACHTEN / oid).mkdir(parents=True, exist_ok=False)
    vorige_o = lees(vorige) if vorige else None
    o = {
        "id": oid, "naam": naam.strip() or f"{DOELEN[doel].split(':')[0]} {', '.join(klas) or niveau} {datetime.now():%d-%m}",
        "gemaakt": opslag.nu(), "vak": infos[0].vak, "sector": te.sector(niveau), "niveau": niveau,
        "klas": ", ".join(klas), "tweefasen": any(i.tweefasen for i in infos),
        "doel": doel, "doel_tekst": DOELEN[doel], "toets_ids": toets_ids, "klassen": klassen,
        "leerling_ids": sorted(prof), "codes": sorted(codes), "aantal_leerlingen": len(prof),
        "instellingen": {"vormen": instellingen.get("vormen") or ["papier", "digitaal"],
                         "minuten": int(instellingen.get("minuten") or 45),
                         "oefenexamen": bool(instellingen.get("oefenexamen")),
                         "groepen": int(instellingen.get("groepen") or 3),
                         "max_leerdoelen": max_ld, "examenminuten": int(instellingen.get("examenminuten") or 30)},
        "doelen": gekozen, "sets": sets, "status": "plan", "fout": "", "voortgang": {},
        "plan": None, "plan_feedback": [], "pool": {}, "uitvoer": {}, "codes_leerling": {},
        "leerloop": {"vorige": vorige, "ronde": (vorige_o["leerloop"]["ronde"] + 1) if vorige_o else 1},
        "kritiek_terugkerend": [],
    }
    _pad(oid).write_text(json.dumps(o, ensure_ascii=False, indent=1), encoding="utf-8")
    start_plan(oid)
    return oid


# ---------------------------------------------------------------------------- ronde 1: plan
def start_plan(oid: str, feedback: str = "") -> None:
    with bewerk(oid) as o:
        if feedback.strip():
            o["plan_feedback"].append(feedback.strip())
        o["status"], o["fout"] = "plan", ""
        o["voortgang"] = {"tekst": "Claude maakt een plan", "gedaan": 0, "totaal": 0}
    _start(oid, _plan, "plan_fout")


def _plan(oid: str) -> None:
    o = lees(oid)
    codes = [d["code"] for d in o["doelen"]]
    besch = bibliotheek.beschikbaar(codes, o["niveau"])
    rol = "Je bent vakdidacticus en maakt een oefenplan op basis van toetsresultaten. Kort en concreet."
    uit = claude.vraag_json(claude.maak_client(), claude.systeem(o["vak"], o["niveau"], rol, domeinen(o)),
                            claude.plan_tekst(o, o["doelen"], besch), claude.SCHEMA_PLAN, max_tokens=12_000,
                            soort="plan")
    uit = _schoon_plan(uit, o, besch)
    with bewerk(oid) as o2:
        o2["plan"] = uit
        o2["beschikbaar"] = besch
        o2["status"] = "plan_klaar"
        o2["voortgang"] = {}


def _schoon_plan(plan: dict, o: dict, besch: dict) -> dict:
    per = {}
    for ond in plan.get("onderdelen", []):
        if ond["leerdoel_code"] in per or ond["leerdoel_code"] not in {d["code"] for d in o["doelen"]}:
            continue
        stappen, gezien = [], set()
        for s in sorted(ond["stappen"], key=lambda s: s["stap"]):
            if s["stap"] in gezien:
                continue
            gezien.add(s["stap"])
            s["aantal"] = max(1, min(3, int(s["aantal"])))
            s["soorten"] = s["soorten"] or ["open"]
            stappen.append(s)
        ond["stappen"] = stappen or _standaard_stappen()
        ond["hergebruik"] = [u for u in ond.get("hergebruik", []) if u in besch.get(ond["leerdoel_code"], {}).get("uids", [])]
        per[ond["leerdoel_code"]] = ond
    for d in o["doelen"]:
        if d["code"] not in per:
            per[d["code"]] = {"leerdoel_code": d["code"], "waarom": "Toegevoegd door de app (niet in het plan).",
                              "stappen": _standaard_stappen(), "hergebruik": []}
    plan["onderdelen"] = [per[d["code"]] for d in o["doelen"]]
    if o["doel"] == "onderhoud":                 # onderhoud: kort, op examenniveau
        for ond in plan["onderdelen"]:
            soorten = sorted({s for st in ond["stappen"] for s in st["soorten"]}) or ["open"]
            ond["stappen"] = [{"stap": 3, "aantal": 2, "soorten": soorten, "bloom": "Toepassen", "leergat_focus": [],
                               "aanpak": "korte herhaling op examenniveau, nieuwe context"}]
    _hergebruik_uit_itembank(plan, o, besch)
    oe = plan.get("oefenexamen") or {}
    plan["oefenexamen"] = {"vragen_per_leerdoel": max(1, min(2, int(oe.get("vragen_per_leerdoel") or 1))),
                           "opbouw": oe.get("opbouw", "")}
    return plan


def _al_gebruikt(o: dict) -> set[str]:
    """Opgaven die deze leerlingen al eerder kregen (in andere opdrachten): die niet nog eens hergebruiken."""
    eigen = set(o.get("leerling_ids", []))
    ids = [x["id"] for x in alle() if x["id"] != o["id"] and eigen & set(x.get("leerling_ids", []))]
    if not ids:
        return set()
    with opslag.db() as con:
        vt = ",".join("?" * len(ids))
        return {r[0] for r in con.execute(f"SELECT item_uid FROM item_gebruik WHERE opdracht_id IN ({vt})", ids)}


def _hergebruik_uit_itembank(plan: dict, o: dict, besch: dict) -> None:
    """Goedgekeurde opgaven uit de itembank gaan vóór nieuwe opgaven: per stap zoveel als er zijn.
    Een opgave die al bij de docent en (liefst) bij leerlingen goed werkte, kost niets meer."""
    gebruikt = _al_gebruikt(o)
    for ond in plan["onderdelen"]:
        uids = [u for u in besch.get(ond["leerdoel_code"], {}).get("uids", []) if u not in gebruikt]
        items = opslag.items_van(uids)
        her = [u for u in ond.get("hergebruik", []) if u in items]
        for s in ond["stappen"]:
            vrij = [u for u in uids if u in items and items[u]["stap"] == s["stap"] and u not in her]
            neem = vrij[:max(0, s["aantal"])]
            her += neem
            s["aantal"] -= len(neem)
        ond["stappen"] = [s for s in ond["stappen"] if s["aantal"] > 0]
        ond["hergebruik"] = her


def _standaard_stappen() -> list[dict]:
    return [{"stap": 1, "aantal": 1, "soorten": ["open"], "bloom": "Begrijpen", "leergat_focus": [], "aanpak": "basis"},
            {"stap": 2, "aantal": 2, "soorten": ["berekening", "open"], "bloom": "Toepassen", "leergat_focus": [],
             "aanpak": "toepassen in context"},
            {"stap": 3, "aantal": 1, "soorten": ["open"], "bloom": "Analyseren", "leergat_focus": [],
             "aanpak": "examenniveau"}]


def pas_plan_aan(oid: str, code: str, stap: int, aantal: int) -> None:
    """De docent past het aantal opgaven per stap zelf aan (0 = stap weglaten)."""
    with bewerk(oid) as o:
        for ond in o["plan"]["onderdelen"]:
            if ond["leerdoel_code"] == code:
                for s in ond["stappen"]:
                    if s["stap"] == stap:
                        s["aantal"] = max(0, min(4, aantal))
                ond["stappen"] = [s for s in ond["stappen"] if s["aantal"] > 0]


def laat_leerdoel_weg(oid: str, code: str) -> None:
    with bewerk(oid) as o:
        o["plan"]["onderdelen"] = [x for x in o["plan"]["onderdelen"] if x["leerdoel_code"] != code]
        o["doelen"] = [d for d in o["doelen"] if d["code"] != code]
        for s in o["sets"]:
            s["doelen"] = [d for d in s["doelen"] if d["code"] != code]


# ---------------------------------------------------------------------------- ronde 2: opgaven
def start_genereren(oid: str) -> None:
    with bewerk(oid) as o:
        if not o.get("plan"):
            raise Stop("Er is nog geen plan.")
        o["status"], o["fout"] = "genereren", ""
        o["voortgang"] = {"tekst": "Opgaven maken, narekenen en beoordelen", "gedaan": 0,
                          "totaal": len(o["plan"]["onderdelen"])}
    _start(oid, _genereer, "genereren_fout")


def _basis(o: dict, code: str, stap: int = 1, leergat: str = "") -> dict:
    return {"vak": o["vak"], "sector": o["sector"], "niveau": o["niveau"], "leerdoel_code": code,
            "stap": stap, "leergat": leergat}


def _genereer(oid: str) -> None:
    o = lees(oid)
    client = claude.maak_client()
    doelen = {d["code"]: d for d in o["doelen"]}
    terugkerend: list[str] = []
    api_ok = claude.api_ok()

    def een(ond):
        claude.zet_opdracht(oid, api_ok)
        code = ond["leerdoel_code"]
        if o["pool"].get(code):                       # al gemaakt (hervatten na een fout)
            _tel(oid)
            return
        extra = o["plan"]["oefenexamen"]["vragen_per_leerdoel"] if o["instellingen"]["oefenexamen"] else 0
        if not any(s["aantal"] > 0 for s in ond["stappen"]) and not extra:
            uids = []                                 # alles hergebruikt uit de itembank: niets te maken
        else:
            uids = _maak_voor_leerdoel(client, o, doelen[code], ond, terugkerend)
        with bewerk(oid) as o2:
            o2["pool"][code] = ond.get("hergebruik", []) + uids
        _tel(oid)

    onderdelen = o["plan"]["onderdelen"]
    if onderdelen:                        # eerst één leerdoel alleen: dan staat de kennisbank in de cache
        een(onderdelen[0])                # en lezen de volgende verzoeken die voor een tiende van de prijs
    with ThreadPoolExecutor(WERKERS) as pool:
        list(pool.map(een, onderdelen[1:]))
    n = kwaliteit.voorstel_uit_kritiek(o["vak"], o["niveau"], _vaakst(terugkerend), oid)
    with bewerk(oid) as o2:
        o2["status"] = "beoordelen"
        o2["kritiek_terugkerend"] = _vaakst(terugkerend)
        o2["nieuwe_voorstellen"] = n
        o2["voortgang"] = {}


def _vaakst(lijst: list[str]) -> list[str]:
    uniek = []
    for t in lijst:
        if not any(kwaliteit._lijkt(t, u) for u in uniek):
            uniek.append(t)
    return uniek[:6]


def domeinen(o: dict) -> str:
    """Domeinletters (bijv. 'GH') van de leerdoelen: alleen die delen van de kennisbank gaan mee."""
    letters = set()
    for d in o.get("doelen", []):
        for e in d.get("eindtermen") or []:
            letters |= set(re.findall(r"\b([A-K])\d", e))
    return "".join(sorted(letters))


def _sys_maker(o: dict) -> list[dict]:
    return claude.systeem(o["vak"], o["niveau"],
                          "Je bent een ervaren examenmaker en schrijft nieuwe oefenopgaven in examenstijl.", domeinen(o))


def _sys_criticus(o: dict) -> list[dict]:
    return claude.systeem(o["vak"], o["niveau"],
                          "Je bent examensecretaris en controleert oefenopgaven streng op vakinhoud, rekenwerk en stijl.",
                          domeinen(o))


def _maak_voor_leerdoel(client, o: dict, doel: dict, ond: dict, terugkerend: list[str]) -> list[str]:
    code = doel["code"]
    fragmenten = bibliotheek.fragmenten_voor(code, o["niveau"], doel.get("thema", ""))
    voorbeelden = opslag.bewezen_items(code, o["niveau"], 3)
    with opslag.db() as con:
        gebruikt = [json.loads(r["inhoud"]).get("titel", "") for r in con.execute(
            "SELECT inhoud FROM items WHERE leerdoel_code = ? ORDER BY item_id DESC LIMIT 30", (code,))]
    extra = o["plan"]["oefenexamen"]["vragen_per_leerdoel"] if o["instellingen"]["oefenexamen"] else 0
    uit = claude.vraag_json(client, _sys_maker(o),
                            claude.genereer_tekst(o, doel, ond, fragmenten, voorbeelden, gebruikt, extra),
                            claude.SCHEMA_ITEMS, max_tokens=32_000, soort="genereren")
    lijst = []
    with opslag.db() as con:
        for inh in uit["items"]:
            it = opslag.nieuw_item(con, o["id"], _basis(o, code, inh["stap"], ",".join(inh["leergat_focus"])), inh)
            lijst.append(it)
    return _kritiek_en_herzien(client, o, doel, lijst, terugkerend)


def _kritiek_en_herzien(client, o: dict, doel: dict, lijst: list[dict], terugkerend: list[str],
                        docent: str = "", rondes: int = 1) -> list[str]:
    """Rekencontrole + zelfkritiek op alle nog niet beoordeelde opgaven; herzien wat niet goed is."""
    for ronde in range(rondes + 1):
        open_ = [it for it in lijst if it.get("kritiek") is None]
        if not open_:
            break
        checks = [kwaliteit.rekencheck(it["inhoud"]) for it in open_]
        kr = claude.vraag_json(client, _sys_criticus(o), claude.kritiek_tekst(doel, [it["inhoud"] for it in open_], checks),
                               claude.SCHEMA_KRITIEK, max_tokens=12_000, model=claude.model_voor("kritiek"), soort="kritiek")
        terugkerend += kr.get("terugkerend", [])
        per = {b["nr"]: b for b in kr["beoordelingen"]}
        te_herzien = []
        for nr, (it, rc) in enumerate(zip(open_, checks), 1):
            b = per.get(nr) or {"scores": {}, "problemen": ["niet beoordeeld"], "oordeel": "herzien", "advies": ""}
            if not rc["ok"]:
                fouten = [f"rekenfout: {c['expressie']} = {c.get('berekend')}, niet {c['uitkomst']}"
                          for c in rc["controles"] if c.get("ok") is False] + rc["structuur"]
                b = dict(b, oordeel="herzien" if b["oordeel"] == "goed" else b["oordeel"],
                         problemen=b.get("problemen", []) + fouten)
            opslag.zet_item(it["uid"], kritiek=b, kritiek_score=kwaliteit.score(b), rekencheck=rc)
            it["kritiek"], it["rekencheck"] = b, rc
            # Alleen herzien wat echt niet goed is (rekenfout, afgekeurd of een lage score); kleine opmerkingen
            # blijven zichtbaar voor de docent. Dat scheelt veel herzieningen en dus kosten.
            ernstig = not rc["ok"] or b["oordeel"] == "afkeuren" or (b["oordeel"] != "goed" and kwaliteit.score(b) < 3.5)
            if ernstig and ronde < rondes:
                te_herzien.append((nr, it, b))
        if not te_herzien:
            break
        hz = claude.vraag_json(client, _sys_maker(o), claude.herzien_tekst(doel, [(n, i["inhoud"], b) for n, i, b in te_herzien], docent),
                               claude.SCHEMA_HERZIEN, max_tokens=24_000, soort="herzien")
        bij_nr = {n: i for n, i, _ in te_herzien}
        with opslag.db() as con:
            for inh in hz["items"]:
                oud = bij_nr.pop(inh.pop("vervangt", -1), None)
                if oud is None:
                    continue
                nieuw = opslag.nieuw_item(con, o["id"], _basis(o, doel["code"], inh["stap"],
                                                               ",".join(inh["leergat_focus"])), inh, ouder=oud)
                lijst[lijst.index(oud)] = nieuw
    return [it["uid"] for it in lijst]


# ---------------------------------------------------------------------------- docent beoordeelt
def _vraag_toestemming(oid: str, wat: str) -> None:
    with bewerk(oid) as o2:
        o2["fout"] = (f"{wat} kon niet: je Claude-abonnement kan nu niet verder (limiet bereikt of niet ingelogd). "
                      "Er is geen API-tegoed gebruikt. Probeer het later opnieuw, of geef toestemming voor API-tegoed en "
                      "klik daarna opnieuw.")
        o2["toestemming_nodig"] = {"stap": "los"}


def geef_api_toestemming(oid: str) -> None:
    """De docent keurt goed dat deze stap via API-tegoed verdergaat. Geldt alleen voor de stap die vastliep."""
    o = lees(oid)
    stap = (o.get("toestemming_nodig") or {}).get("stap", "")
    with bewerk(oid) as o2:
        o2["api_toestemming"] = True
        o2["toestemming_nodig"] = None
        o2["fout"] = ""
    _herstart(oid, stap)


def opnieuw_via_abonnement(oid: str) -> None:
    claude.abonnement_weer_proberen()
    o = lees(oid)
    stap = (o.get("toestemming_nodig") or {}).get("stap", "")
    with bewerk(oid) as o2:
        o2["api_toestemming"] = False
        o2["toestemming_nodig"] = None
        o2["fout"] = ""
    _herstart(oid, stap)


def _herstart(oid: str, stap: str) -> None:
    if stap == "_plan":
        start_plan(oid)
    elif stap == "_genereer":
        start_genereren(oid)
    elif stap == "_klaarzetten":
        start_klaarzetten(oid)


def oordeel(oid: str, uid: str, soort: str, reden: str = "") -> None:
    o = lees(oid)
    if not any(uid in u for u in o["pool"].values()):
        raise Stop("Deze opgave hoort niet bij de opdracht.")
    if soort == "goed":
        opslag.zet_item(uid, status="goedgekeurd", docent_oordeel="goed", docent_opmerking=reden or None)
        return
    if soort not in ("aanpassen", "afgekeurd"):
        raise Stop("Onbekend oordeel.")
    if not reden.strip():
        raise Stop("Schrijf erbij wat er anders moet; daar leert de oefentool van.")
    opslag.zet_item(uid, docent_oordeel=soort, docent_opmerking=reden,
                    status="afgekeurd" if soort == "afgekeurd" else "concept")
    with bewerk(oid) as o2:
        o2.setdefault("in_bewerking", []).append(uid)

    def werk():
        claude.zet_opdracht(oid, bool(lees(oid).get("api_toestemming")))
        try:
            client = claude.maak_client()
            it = opslag.item(uid)
            kwaliteit.regel_uit_docent(client, o["vak"], o["niveau"], it["inhoud"], soort, reden)
            doel = next(d for d in o["doelen"] if d["code"] == it["leerdoel_code"])
            opdracht = (f"De docent keurde deze opgave af: {reden}. Maak een nieuwe opgave met een andere context."
                        if soort == "afgekeurd" else f"De docent wil deze aanpassing: {reden}")
            b = {"problemen": [reden], "advies": opdracht}
            hz = claude.vraag_json(client, _sys_maker(o), claude.herzien_tekst(doel, [(1, it["inhoud"], b)], opdracht),
                                   claude.SCHEMA_HERZIEN, max_tokens=12_000, soort="docent")
            if not hz["items"]:
                return
            inh = hz["items"][0]
            inh.pop("vervangt", None)
            with opslag.db() as con:
                nieuw = opslag.nieuw_item(con, oid, _basis(o, doel["code"], inh["stap"], ",".join(inh["leergat_focus"])),
                                          inh, ouder=it)
            nieuw_uids = _kritiek_en_herzien(client, o, doel, [nieuw], [], docent=reden, rondes=1)
            with bewerk(oid) as o2:
                lijst = o2["pool"][doel["code"]]
                if uid in lijst:
                    lijst[lijst.index(uid)] = nieuw_uids[0]
        except claude.ToestemmingNodig:
            _vraag_toestemming(oid, f"Opgave {uid} opnieuw maken")
        except Exception as fout:  # noqa: BLE001
            log.exception("Opgave %s herzien mislukt", uid)
            with bewerk(oid) as o2:
                o2["fout"] = f"Opgave {uid} opnieuw maken is mislukt: {fout}"
        finally:
            with bewerk(oid) as o2:
                o2["in_bewerking"] = [u for u in o2.get("in_bewerking", []) if u != uid]
                if not o2.get("toestemming_nodig"):
                    o2["api_toestemming"] = False

    threading.Thread(target=werk, daemon=True).start()


def extra_opgave(oid: str, code: str, stap: int) -> None:
    """Nog een opgave bij een leerdoel (bijvoorbeeld als er te weinig zijn goedgekeurd)."""
    o = lees(oid)
    ond = next((x for x in o["plan"]["onderdelen"] if x["leerdoel_code"] == code), None)
    if not ond:
        raise Stop("Dit leerdoel staat niet in het plan.")
    bron = next((s for s in ond["stappen"] if s["stap"] == stap), None) or _standaard_stappen()[min(stap, 3) - 1]
    klein = dict(ond, stappen=[dict(bron, stap=min(stap, 3), aantal=1)])
    with bewerk(oid) as o2:
        o2.setdefault("in_bewerking", []).append(f"extra:{code}")

    def werk():
        claude.zet_opdracht(oid, bool(lees(oid).get("api_toestemming")))
        try:
            client = claude.maak_client()
            o3 = dict(o, instellingen=dict(o["instellingen"], oefenexamen=False))
            doel = next(d for d in o["doelen"] if d["code"] == code)
            uids = _maak_voor_leerdoel(client, o3, doel, klein, [])
            with bewerk(oid) as o2:
                o2["pool"].setdefault(code, []).extend(uids)
        except claude.ToestemmingNodig:
            _vraag_toestemming(oid, "Extra opgave maken")
        except Exception as fout:  # noqa: BLE001
            log.exception("Extra opgave mislukt")
            with bewerk(oid) as o2:
                o2["fout"] = f"Extra opgave maken is mislukt: {fout}"
        finally:
            with bewerk(oid) as o2:
                o2["in_bewerking"] = [u for u in o2.get("in_bewerking", []) if u != f"extra:{code}"]
                if not o2.get("toestemming_nodig"):
                    o2["api_toestemming"] = False

    threading.Thread(target=werk, daemon=True).start()


def bewerk_item(oid: str, uid: str, inhoud: dict) -> None:
    """De docent past zelf tekst aan: wordt een nieuwe versie, de oude blijft bewaard."""
    o = lees(oid)
    it = opslag.item(uid)
    nieuw_inh = dict(it["inhoud"], **inhoud)
    with opslag.db() as con:
        nieuw = opslag.nieuw_item(con, oid, _basis(o, it["leerdoel_code"], it["stap"], it["leergat"]), nieuw_inh, ouder=it)
    rc = kwaliteit.rekencheck(nieuw_inh)
    opslag.zet_item(nieuw["uid"], status="goedgekeurd", docent_oordeel="goed", docent_opmerking="zelf aangepast",
                    rekencheck=rc, kritiek=it.get("kritiek"), kritiek_score=it.get("kritiek_score"))
    with bewerk(oid) as o2:
        lijst = o2["pool"][it["leerdoel_code"]]
        lijst[lijst.index(uid)] = nieuw["uid"]


# ---------------------------------------------------------------------------- ronde 3: klaarzetten
def start_klaarzetten(oid: str) -> None:
    with bewerk(oid) as o:
        if o.get("in_bewerking"):
            raise Stop("Er worden nog opgaven opnieuw gemaakt. Wacht tot die klaar zijn.")
        o["status"], o["fout"] = "klaarzetten", ""
        o["voortgang"] = {"tekst": "Sets samenstellen en bestanden maken", "gedaan": 0, "totaal": 0}
    _start(oid, _klaarzetten, "beoordelen")


def _minuten(it: dict, niveau: str) -> float:
    return it["inhoud"].get("punten", 2) * MIN_PER_PUNT.get(niveau, 3.0) + 1


def stel_sets_samen(o: dict) -> None:
    items = opslag.items_van([u for us in o["pool"].values() for u in us])
    for s in o["sets"]:
        gekozen, minuten = [], 0.0
        for i, d in enumerate(s["doelen"]):
            stappen = doelgroep.STAPPEN.get(d["stand"], [2, 3])
            if o["doel"] == "examentraining":
                stappen = [s_ for s_ in stappen if s_ >= 2] or [3]
            kand = sorted((items[u] for u in o["pool"].get(d["code"], []) if u in items
                           and items[u]["status"] == "goedgekeurd" and items[u]["stap"] in stappen),
                          key=lambda it: (it["stap"], -(it.get("kritiek_score") or 0)))
            for it in kand:
                m = _minuten(it, o["niveau"])
                if minuten + m > o["instellingen"]["minuten"] and gekozen and i >= 2:
                    break
                gekozen.append(it["uid"])
                minuten += m
            if minuten >= o["instellingen"]["minuten"] and i >= 2:
                break
        s["items"] = gekozen
        s["minuten"] = round(minuten)


def _klaarzetten(oid: str) -> None:
    from . import uitvoer
    o = lees(oid)
    alle = opslag.items_van([u for us in o["pool"].values() for u in us])
    for u, it in alle.items():
        if it["status"] == "concept":
            opslag.zet_item(u, status="goedgekeurd")
    with bewerk(oid) as o2:
        o2["pool"] = {c: [u for u in us if alle.get(u) and alle[u]["status"] != "afgekeurd"] for c, us in o2["pool"].items()}
        stel_sets_samen(o2)
        if "digitaal" in o2["instellingen"]["vormen"] and o2["doel"] == "leerling":
            o2["codes_leerling"] = o2.get("codes_leerling") or _codes(o2["leerling_ids"])
        o = dict(o2)
    _voortgang(oid, "Bestanden maken")
    uit = uitvoer.bouw_alles(o)
    with opslag.db() as con:
        for s in o["sets"]:
            for u in s["items"]:
                con.execute("INSERT OR IGNORE INTO item_gebruik (item_uid, opdracht_id, set_naam) VALUES (?,?,?)",
                            (u, oid, s["naam"]))
                con.execute("UPDATE items SET gebruikt = gebruikt + 1 WHERE uid = ?", (u,))
        for ex in uit.get("oefenexamens", []):
            for nr, u in enumerate(ex["items"], 1):
                con.execute("INSERT OR REPLACE INTO item_gebruik (item_uid, opdracht_id, set_naam, toets_id, vraagnr) "
                            "VALUES (?,?,?,?,?)", (u, oid, "examen:" + ex["groep"], ex["code"], nr))
    try:
        bibliotheek.schrijf_drive()
    except Exception as fout:  # noqa: BLE001
        log.warning("Drive bijwerken mislukt: %s", fout)
    with bewerk(oid) as o2:
        o2["uitvoer"] = uit
        o2["status"] = "klaar"
        o2["voortgang"] = {}


def _codes(ids: list[str]) -> dict[str, str]:
    tekens = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    rnd = random.SystemRandom()
    gebruikt, uit = set(), {}
    for lid in ids:
        while True:
            c = "".join(rnd.choice(tekens) for _ in range(5))
            if c not in gebruikt:
                gebruikt.add(c)
                uit[lid] = c
                break
    return uit


def volgnummer_oefenexamen() -> int:
    n = 0
    for o in alle():
        for ex in (o.get("uitvoer") or {}).get("oefenexamens", []):
            m = re.match(r"OEF(\d+)", ex.get("code", ""))
            if m:
                n = max(n, int(m.group(1)))
    return n + 1


def maak_digitaal(oid: str) -> list[str]:
    """(Opnieuw) de digitale oefenpagina's maken, bijvoorbeeld met de nieuwste versie van de pagina."""
    from . import uitvoer
    with bewerk(oid) as o:
        if o["status"] != "klaar":
            raise Stop("Zet de opdracht eerst klaar (ronde 3).")
        if o["doel"] == "leerling" and not o.get("codes_leerling"):
            o["codes_leerling"] = _codes(o["leerling_ids"])
        if "digitaal" not in o["instellingen"]["vormen"]:
            o["instellingen"]["vormen"].append("digitaal")
        kopie = dict(o)
    map_ = OPDRACHTEN / oid / "uitvoer" / "digitaal"
    if map_.exists():
        shutil.rmtree(map_)
    lijst = uitvoer.digitaal(kopie, kopie["sets"], map_, kopie.get("codes_leerling") if kopie["doel"] == "leerling" else None)
    with bewerk(oid) as o:
        o.setdefault("uitvoer", {})["digitaal"] = lijst
    return lijst
