# -*- coding: utf-8 -*-
"""Alle verzoeken aan Claude (Anthropic API): indexeren, plan, genereren, zelfkritiek, herzien en
kwaliteitsregels. Er gaan nooit namen of leerlingnummers mee: alleen leerdoelen, aantallen en
(anonieme) foutomschrijvingen."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from . import opslag
from .instellingen import (AANPAK_LEERGAT, BLOOM, KWALITEITSREGELS, LEERGATEN, MODEL, TAALNIVEAU,
                           TOETSCONSTRUCTIE)

log = logging.getLogger("oefentool")


class ApiFout(RuntimeError):
    pass


def heeft_sleutel() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def kan_werken() -> bool:
    """Er is een API-sleutel, of Claude Code met je abonnement."""
    return heeft_sleutel() or claude_pad() is not None


def abonnement_weer_proberen() -> None:
    """De docent wil het opnieuw via het abonnement proberen (bijvoorbeeld omdat de limiet weer vrij is)."""
    _abonnement.update(op_tot=0.0, melding="")
    _status_cache.clear()


def maak_client():
    """API-client, of None zonder sleutel (via het abonnement is er geen client nodig)."""
    if not heeft_sleutel():
        return None
    import anthropic
    return anthropic.Anthropic(max_retries=6, timeout=900.0)


def api_ok() -> bool:
    return getattr(_ctx, "api_ok", False)


_ctx = threading.local()


def zet_opdracht(oid: str, api_ok: bool = False) -> None:
    """Koppelt de verzoeken in deze thread aan een opdracht (kostenregistratie) en zegt of de docent voor deze
    stap toestemming heeft gegeven om API-tegoed te gebruiken."""
    _ctx.opdracht = oid
    _ctx.api_ok = api_ok


class ToestemmingNodig(RuntimeError):
    """Het abonnement kan dit nu niet doen; API-tegoed gebruiken mag alleen na toestemming van de docent."""


def model_voor(taak: str) -> str:
    """'index' en 'kritiek' kunnen zuiniger (instelling); maken en herzien altijd met het sterke model."""
    from .instellingen import laad_instellingen
    inst = laad_instellingen()
    return {"index": inst.get("model_index"), "kritiek": inst.get("model_kritiek")}.get(taak) or MODEL


def log_usage(soort: str, tekst: str, model: str, g, batch: bool = False) -> None:
    from .instellingen import kosten
    gewoon = g.input_tokens or 0
    schrijf = getattr(g, "cache_creation_input_tokens", 0) or 0
    lees = getattr(g, "cache_read_input_tokens", 0) or 0
    uit = g.output_tokens or 0
    opslag.log_gebruik(soort, tekst[:120], gewoon, uit, getattr(_ctx, "opdracht", ""), model, schrijf, lees,
                       kosten(model, gewoon, schrijf, lees, uit, batch))


# ---------------------------------------------------------------------------- abonnement (Claude Code)
# Opgaven maken, herzien en plannen kan via je Claude-abonnement (Claude Code, 'claude -p'), net als de
# Toetsconstructie-app. Dat kost geen API-tegoed maar telt mee in de limieten van je abonnement (venster van
# 5 uur en weeklimiet, samen met je gewone gesprekken). Is de limiet bereikt, dan neemt de API het over
# (als je dat wilt). Zuinige taken (bibliotheek lezen, zelfkritiek) blijven op de API met Haiku: die kosten centen.
class AbonnementOp(RuntimeError):
    pass


_abonnement = {"op_tot": 0.0, "melding": ""}


def claude_pad() -> str | None:
    import shutil
    from pathlib import Path
    for k in (os.environ.get("OEFENTOOL_CLAUDE_CODE"), shutil.which("claude"), str(Path.home() / ".claude/local/claude"),
              str(Path.home() / ".local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if k and Path(k).exists():
            return k
    return None


def _schone_env() -> dict:
    """Zonder API-sleutel: Claude Code gebruikt dan altijd het abonnement en nooit betaald tegoed."""
    e = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        e.pop(k, None)
    return e


_status_cache: dict = {}


def abonnement_status() -> dict:
    import subprocess
    import time
    if _status_cache.get("t", 0) > time.time() - 60:
        return dict(_status_cache["v"], limiet=_abonnement["op_tot"] > time.time())
    c = claude_pad()
    if not c:
        return {"gevonden": False, "ingelogd": False, "tekst": "Claude Code is niet gevonden (installeer het via Toetsconstructie)."}
    try:
        uit = subprocess.run([c, "auth", "status", "--json"], capture_output=True, text=True, timeout=20, env=_schone_env()).stdout
        ingelogd = bool(json.loads(uit or "{}").get("loggedIn"))
    except Exception:  # noqa: BLE001
        ingelogd = False
    op = _abonnement["op_tot"] > time.time()
    _status_cache.update(t=time.time(), v={"gevonden": True, "ingelogd": ingelogd, "melding": "",
                                           "tekst": ("Ingelogd met je abonnement" if ingelogd else "Niet ingelogd")})
    return {"gevonden": True, "ingelogd": ingelogd, "limiet": op, "melding": _abonnement["melding"] if op else "",
            "tekst": ("Ingelogd met je abonnement" if ingelogd else "Niet ingelogd: start Toetsconstructie of typ 'claude auth login' in Terminal")}


def motor() -> str:
    """'abonnement' (standaard: API alleen na toestemming) of 'api' (bewust zo ingesteld door de docent)."""
    from .instellingen import laad_instellingen
    m = laad_instellingen().get("motor") or "abonnement"
    return "api" if m == "api" else "abonnement"


def _json_uit(tekst: str) -> dict:
    t = tekst.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        begin, eind = t.find("{"), t.rfind("}")
        if begin >= 0 and eind > begin:
            return json.loads(t[begin:eind + 1])
        raise


def _vul_aan(obj, schema: dict):
    """Ontbrekende velden aanvullen met lege waarden, zodat een iets onvolledig antwoord geen fout geeft."""
    soort = schema.get("type")
    if soort == "object":
        obj = obj if isinstance(obj, dict) else {}
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                obj[k] = _vul_aan(obj[k], sub)
            else:
                obj[k] = _leeg(sub)
        return obj
    if soort == "array":
        return [_vul_aan(x, schema.get("items", {})) for x in obj] if isinstance(obj, list) else []
    if "enum" in schema and obj not in schema["enum"]:
        return schema["enum"][0]
    if soort == "integer":
        try:
            return int(obj)
        except (TypeError, ValueError):
            return 0
    if soort == "number":
        try:
            return float(obj)
        except (TypeError, ValueError):
            return 0.0
    if soort == "boolean":
        return bool(obj)
    return obj if isinstance(obj, str) else ("" if obj is None else str(obj))


def _leeg(schema: dict):
    soort = schema.get("type")
    if soort == "object":
        return _vul_aan({}, schema)
    return {"array": [], "integer": 0, "number": 0.0, "boolean": False}.get(soort, schema.get("enum", [""])[0] if "enum" in schema else "")


def _via_abonnement(systeem: list[dict], tekst: str, schema: dict, soort: str, api_model: str = MODEL) -> dict:
    import subprocess
    import tempfile
    import time
    from .instellingen import laad_instellingen
    c = claude_pad()
    if not c:
        raise AbonnementOp("Claude Code is niet gevonden op deze Mac (installeer het via Toetsconstructie).")
    # zuinige taken (bibliotheek lezen, zelfkritiek) ook via het abonnement zuinig: met Haiku
    model = "haiku" if "haiku" in api_model else (laad_instellingen().get("abonnement_model") or "sonnet")
    sys_tekst = "\n\n".join(b["text"] for b in systeem)
    opdracht = (tekst + "\n\nANTWOORD: geef alleen één JSON-object, zonder uitleg en zonder codeblok, dat precies "
                "voldoet aan dit JSON-schema (alle velden invullen):\n" + json.dumps(schema, ensure_ascii=False))
    cmd = [c, "-p", "--output-format", "json", "--model", model, "--system-prompt", sys_tekst, "--max-turns", "1",
           "--disallowedTools", "Bash,Edit,Write,MultiEdit,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit,TodoWrite"]
    for poging in range(2):
        with tempfile.TemporaryDirectory() as leeg:
            proc = subprocess.run(cmd, input=opdracht, capture_output=True, text=True, timeout=1200, cwd=leeg,
                                  env=_schone_env())
        try:
            d = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        except (json.JSONDecodeError, IndexError):
            d = {}
        melding = str(d.get("result") or proc.stderr or "")[-500:]
        if proc.returncode != 0 or d.get("is_error") or d.get("subtype", "success") != "success":
            if re.search(r"limit|usage|quota|rate.?limit|429|overloaded|log ?in|authenticat|credit", melding, re.I):
                _abonnement.update(op_tot=time.time() + 30 * 60, melding=melding[:200])
                raise AbonnementOp(melding)
            if poging == 0:
                continue
            raise ApiFout(f"Claude Code gaf een fout: {melding[:300]}")
        g = d.get("usage") or {}
        opslag.log_gebruik(soort, tekst[:120], g.get("input_tokens", 0) or 0, g.get("output_tokens", 0) or 0,
                           getattr(_ctx, "opdracht", ""), f"abonnement:{model}", g.get("cache_creation_input_tokens", 0) or 0,
                           g.get("cache_read_input_tokens", 0) or 0, 0.0)
        try:
            return _vul_aan(_json_uit(d.get("result", "")), schema)
        except json.JSONDecodeError:
            opdracht += "\n\nLET OP: je vorige antwoord was geen geldige JSON. Geef nu alleen het JSON-object."
    raise ApiFout("Claude Code gaf twee keer geen geldige JSON.")


def vraag_json(client, systeem: list[dict], tekst: str, schema: dict, max_tokens: int,
               model: str = MODEL, soort: str = "") -> dict:
    import time
    if motor() == "abonnement":
        api_ok = getattr(_ctx, "api_ok", False)
        limiet = _abonnement["op_tot"] > time.time()
        if limiet and not api_ok:
            raise ToestemmingNodig(_abonnement["melding"] or "De limiet van je abonnement is bereikt.")
        if not limiet:                                  # met toestemming en limiet op: meteen API
            try:
                return _via_abonnement(systeem, tekst, schema, soort, model)
            except AbonnementOp as fout:
                if not api_ok:
                    raise ToestemmingNodig(str(fout)[:300]) from fout
                log.warning("Abonnement kan niet; met toestemming van de docent verder via de API: %s", fout)
        if not heeft_sleutel():
            raise ApiFout("Er is geen API-sleutel. Start de oefentool opnieuw en plak de sleutel, of wacht tot je "
                          "abonnement weer ruimte heeft.")
    if client is None:
        client = maak_client()
        if client is None:
            raise ApiFout("Er is geen API-sleutel. Start de oefentool opnieuw via 'Start Oefentool.command'.")
    try:
        with client.messages.stream(
            model=model, max_tokens=max_tokens, system=systeem,
            messages=[{"role": "user", "content": [{"type": "text", "text": tekst}]}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        ) as stroom:
            antwoord = stroom.get_final_message()
    except Exception as fout:  # noqa: BLE001
        # Lukt het met het zuinige model niet (bijvoorbeeld een functie die dat model niet kent), dan het sterke model.
        if model != MODEL and type(fout).__name__ in ("BadRequestError", "NotFoundError"):
            log.warning("%s gaf een fout (%s); opnieuw met %s", model, fout, MODEL)
            return vraag_json(client, systeem, tekst, schema, max_tokens, MODEL, soort)
        raise
    log_usage(soort, tekst, model, antwoord.usage)
    if antwoord.stop_reason == "max_tokens":
        raise ApiFout("Het antwoord van Claude was te lang en is afgekapt. Kies minder opgaven per leerdoel.")
    if antwoord.stop_reason == "refusal":
        raise ApiFout("Claude heeft dit verzoek geweigerd.")
    tekst_uit = "".join(b.text for b in antwoord.content if b.type == "text")
    try:
        return json.loads(tekst_uit)
    except json.JSONDecodeError as fout:
        raise ApiFout(f"Geen geldige JSON ontvangen: {fout}") from fout


# ---------------------------------------------------------------------------- kennis van de docent
RICHTLIJN_DELEN = ("vaste gegevens", "tweefasen", "rangorde", "data", "ce-stijl", "werkwoorden", "taalniveau")


def _laatste_richtlijnen() -> str:
    """Alleen de onderdelen van de richtlijnen die voor oefenopgaven tellen (stijl, werkwoorden, taal, tweefasen).
    De changelog, de intake en de bouwstappen van een toets laten we weg: dat scheelt veel invoer per verzoek."""
    map_ = TOETSCONSTRUCTIE / "kennisbank" / "algemeen"
    kandidaten = sorted(map_.glob("00_toetsconstructie_richtlijnen_v*.txt"),
                        key=lambda p: [int(x) for x in re.findall(r"\d+", p.stem.split("_v")[-1])])
    if not kandidaten:
        return ""
    tekst = kandidaten[-1].read_text(encoding="utf-8", errors="replace")
    delen = re.split(r"(?m)^(?=\*\*[^*\n]+\*\*\s*$)", tekst)
    gekozen = [d for d in delen if d.startswith("**") and any(k in d.split("\n", 1)[0].lower() for k in RICHTLIJN_DELEN)]
    return "\n".join(gekozen)[:16_000] if gekozen else tekst[:16_000]


def _vakbestanden(vak: str, niveau: str, domeinen: str = "", max_tekens: int = 30_000) -> str:
    """Rekenregels, definities en examenanalyse van het vak en niveau; met domeinen (bijv. 'GH') alleen de
    bestanden van die domeinen (plus de algemene). Het PTA-overzicht laten we weg."""
    map_ = TOETSCONSTRUCTIE / "kennisbank" / vak.lower()
    if not map_.is_dir():
        return ""
    andere = {"mavo", "havo", "vwo"} - {niveau}
    delen, totaal = [], 0
    for p in sorted(map_.glob("*.txt")):
        naam = p.name.lower()
        if any(f"_{a}_" in naam for a in andere) and f"_{niveau}_" not in naam:
            continue
        if "pta" in naam:
            continue
        m = re.search(r"domein_([a-z](?:_[a-z])*)(?:_|\.|$)", naam)
        if domeinen and m and not set(m.group(1).replace("_", "").upper()) & set(domeinen.upper()):
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        if totaal + len(t) > max_tekens:
            continue
        delen.append(f"### {p.stem}\n{t}")
        totaal += len(t)
    return "\n\n".join(delen)


def _docent_leerregels(vak: str) -> str:
    try:
        regels = json.loads((TOETSCONSTRUCTIE / "kennisbank" / "leerregels.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    eigen = [r for r in regels if str(r.get("vak", "")).lower() == vak.lower()][-40:]
    return "\n".join(f"- ({r.get('niveau', '')}) {r.get('regel', '')}" for r in eigen)


def kwaliteitsregels(vak: str | None = None, niveau: str | None = None, alleen_actief: bool = True) -> list[dict]:
    try:
        regels = json.loads(KWALITEITSREGELS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        regels = []
    uit = []
    for r in regels:
        if alleen_actief and r.get("status") != "actief":
            continue
        if vak and r.get("vak") not in ("", None, vak):
            continue
        if niveau and r.get("niveau") not in ("", None, niveau):
            continue
        uit.append(r)
    return uit


def _vakregels(vak: str) -> str:
    try:
        from . import te
        return te._laad()["kern"].vakregels(vak)
    except Exception:  # noqa: BLE001
        if vak == "Bedrijfseconomie":
            return ("Bedrijfseconomie: geen aftrek voor rekenfouten, tenzij de leerling kennelijk niet heeft "
                    "geschat (1 punt, maximaal 2 per opgave). Los eindantwoord zonder berekening = 0 punten.")
        return "Economie: maximaal 1 punt aftrek per rekenfout. Los eindantwoord zonder berekening = 0 punten."


def systeem_index(vak: str) -> list[dict]:
    """Klein systeemprompt voor het lezen van de bibliotheek: de kennisbank is daar niet nodig."""
    return [{"type": "text", "text": "Je ordent de bibliotheek van een docent " + (vak or "economie").lower()
             + " (Vellesan College). Je knipt bestanden in fragmenten, vat samen in eigen woorden (Nederlands) en "
             "koppelt aan leerdoelen uit zijn leerdoelenregister. Wees beknopt."}]


def systeem(vak: str, niveau: str, rol: str, domeinen: str = "") -> list[dict]:
    """Vaste kennis (gecachet) + de rol van dit verzoek."""
    kwal = kwaliteitsregels(vak, niveau)
    vast = f"""Je werkt voor een docent economie en bedrijfseconomie (Vellesan College, afkorting GRA). Je maakt \
