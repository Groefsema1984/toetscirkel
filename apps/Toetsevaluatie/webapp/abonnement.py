# -*- coding: utf-8 -*-
"""Claude via je abonnement (Claude Code, 'claude -p') in plaats van betaald API-tegoed.

Alle verzoeken van de webapp (toetsmatrijs, namen lezen, nakijken, feedbackstroken, klassenadvies,
leerdoelen) lopen via kern.vraag_json. Die wordt hier vervangen door een versie die eerst het
abonnement gebruikt, met het sterkste model (standaard Opus). Kan het abonnement niet verder (limiet
bereikt, niet ingelogd, Claude Code niet gevonden), dan stopt de stap en vraagt de app de docent of
hij met API-tegoed verder wil. Zonder die toestemming wordt er nooit tegoed gebruikt.

Afbeeldingen (de scans) gaan als tijdelijke bestanden naar Claude Code, die ze met de Read-tool
bekijkt. De tijdelijke map wordt direct na het verzoek weer verwijderd.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from config import DATA, kern

log = logging.getLogger("webapp")
INSTELLINGEN = DATA / "instellingen.json"
MOTOREN = {"abonnement": "Abonnement; API-tegoed alleen na mijn toestemming (aanbevolen)",
           "api": "Altijd API-tegoed (betaald)"}
MODELLEN = {"opus": "Opus (sterkst, aanbevolen)", "sonnet": "Sonnet (sneller, gaat minder snel door je limiet)"}

_origineel = getattr(kern, "_vraag_json_api", None) or kern.vraag_json
kern._vraag_json_api = _origineel


class ToestemmingNodig(RuntimeError):
    """Het abonnement kan nu niet verder; API-tegoed gebruiken mag alleen na toestemming van de docent."""


class AbonnementOp(RuntimeError):
    pass


# ---------------------------------------------------------------------------- instellingen
def instellingen() -> dict:
    try:
        d = json.loads(INSTELLINGEN.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        d = {}
    return {"motor": d.get("motor") if d.get("motor") in MOTOREN else "abonnement",
            "abonnement_model": d.get("abonnement_model") if d.get("abonnement_model") in MODELLEN else "opus"}


def bewaar_instellingen(motor: str, model: str) -> None:
    d = instellingen()
    if motor in MOTOREN:
        d["motor"] = motor
    if model in MODELLEN:
        d["abonnement_model"] = model
    INSTELLINGEN.parent.mkdir(parents=True, exist_ok=True)
    INSTELLINGEN.write_text(json.dumps(d, indent=1), encoding="utf-8")
    _staat["model_terug"] = ""


def motor() -> str:
    return instellingen()["motor"]


# ---------------------------------------------------------------------------- toestemming per taak
# Elke achtergrondtaak zet vooraf of de docent voor deze stap API-tegoed heeft goedgekeurd. Threads in een
# pool erven dat niet vanzelf; gebruik daarom met_context() bij het starten van een thread.
_ctx = threading.local()


def zet(api_ok: bool = False) -> None:
    _ctx.api_ok = bool(api_ok)


def api_ok() -> bool:
    return getattr(_ctx, "api_ok", False)


def met_context(fn):
    """Geeft fn terug, maar dan met de toestemming van de huidige thread (voor pool.map en threads)."""
    ok = api_ok()

    def binnen(*a, **k):
        zet(ok)
        return fn(*a, **k)
    return binnen


# ---------------------------------------------------------------------------- Claude Code
_staat = {"op_tot": 0.0, "melding": "", "model_terug": ""}
_status_cache: dict = {}


def claude_pad() -> str | None:
    for k in (os.environ.get("TOETS_CLAUDE_CODE"), shutil.which("claude"), str(Path.home() / ".claude/local/claude"),
              str(Path.home() / ".local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if k and Path(k).exists():
            return k
    return None


def _schone_env(max_tokens: int = 32000) -> dict:
    """Zonder API-sleutel: Claude Code gebruikt dan altijd het abonnement en nooit betaald tegoed."""
    e = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        e.pop(k, None)
    e["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max(max_tokens, 16000))
    return e


def weer_proberen() -> None:
    """De docent wil het opnieuw via het abonnement proberen (bijvoorbeeld omdat de limiet weer vrij is)."""
    _staat.update(op_tot=0.0, melding="")
    _status_cache.clear()


def status() -> dict:
    """Voor het scherm: is Claude Code er, ben je ingelogd, is de limiet bereikt?"""
    nu = time.time()
    limiet = _staat["op_tot"] > nu
    if _status_cache.get("t", 0) > nu - 60:
        return dict(_status_cache["v"], limiet=limiet, melding=_staat["melding"] if limiet else "")
    c = claude_pad()
    if not c:
        v = {"gevonden": False, "ingelogd": False,
             "tekst": "Claude Code niet gevonden (start één keer Toetsconstructie om het te installeren)"}
    else:
        try:
            uit = subprocess.run([c, "auth", "status", "--json"], capture_output=True, text=True, timeout=20,
                                 env=_schone_env()).stdout
            ingelogd = bool(json.loads(uit or "{}").get("loggedIn"))
        except Exception:  # noqa: BLE001
            ingelogd = False
        v = {"gevonden": True, "ingelogd": ingelogd,
             "tekst": "Ingelogd met je abonnement" if ingelogd else
             "Niet ingelogd: start Toetsconstructie of typ 'claude auth login' in Terminal"}
    _status_cache.update(t=nu, v=v)
    return dict(v, limiet=limiet, melding=_staat["melding"] if limiet else "")


def kan_werken() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or claude_pad() is not None


def _json_uit(tekst: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", tekst.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        begin, eind = t.find("{"), t.rfind("}")
        if begin >= 0 and eind > begin:
            return json.loads(t[begin:eind + 1])
        raise


def _vul_aan(obj, schema: dict):
    """Ontbrekende velden aanvullen, zodat een iets onvolledig antwoord geen fout geeft."""
    soort = schema.get("type")
    if isinstance(soort, list):                      # bijv. ["number", "null"]
        if obj is None and "null" in soort:
            return None
        soort = next((x for x in soort if x != "null"), None)
    if "enum" in schema and obj not in schema["enum"]:
        return schema["enum"][0]
    if soort == "object":
        obj = obj if isinstance(obj, dict) else {}
        for k, sub in schema.get("properties", {}).items():
            obj[k] = _vul_aan(obj[k], sub) if k in obj else _leeg(sub)
        return obj
    if soort == "array":
        return [_vul_aan(x, schema.get("items", {})) for x in obj] if isinstance(obj, list) else []
    if soort == "integer":
        try:
            return int(round(float(obj)))
        except (TypeError, ValueError):
            return 0
    if soort == "number":
        try:
            return float(str(obj).replace(",", "."))
        except (TypeError, ValueError):
            return 0.0
    if soort == "boolean":
        return bool(obj)
    if soort == "string":
        return obj if isinstance(obj, str) else ("" if obj is None else str(obj))
    return obj


def _leeg(schema: dict):
    soort = schema.get("type")
    if isinstance(soort, list):
        return None if "null" in soort else _leeg(dict(schema, type=soort[0]))
    if "enum" in schema:
        return schema["enum"][0]
    if soort == "object":
        return _vul_aan({}, schema)
    return {"array": [], "integer": 0, "number": 0.0, "boolean": False}.get(soort, "")


_LIMIET = re.compile(r"usage limit|limit reached|rate.?limit|quota|429|overloaded|5-hour|weekly limit|"
                     r"not logged in|log ?in|/login|authenticat|credit balance|invalid api key", re.I)
_MODEL_FOUT = re.compile(r"model.{0,40}(not (found|available|supported)|unavailable|access)|"
                         r"(no access|not available).{0,40}model|invalid model", re.I)


def _via_abonnement(systeem: list[dict], inhoud: list[dict], schema: dict, max_tokens: int) -> dict:
    c = claude_pad()
    if not c:
        raise AbonnementOp("Claude Code is niet gevonden op deze Mac.")
    model = _staat["model_terug"] or instellingen()["abonnement_model"]
    sys_tekst = "\n\n".join(b.get("text", "") for b in systeem if b.get("type", "text") == "text")
    with tempfile.TemporaryDirectory(prefix="toetsevaluatie_") as tmp:
        delen, beelden = [], []
        for b in inhoud:
            if b.get("type") == "image":
                naam = f"afbeelding_{len(beelden) + 1:02d}.jpg"
                Path(tmp, naam).write_bytes(base64.standard_b64decode(b["source"]["data"]))
                beelden.append(naam)
                delen.append(f"[afbeelding: {naam}]")
            elif b.get("type") == "text":
                delen.append(b["text"])
        opdracht = "\n\n".join(delen)
        if beelden:
            opdracht = (f"De afbeeldingen staan als bestanden in de huidige map ({len(beelden)} stuks: "
                        f"{', '.join(beelden)}). Bekijk ze ALLEMAAL met de Read-tool (lees ze tegelijk) voordat je "
                        "antwoordt. Op de plek van [afbeelding: ...] hieronder hoort die afbeelding.\n\n" + opdracht)
        opdracht += ("\n\nANTWOORD: geef alleen één JSON-object, zonder uitleg en zonder codeblok, dat precies "
                     "voldoet aan dit JSON-schema (alle velden invullen):\n" + json.dumps(schema, ensure_ascii=False))
        verboden = "Bash,Edit,Write,MultiEdit,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit,TodoWrite"
        for poging in range(3):
            cmd = [c, "-p", "--output-format", "json", "--model", model, "--system-prompt", sys_tekst,
                   "--max-turns", str(len(beelden) + 4 if beelden else 1), "--disallowedTools", verboden]
            if beelden:
                cmd += ["--allowedTools", "Read"]
            try:
                proc = subprocess.run(cmd, input=opdracht, capture_output=True, text=True, timeout=1800, cwd=tmp,
                                      env=_schone_env(max_tokens))
            except subprocess.TimeoutExpired:
                if poging < 2:
                    continue
                raise kern.ApiFout("Claude Code reageerde niet binnen een half uur.")
            try:
                d = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
            except (json.JSONDecodeError, IndexError):
                d = {}
            melding = str(d.get("result") or proc.stderr or proc.stdout or "")[-600:]
            if proc.returncode != 0 or d.get("is_error") or d.get("subtype", "success") != "success":
                if model != "sonnet" and _MODEL_FOUT.search(melding):
                    log.warning("Model %s niet beschikbaar via het abonnement (%s); verder met sonnet", model, melding)
                    model = _staat["model_terug"] = "sonnet"
                    continue
                if _LIMIET.search(melding):
                    _staat.update(op_tot=time.time() + 30 * 60, melding=melding[:200])
                    raise AbonnementOp(melding)
                if d.get("subtype") == "error_max_turns" and poging < 2:
                    continue
                if poging < 2:
                    continue
                raise kern.ApiFout(f"Claude Code gaf een fout: {melding[:300]}")
            g = d.get("usage") or {}
            log.info("   abonnement (%s): invoer %s, uitvoer %s", model, g.get("input_tokens"), g.get("output_tokens"))
            try:
                return _vul_aan(_json_uit(d.get("result", "")), schema)
            except json.JSONDecodeError:
                opdracht += "\n\nLET OP: je vorige antwoord was geen geldige JSON. Geef nu alleen het JSON-object."
    raise kern.ApiFout("Claude Code gaf geen geldige JSON.")


def vraag_json(client, systeem: list[dict], inhoud: list[dict], schema: dict, max_tokens: int) -> dict:
    """Vervangt kern.vraag_json: eerst het abonnement, API-tegoed alleen met toestemming."""
    if motor() == "abonnement":
        ok = api_ok()
        limiet = _staat["op_tot"] > time.time()
        if limiet and not ok:
            raise ToestemmingNodig(_staat["melding"] or "De limiet van je abonnement is bereikt.")
        if not limiet:
            try:
                return _via_abonnement(systeem, inhoud, schema, max_tokens)
            except AbonnementOp as fout:
                if not ok:
                    raise ToestemmingNodig(str(fout)[:300]) from fout
                log.warning("Abonnement kan niet; met toestemming van de docent verder via de API: %s", fout)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise kern.ApiFout("Er is geen API-sleutel. Wacht tot je abonnement weer ruimte heeft, of start de app "
                           "opnieuw en plak een API-sleutel.")
    if client is None:
        client = kern.anthropic.Anthropic(max_retries=6, timeout=900.0)
    return _origineel(client, systeem, inhoud, schema, max_tokens)


kern.vraag_json = vraag_json