oefenmateriaal in de stijl van zijn toetsen en van het centraal examen. Schrijf Nederlands op taalniveau \
{TAALNIVEAU.get(niveau, 'B1/B2')} voor {niveau}. Verzonnen namen, bedrijven en landen; echte tarieven alleen als \
ze in de kennisbank staan. Neem nooit tekst letterlijk over uit methode of examen: schrijf nieuwe contexten.

## Vakregels bij het nakijken
{_vakregels(vak)}

## Richtlijnen toetsconstructie van de docent (meest recente versie)
{_laatste_richtlijnen()[:40_000]}

## Kennisbank {vak} {niveau} (rekenregels, definities, examenanalyse)
{_vakbestanden(vak, niveau, domeinen)}

## Leerregels van de docent (gaan vóór alles)
{_docent_leerregels(vak) or '- (nog geen)'}

## Kwaliteitsregels van de oefentool (geleerd uit eerdere rondes)
{chr(10).join('- ' + r['regel'] for r in kwal) or '- (nog geen)'}"""
    return [{"type": "text", "text": vast, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": rol}]


# ---------------------------------------------------------------------------- schema's
def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


S, I, N, B = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}


def _lijst(x):
    return {"type": "array", "items": x}


SCHEMA_INDEX = _obj({
    "vak": {"type": "string", "enum": ["Economie", "Bedrijfseconomie", "onbekend"]},
    "niveau": {"type": "string", "enum": ["mavo", "havo", "vwo", "havo-vwo", "onbekend"]},
    "soort": {"type": "string", "enum": ["lesmateriaal", "examen", "correctievoorschrift", "eindtermen", "overig"]},
    "samenvatting": S,
    "fragmenten": _lijst(_obj({
        "soort": {"type": "string", "enum": ["uitleg", "voorbeeld", "opgave", "examenvraag", "begrippen"]},
        "titel": S, "kern": S, "vraag": S, "antwoord": S, "paginas": S, "examen": S, "vraagnr": S,
        "punten": I, "leerdoel_codes": _lijst(S), "eindtermen": S,
        "bloom": {"type": "string", "enum": BLOOM + [""]},
    })),
})

SCHEMA_PLAN = _obj({
    "titel": S, "toelichting": S,
    "onderdelen": _lijst(_obj({
        "leerdoel_code": S, "waarom": S,
        "stappen": _lijst(_obj({
            "stap": {"type": "integer", "enum": [1, 2, 3]}, "aantal": I,
            "soorten": _lijst({"type": "string", "enum": ["open", "berekening", "meerkeuze", "invul", "redeneerketen"]}),
            "bloom": {"type": "string", "enum": BLOOM}, "leergat_focus": _lijst({"type": "string", "enum": list(LEERGATEN)}),
            "aanpak": S,
        })),
        "hergebruik": _lijst(S),
    })),
    "oefenexamen": _obj({"vragen_per_leerdoel": I, "opbouw": S}),
    "vragen_aan_docent": _lijst(S),
})

# Een figuur heeft per type andere gegevens. Die gaan als JSON-tekst mee (veld 'gegevens'), zodat het schema
# klein blijft: een groot geneste schema geeft bij de API de fout 'compiled grammar is too large'.
FIGUUR = _obj({
    "soort": {"type": "string", "enum": ["geen", "markt", "breakeven", "lorenz", "kringloop", "spelboom", "lijnen"]},
    "titel": S,
    "gegevens": S,
})

ITEM = _obj({
    "stap": {"type": "integer", "enum": [1, 2, 3, 4]},
    "soort": {"type": "string", "enum": ["open", "berekening", "meerkeuze", "invul", "redeneerketen"]},
    "titel": S,
    "context": _lijst(S),
    "tabel": _obj({"titel": S, "kop": _lijst(S), "rijen": _lijst(_lijst(S))}),
    "figuur": FIGUUR,
    "vraag": S,
    "extra": _lijst(S),
    "punten": I,
    "bloom": {"type": "string", "enum": BLOOM},
    "werkwoord": S, "eindterm": S,
    "leergat_focus": _lijst({"type": "string", "enum": list(LEERGATEN)}),
    "opties": _lijst(S), "juist": I,
    "antwoord_kort": S, "tolerantie": N,
    "cv": _obj({"intro": S, "regels": _lijst(_obj({"tekst": S, "punten": I})), "opm": S, "fout": S}),
    "voorbeeld": S, "uitleg": S,
    "hints": _lijst(S),
    "berekeningen": _lijst(_obj({"omschrijving": S, "expressie": S, "uitkomst": N})),
})
SCHEMA_ITEMS = _obj({"items": _lijst(ITEM)})
SCHEMA_HERZIEN = _obj({"items": _lijst(_obj(dict(ITEM["properties"], vervangt=I)))})

SCHEMA_KRITIEK = _obj({
    "beoordelingen": _lijst(_obj({
        "nr": I,
        "scores": _obj({k: {"type": "integer", "enum": [1, 2, 3, 4, 5]}
                        for k in ("vakinhoud", "rekenwerk", "examenstijl", "leerdoel", "taal", "correctievoorschrift")}),
        "problemen": _lijst(S),
        "oordeel": {"type": "string", "enum": ["goed", "herzien", "afkeuren"]},
        "advies": S,
    })),
    "terugkerend": _lijst(S),
})

SCHEMA_REGEL = _obj({"regel": S, "geldt_voor": {"type": "string", "enum": ["vak_en_niveau", "vak", "alle"]},
                     "nuttig": B})


# ---------------------------------------------------------------------------- opdrachten
ITEMREGELS = """Regels voor elke opgave:
- context: 1 tot 4 korte alinea's met een nieuwe, realistische situatie (verzonnen namen). Een tabel alleen als \
de vraag die nodig heeft (anders tabel met lege titel, kop en rijen).
- figuur: alleen als de vraag een grafiek nodig heeft, anders soort 'geen' met lege titel en gegevens ''. gegevens is JSON-tekst, per soort:
  markt: {"a_v": 10, "b_v": 0.5, "a_s": 2, "b_s": 0.3, "heffing": 1, "qmax": 20, "pmax": 12, "product": "brood",
  "lijnnaam": "A + accijns"} (vraag P = a_v − b_v·Q, aanbod P = a_s + b_s·Q; heffing 0 als er geen is)
  breakeven: {"x": "afzet in stuks", "y": "euro's"} (schets met lijnen A opbrengst, B totale kosten, C constante kosten)
  lorenz: {"namen": ["2015", "2025"], "curves": [[8, 21, 38, 61], [5, 15, 32, 55]]} (cumulatief % inkomen bij 20/40/60/80%)
  kringloop: {"Y": "1.000", "C": "560", "B": "230", "O": "250", "E": "620", "M": "580"} (I en S blijven open)
  spelboom: {"spelers": ["A", "B"], "keuzes": [["x", "y"], ["x", "y"], ["x", "y"]], "uitkomsten": ["..", "..", "..", ".."]}
  lijnen: {"x": "afzet", "y": "euro's", "getallen": false, "lijnen": [{"naam": "TO", "punten": [[0, 0], [10, 100]]}]}
  (eigen lijnen, bijvoorbeeld kostenlijnen, MO/MK of een tijdreeks; hoogstens 5)
  Een figuur bij een rekenvraag is een schets zonder getallen op de assen: de getallen staan in de tekst of de tabel. Verwijs in de vraag naar 'de figuur'.
- vraag: één opdracht met een examenwerkwoord (Bereken, Leg uit, Toon aan, Verklaar, Geef aan, Beredeneer, \
Maak ... af). Gebruik de vraagvorm van het centraal examen. extra: regels onder de vraag (keuzes bij een \
invulvraag, 'Kies uit: ...'), anders leeg.
- soort meerkeuze: 3 of 4 opties in 'opties' en 'juist' = index (0 = eerste). Bij andere soorten: opties leeg, juist = -1.
- antwoord_kort: alleen bij berekening (het getal, bijv. '150') of invul (de juiste woorden, gescheiden door ' | '); \
anders leeg. tolerantie: toegestane afwijking bij een getal (0 bij geen getal).
- cv: correctievoorschrift in CE-stijl. regels = deelscores die optellen tot punten, of één regel \
'Per gemaakte fout 1 scorepunt in mindering brengen.' met de maximumscore, of regels die met 'indien' beginnen. \
fout = de veelgemaakte fout die bij het leergat past.
- voorbeeld: volledig antwoord in leerlingtaal dat de volle score haalt. uitleg: waarom, kort, voor de leerling.
- Wees beknopt: context hoogstens 80 woorden, voorbeeld en uitleg elk hoogstens 60 woorden.
- hints: precies 2, oplopend: eerst een duwtje (welk begrip of welke formule), dan de eerste stap.
- berekeningen: ELKE rekenstap in cv en voorbeeld als Python-expressie met alleen getallen (punt als \
decimaalteken) en de uitkomst. De app rekent ze na; een fout getal = opgave afgekeurd. Geen berekening: lege lijst.
- stap 1 = basis (kennis en eenvoudige toepassing), 2 = oefenen (toepassen in context), 3 = examenniveau, \
4 = toetsvraag voor het oefenexamen (examenniveau, nooit gelijk aan een oefenopgave).
- Geen leerlingnamen uit de werkelijkheid, geen verwijzing naar 'de vorige toets'."""


def plan_tekst(opdracht: dict, doelen: list[dict], beschikbaar: dict[str, dict]) -> str:
    regels = []
    for d in doelen:
        b = beschikbaar.get(d["code"], {})
        regels.append(
            f"{d['code']} | {d['omschrijving']} | eindtermen {', '.join(d.get('eindtermen') or []) or '-'} | "
            f"nog niet {d['nog_niet']}, bijna {d['bijna']}, wisselend {d['wisselend']} van {d['totaal']} leerlingen | "
            f"leergaten {', '.join(f'{k} {v}x' for k, v in d['leergaten'].items()) or '-'} | "
            f"bibliotheek: {b.get('les', 0)} lesfragmenten, {b.get('examen', 0)} examenvragen, "
            f"{b.get('bewezen', 0)} goedgekeurde opgaven {('(' + ', '.join(b.get('uids', [])) + ')') if b.get('uids') else ''}")
    fb = "\n".join(f"- {f}" for f in opdracht.get("plan_feedback", [])) or "- (nog geen)"
    return f"""RONDE 1: PLAN VOOR OEFENMATERIAAL

Soort opdracht: {opdracht['doel_tekst']}
Klas/niveau: {opdracht['klas'] or opdracht['niveau']} ({opdracht['vak']}), {opdracht['aantal_leerlingen']} leerlingen\
{', ' + str(len(opdracht.get('groepen', []))) + ' groepen' if opdracht['doel'] == 'groep' else ''}
Gewenste omvang per set: ongeveer {opdracht['instellingen']['minuten']} minuten werk.
Vorm: {', '.join(opdracht['instellingen']['vormen'])}. Oefenexamen per groep: \
{'ja' if opdracht['instellingen'].get('oefenexamen') else 'nee'}.
Aandeel nieuwe opgaven: vooral nieuw; hergebruik alleen goedgekeurde opgaven met een goede score.

Leerdoelen van de doelgroep (code | leerdoel | eindtermen | stand | leergaten in de laatste meting | bibliotheek):
{chr(10).join(regels)}

Opmerkingen van de docent bij een eerder plan:
{fb}

Maak een plan per leerdoel. Kies per leerdoel welke stappen nodig zijn (1 basis, 2 oefenen, 3 examenniveau) en \
hoeveel opgaven per stap (samen 2 tot 4 per leerdoel; 4 alleen als veel leerlingen het nog niet beheersen; liever één goede opgave per stap dan meerdere). Stem soort \
en aanpak af op de leergaten. Aanpak per leergat:
{chr(10).join(f'- {k}: {v}' for k, v in AANPAK_LEERGAT.items())}
hergebruik: uids van goedgekeurde opgaven uit de bibliotheek die je wilt hergebruiken (mag leeg).
oefenexamen.vragen_per_leerdoel: 1 of 2. toelichting: hoogstens 120 woorden, voor de docent.
vragen_aan_docent: alleen echte twijfels (mag leeg)."""


def genereer_tekst(opdracht: dict, doel: dict, onderdeel: dict, fragmenten: list[dict], voorbeelden: list[dict],
                   gebruikte_contexten: list[str], extra_toets: int) -> str:
    les = [f for f in fragmenten if f["soort"] != "examenvraag"][:6]
    ex = [f for f in fragmenten if f["soort"] == "examenvraag"][:4]
    stappen = "\n".join(
        f"- stap {s['stap']}: {s['aantal']} opgave(n), soorten {', '.join(s['soorten'])}, Bloom {s['bloom']}, "
        f"leergat {', '.join(s['leergat_focus']) or '-'}; aanpak: {s['aanpak']}" for s in onderdeel["stappen"])
    if extra_toets:
        stappen += f"\n- stap 4: {extra_toets} toetsvraag/-vragen voor het oefenexamen (examenniveau, nieuwe context)"
    fouten = "\n".join(f"- {o}" for o in doel.get("fouten", [])[:8]) or "- (geen omschrijvingen)"
    vb = "\n\n".join(_item_als_tekst(v["inhoud"], v["uid"]) for v in voorbeelden) or "(nog geen)"
    return f"""RONDE 2: OPGAVEN MAKEN VOOR ÉÉN LEERDOEL

Leerdoel {doel['code']}: {doel['omschrijving']}
Leerlingzin: {doel.get('leerling_zin', '')}
Thema: {doel.get('thema', '')}. Eindtermen: {', '.join(doel.get('eindtermen') or []) or 'zie kennisbank'}. \
Bloom in de toetsen: {', '.join(doel.get('bloom') or []) or '-'}.
{opdracht['vak']}, {opdracht['niveau']} ({opdracht['klas']}){', tweefasen (vwo)' if opdracht.get('tweefasen') else ''}.

Plan voor dit leerdoel:
{stappen}

Wat leerlingen in hun laatste toets fout deden (anoniem, door de nakijker omschreven):
{fouten}

Lesmateriaal van de docent bij dit leerdoel (samenvattingen; volg de begrippen en formules van de docent):
{chr(10).join(f"- [{f['bron']}{(' p. ' + f['paginas']) if f.get('paginas') else ''}] {f['titel']}: {f['kern']}" for f in les) or '- (niet gevonden: gebruik de kennisbank)'}

Examenvragen bij dit leerdoel (alleen als stijlvoorbeeld, niet overnemen):
{chr(10).join(f"- {f.get('examen', '')} vr. {f.get('vraagnr', '')} ({f.get('punten', '')}p): {f.get('vraag', '')} | antwoord: {f.get('antwoord', '')}" for f in ex) or '- (niet gevonden)'}

Goedgekeurde opgaven van de docent bij dit leerdoel (zo wil hij het hebben):
{vb}

Gebruik geen van deze contexten opnieuw: {'; '.join(gebruikte_contexten[-25:]) or '-'}

{ITEMREGELS}

Maak precies de opgaven uit het plan, in volgorde van stap."""


def _item_als_tekst(it: dict, uid: str = "") -> str:
    tabel = it.get("tabel") or {}
    t = [f"[{uid}] {it.get('titel', '')} (stap {it.get('stap')}, {it.get('soort')}, {it.get('punten')}p, {it.get('bloom')})"]
    t += it.get("context", [])
    if tabel.get("rijen"):
        t.append(f"Tabel {tabel.get('titel', '')}: " + " / ".join(" | ".join(r) for r in [tabel.get("kop", [])] + tabel["rijen"]))
    from .figuur import beschrijving
    if beschrijving(it):
        t.append(beschrijving(it))
    t.append(f"Vraag: {it.get('vraag', '')}")
    t += it.get("extra", [])
    if it.get("opties"):
        t.append("Opties: " + " / ".join(f"{chr(65 + i)} {o}" for i, o in enumerate(it["opties"])) + f" (juist: {chr(65 + it.get('juist', 0))})")
    cv = it.get("cv") or {}
    t.append("CV: " + (cv.get("intro") or "") + " " + "; ".join(f"{r['tekst']} ({r['punten']})" for r in cv.get("regels", [])))
    t.append(f"Voorbeeldantwoord: {it.get('voorbeeld', '')}")
    return "\n".join(t)


def kritiek_tekst(doel: dict, items: list[dict], rekencheck: list[dict]) -> str:
    blok = []
    for i, (it, rc) in enumerate(zip(items, rekencheck), 1):
        fouten = [f"{c['omschrijving']}: {c['expressie']} = {c.get('berekend')} maar opgegeven {c['uitkomst']}"
                  for c in rc.get("controles", []) if not c.get("ok")]
        blok.append(f"=== Opgave {i}\n{_item_als_tekst(it)}\nRekencontrole door de app: "
                    + ("FOUT: " + "; ".join(fouten) if fouten else "geen fouten gevonden"))
    return f"""ZELFKRITIEK: je bent nu de examensecretaris die het werk van een collega controleert. Wees streng.

Leerdoel {doel['code']}: {doel['omschrijving']}

Beoordeel elke opgave op een schaal van 1 tot 5:
- vakinhoud: klopt de economie, sluit het aan bij de lesstof en de kennisbank?
- rekenwerk: kloppen alle getallen in context, cv en voorbeeldantwoord? (gebruik de rekencontrole van de app)
- examenstijl: werkwoord, vraagvorm, puntenverdeling en context als in het centraal examen?
- leerdoel: oefent de opgave precies dit leerdoel en het beoogde leergat?
- taal: helder, eenduidig, passend taalniveau, geen dubbelzinnigheid?
- correctievoorschrift: deelscores tellen op, alternatieve goede antwoorden genoemd, eenduidig na te kijken?
oordeel: goed (alle scores 4 of 5 en geen rekenfout), herzien (te repareren), afkeuren (opnieuw maken).
problemen: concreet en kort. advies: hoe de opgave beter wordt (hoogstens 40 woorden).
terugkerend: fouten die bij meerdere opgaven terugkomen, geformuleerd als regel voor de volgende keer (mag leeg).

{chr(10).join(blok)}"""


def herzien_tekst(doel: dict, te_herzien: list[tuple[int, dict, dict]], docent: str = "") -> str:
    blok = []
    for nr, it, beoordeling in te_herzien:
        blok.append(f"=== Opgave {nr}\n{_item_als_tekst(it)}\nProblemen: {'; '.join(beoordeling.get('problemen', []))}\n"
                    f"Advies: {beoordeling.get('advies', '')}")
    return f"""HERZIEN: verbeter de opgaven hieronder voor leerdoel {doel['code']} ({doel['omschrijving']}).
{('Opmerking van de docent: ' + docent) if docent else ''}
Los alle problemen op. Houd stap, soort en leerdoel gelijk, tenzij de docent iets anders vraagt. Is de \
context het probleem, kies dan een nieuwe context. vervangt = het nummer van de opgave die je vervangt.

{ITEMREGELS}

{chr(10).join(blok)}"""


def regel_tekst(vak: str, niveau: str, item: dict, oordeel: str, reden: str) -> str:
    return f"""De docent heeft een oefenopgave {'afgekeurd' if oordeel == 'afgekeurd' else 'laten aanpassen'}.
Zijn reden: "{reden}"

De opgave:
{_item_als_tekst(item)}

Maak van zijn reden één algemene kwaliteitsregel (hoogstens 40 woorden, gebiedende wijs) die je bij elke volgende \
opgave kunt toepassen, zodat deze fout niet meer voorkomt. geldt_voor: vak_en_niveau ({vak} {niveau}), vak \
({vak}) of alle. nuttig: false als de reden alleen over deze ene opgave gaat (bijvoorbeeld een tikfout) en er \
geen algemene regel uit volgt."""


def index_tekst(naam: str, map_: str, tekst: str, codes: str, partner: str = "") -> str:
    return f"""BIBLIOTHEEK: deel dit bestand op in fragmenten en koppel ze aan leerdoelen.

Bestand: {naam}
Map: {map_}

Leerdoelenregister (code | thema | leerdoel). Koppel alleen aan codes uit deze lijst; past er geen, laat \
leerdoel_codes leeg en vul wel eindtermen in:
{codes}

Regels:
- Lesmateriaal (uitleg, PowerPoint, mindmap, opdrachten): één fragment per paragraaf of onderwerp. kern = de \
inhoud in eigen woorden, hoogstens 80 woorden, met de begrippen en formules die de docent gebruikt. Bij een \
opgave ook vraag (ingekort, hoogstens 60 woorden) en antwoord.
- Examen (opgaven, eventueel met correctievoorschrift erachter): één fragment per examenvraag (soort \
examenvraag) met examen (bijv. 'havo 2024-I'), vraagnr, punten, vraag (ingekort, hoogstens 60 woorden, eigen \
woorden voor de context) en antwoord (de kern van het correctievoorschrift, hoogstens 60 woorden).
- Alleen een correctievoorschrift, eindtermen of syllabus: soort van het bestand invullen en hoogstens 30 fragmenten.
- paginas: zoals [p. ..] of [dia ..] in de tekst ('3' of '3-4'), anders leeg.
- Geen fragmenten voor voorbladen, inhoudsopgaven, regels voor de kandidaat of lege pagina's.

=== TEKST
{tekst}
{('=== CORRECTIEVOORSCHRIFT BIJ DIT EXAMEN' + chr(10) + partner) if partner else ''}"""


def leerdoel_regels(doelen: list[dict]) -> str:
    return "\n".join(f"{d['code']} | {d.get('thema', '')} | {d['omschrijving']}" for d in doelen)


def sla_regel_op(regel: dict) -> None:
    regels = kwaliteitsregels(alleen_actief=False)
    regels.append(regel)
    Path(KWALITEITSREGELS).write_text(json.dumps(regels, ensure_ascii=False, indent=1), encoding="utf-8")


def bewaar_regels(regels: list[dict]) -> None:
    Path(KWALITEITSREGELS).write_text(json.dumps(regels, ensure_ascii=False, indent=1), encoding="utf-8")
