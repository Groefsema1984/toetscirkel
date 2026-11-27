# -*- coding: utf-8 -*-
"""Leerdoelenregister: één vaste code per leerdoel, zodat de app over toetsen heen kan zien of een
leerling een leerdoel beheerst.

- Het register hoort bij een vak en een sector: havo en vwo delen één register (dezelfde stof),
  mavo heeft een eigen register. Codes: ECO-KRING-01, BECO-KOST-03, ECO-M-GELD-02.
- Eindtermcodes zijn geen sleutel: havo en vwo nummeren hun eindtermen verschillend. Claude
  groepeert op inhoud: dezelfde vaardigheid in een andere context is hetzelfde leerdoel.
- Elke vraag in toetsmatrijs krijgt een leerdoel_code en een koppel_status:
  'auto' (Claude was zeker), 'controleren' (docent kijkt even), 'bevestigd' (door de docent).
- Een leerdoel dat Claude na de eerste opbouw toevoegt, krijgt status 'nieuw' tot je het goedkeurt.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from datetime import datetime

import ai
import sessie
from config import kern

PREFIX = {("Economie", "havo-vwo"): "ECO", ("Economie", "mavo"): "ECO-M",
          ("Bedrijfseconomie", "havo-vwo"): "BECO", ("Bedrijfseconomie", "mavo"): "BECO-M"}


# ---------------------------------------------------------------------------- database
def zorg_voor_register() -> None:
    con = kern.open_db()
    try:
        bestaand = {r[1] for r in con.execute("PRAGMA table_info(toetsmatrijs)")}
        with con:
            for kolom in ("leerdoel_code", "koppel_status", "koppel_toelichting"):
                if kolom not in bestaand:
                    con.execute(f"ALTER TABLE toetsmatrijs ADD COLUMN {kolom} TEXT")
            con.execute(
                "CREATE TABLE IF NOT EXISTS leerdoelen_register ("
                " code TEXT PRIMARY KEY, vak TEXT NOT NULL, sector TEXT NOT NULL, thema TEXT,"
                " omschrijving TEXT NOT NULL, leerling_zin TEXT, status TEXT NOT NULL DEFAULT 'actief',"
                " aangemaakt TEXT, gewijzigd TEXT)")
    finally:
        con.close()


def sector_van(toets_id: str) -> tuple[str, str] | None:
    try:
        info = kern.lees_toetsmap(toets_id)
    except ValueError:
        return None
    return info.vak, ("mavo" if info.niveau == "mavo" else "havo-vwo")


def naam_scope(vak: str, sector: str) -> str:
    return f"{vak} {'mavo' if sector == 'mavo' else 'havo en vwo'}"


def scopes() -> list[tuple[str, str]]:
    con = kern.open_db()
    try:
        ids = [r[0] for r in con.execute("SELECT toets_id FROM toetsen ORDER BY toets_id")]
    finally:
        con.close()
    return sorted({sc for t in ids if (sc := sector_van(t))})


def items(vak: str, sector: str) -> list[dict]:
    """Alle vragen van de toetsen in deze scope, met hun leerdoel en koppeling."""
    con = kern.open_db()
    try:
        rijen = con.execute(
            "SELECT m.item_id, m.toets_id, t.datum, m.vraagnummer, m.domein_eindterm, m.bloom_niveau,"
            " m.max_score, m.tweefasen_label, m.leerdoel, m.leerdoel_leerling, m.onderwerp,"
            " m.leerdoel_code, m.koppel_status, m.koppel_toelichting"
            " FROM toetsmatrijs m JOIN toetsen t ON t.toets_id = m.toets_id"
            " ORDER BY t.datum, m.toets_id, m.vraagnummer").fetchall()
    finally:
        con.close()
    return [dict(r) for r in rijen if sector_van(r["toets_id"]) == (vak, sector)]


def register(vak: str, sector: str) -> list[dict]:
    con = kern.open_db()
    try:
        rijen = con.execute("SELECT * FROM leerdoelen_register WHERE vak = ? AND sector = ? "
                            "ORDER BY code", (vak, sector)).fetchall()
        return [dict(r) for r in rijen]
    finally:
        con.close()


def alle_codes() -> dict[str, dict]:
    con = kern.open_db()
    try:
        return {r["code"]: dict(r) for r in con.execute("SELECT * FROM leerdoelen_register")}
    finally:
        con.close()


def _afkorting(thema: str) -> str:
    kaal = unicodedata.normalize("NFKD", thema or "").encode("ascii", "ignore").decode().upper()
    letters = re.sub(r"[^A-Z]", "", kaal)
    return (letters[:5] or "ALG")


def _nieuwe_code(con, prefix: str, thema: str) -> str:
    stam = f"{prefix}-{_afkorting(thema)}-"
    nummers = [int(r[0][len(stam):]) for r in con.execute(
        "SELECT code FROM leerdoelen_register WHERE code LIKE ?", (stam + "%",))
        if r[0][len(stam):].isdigit()]
    return f"{stam}{max(nummers, default=0) + 1:02d}"


def maak_leerdoel(con, vak: str, sector: str, thema: str, omschrijving: str,
                  leerling_zin: str, status: str) -> str:
    code = _nieuwe_code(con, PREFIX[(vak, sector)], thema)
    nu = datetime.now().isoformat(timespec="seconds")
    con.execute("INSERT INTO leerdoelen_register (code, vak, sector, thema, omschrijving, leerling_zin,"
                " status, aangemaakt, gewijzigd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, vak, sector, thema.strip() or "Algemeen", omschrijving.strip(),
                 leerling_zin.strip(), status, nu, nu))
    return code


def koppel(con, item_id: int, code: str | None, status: str, toelichting: str = "") -> None:
    con.execute("UPDATE toetsmatrijs SET leerdoel_code = ?, koppel_status = ?, koppel_toelichting = ? "
                "WHERE item_id = ?", (code, status, toelichting or None, item_id))


# ---------------------------------------------------------------------------- Claude
GRANULARITEIT = """Wat is één leerdoel?
- Eén vaardigheid die een leerling los kan oefenen, bijvoorbeeld "investeringen en besparingen \
berekenen met de kringloop" of "uitleggen hoe averechtse selectie de premie laat stijgen".
- Niet te breed: "kringloop" of "verzekeren" is een thema, geen leerdoel.
- Niet te smal: de context (Noordland, Bergzeker, een naam van een leerling) hoort er niet bij. \
Dezelfde vaardigheid in een andere context is hetzelfde leerdoel.
- Havo en vwo: dezelfde vaardigheid is hetzelfde leerdoel. Een vwo-vraag die duidelijk extra stof \
toetst (bijvoorbeeld wisselkoers en concurrentiepositie), is een eigen leerdoel.
- Eindtermcodes zijn geen sleutel: havo en vwo nummeren verschillend.
- Een vraag die twee dingen toetst, koppel je aan het leerdoel waar de meeste punten om draaien."""

SCHEMA_OPBOUW = {
    "type": "object", "additionalProperties": False, "required": ["leerdoelen"],
    "properties": {"leerdoelen": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["thema", "omschrijving", "leerling_zin", "vragen"],
        "properties": {"thema": {"type": "string"}, "omschrijving": {"type": "string"},
                       "leerling_zin": {"type": "string"},
                       "vragen": {"type": "array", "items": {"type": "integer"}}}}}},
}

SCHEMA_KOPPEL = {
    "type": "object", "additionalProperties": False, "required": ["vragen"],
    "properties": {"vragen": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["nr", "code", "zekerheid", "toelichting", "nieuw_thema", "nieuw_omschrijving",
                     "nieuw_leerling_zin"],
        "properties": {"nr": {"type": "integer"}, "code": {"type": "string"},
                       "zekerheid": {"type": "string", "enum": ["hoog", "middel", "laag"]},
                       "toelichting": {"type": "string"}, "nieuw_thema": {"type": "string"},
                       "nieuw_omschrijving": {"type": "string"},
                       "nieuw_leerling_zin": {"type": "string"}}}}},
}


def _regel(nr: int, it: dict) -> str:
    return (f"{nr} | {it['toets_id']} vraag {it['vraagnummer']} | eindterm {it['domein_eindterm'] or '-'}"
            f" | {it['bloom_niveau'] or '-'} | leerdoel: {it['leerdoel'] or '-'}"
            f" | leerlingzin: {it['leerdoel_leerling'] or '-'} | context: {it['onderwerp'] or '-'}")


def _systeem(vak: str, sector: str) -> list[dict]:
    return [{"type": "text", "text": f"""Je beheert het leerdoelenregister voor {naam_scope(vak, sector)} \
van een docent in het Nederlandse voortgezet onderwijs. Elk leerdoel krijgt een vaste code, zodat \
over toetsen heen te zien is of een leerling een leerdoel beheerst, en zodat er gericht \
oefenmateriaal bij gezocht kan worden.

{GRANULARITEIT}

Formuleer in het Nederlands:
- thema: het hoofdonderwerp in één of twee woorden (bijvoorbeeld Kringloop, Groei, \
Inkomensverdeling, Belastingen, Verzekeren, Marktvormen, Speltheorie). Gebruik voor hetzelfde \
onderwerp steeds precies hetzelfde woord.
- omschrijving: het leerdoel voor de docent, kort en zonder context (maximaal 12 woorden).
- leerling_zin: hetzelfde als "Ik kan ..."-zin voor de leerling, concreet en controleerbaar \
(maximaal 16 woorden)."""}]


def bouw_op(vak: str, sector: str, client=None) -> dict:
    """Eerste opbouw: Claude groepeert alle vragen van deze scope tot leerdoelen."""
    lijst = [it for it in items(vak, sector) if it["leerdoel"] and not it["leerdoel_code"]]
    if not lijst:
        return {"nieuw": 0, "gekoppeld": 0}
    tekst = "\n".join(_regel(i + 1, it) for i, it in enumerate(lijst))
    opdracht = f"""Hieronder staan alle vragen uit de toetsen van {naam_scope(vak, sector)} (nummer | \
toets en vraag | eindterm | Bloom | leerdoel uit de toetsmatrijs | leerlingzin | context).

Stel het leerdoelenregister op: groepeer de vragen die hetzelfde leerdoel toetsen. Elke vraag hoort \
bij precies één leerdoel; gebruik de nummers vooraan. Een leerdoel mag ook maar één vraag hebben.

{tekst}"""
    uit = kern.vraag_json(client or ai.maak_client(), _systeem(vak, sector),
                          [{"type": "text", "text": opdracht}], SCHEMA_OPBOUW, max_tokens=32000)
    gezien: set[int] = set()
    con = kern.open_db()
    nieuw = gekoppeld = 0
    try:
        with con:
            for ld in uit.get("leerdoelen", []):
                leden = [n for n in ld["vragen"] if 1 <= n <= len(lijst) and n not in gezien]
                if not leden:
                    continue
                gezien.update(leden)
                code = maak_leerdoel(con, vak, sector, ld["thema"], ld["omschrijving"],
                                     ld["leerling_zin"], "actief")
                nieuw += 1
                for n in leden:
                    koppel(con, lijst[n - 1]["item_id"], code, "auto")
                    gekoppeld += 1
            for n, it in enumerate(lijst, 1):            # vergeten vragen: eigen leerdoel
                if n not in gezien:
                    code = maak_leerdoel(con, vak, sector, "Algemeen", it["leerdoel"],
                                         it["leerdoel_leerling"] or "", "nieuw")
                    koppel(con, it["item_id"], code, "controleren",
                           "Claude heeft deze vraag niet ingedeeld.")
                    nieuw += 1
                    gekoppeld += 1
    finally:
        con.close()
    return {"nieuw": nieuw, "gekoppeld": gekoppeld}


def koppel_nieuwe(vak: str, sector: str, client=None) -> dict:
    """Koppelt vragen zonder code aan het bestaande register; onbekende leerdoelen worden 'nieuw'."""
    reg = register(vak, sector)
    lijst = [it for it in items(vak, sector) if it["leerdoel"] and not it["leerdoel_code"]]
    if not lijst:
        return {"nieuw": 0, "gekoppeld": 0}
    reg_tekst = "\n".join(f"{r['code']} | {r['thema']} | {r['omschrijving']}" for r in reg)
    tekst = "\n".join(_regel(i + 1, it) for i, it in enumerate(lijst))
    opdracht = f"""Het register van {naam_scope(vak, sector)} (code | thema | leerdoel):
{reg_tekst}

Koppel elke vraag hieronder aan een code uit het register. Past geen enkel leerdoel, gebruik dan \
code "NIEUW" en vul nieuw_thema, nieuw_omschrijving en nieuw_leerling_zin in; geef vragen die \
hetzelfde nieuwe leerdoel toetsen precies dezelfde nieuw_omschrijving. Bij een bestaande code laat \
je de nieuw_-velden leeg. zekerheid: hoe zeker je bent van de koppeling. toelichting: alleen bij \
middel of laag, maximaal 20 woorden.

{tekst}"""
    uit = kern.vraag_json(client or ai.maak_client(), _systeem(vak, sector),
                          [{"type": "text", "text": opdracht}], SCHEMA_KOPPEL, max_tokens=16000)
    bekend = {r["code"] for r in reg}
    per = {}
    for v in uit.get("vragen", []):
        if 1 <= v["nr"] <= len(lijst):
            per.setdefault(v["nr"], v)
    nieuwe_codes: dict[str, str] = {}
    con = kern.open_db()
    nieuw = gekoppeld = 0
    try:
        with con:
            for n, it in enumerate(lijst, 1):
                v = per.get(n)
                if v is None:
                    continue                                  # volgende keer opnieuw proberen
                code, toel = v["code"].strip(), v["toelichting"].strip()
                if code in bekend:
                    status = "auto" if v["zekerheid"] == "hoog" else "controleren"
                else:
                    sleutel = (v["nieuw_omschrijving"] or it["leerdoel"]).strip().lower()
                    if sleutel not in nieuwe_codes:
                        nieuwe_codes[sleutel] = maak_leerdoel(
                            con, vak, sector, v["nieuw_thema"] or "Algemeen",
                            v["nieuw_omschrijving"] or it["leerdoel"],
                            v["nieuw_leerling_zin"] or it["leerdoel_leerling"] or "", "nieuw")
                        nieuw += 1
                    code, status = nieuwe_codes[sleutel], "controleren"
                    toel = toel or "Nieuw leerdoel: nog niet in het register."
                koppel(con, it["item_id"], code, status, toel)
                gekoppeld += 1
    finally:
        con.close()
    return {"nieuw": nieuw, "gekoppeld": gekoppeld}


def vul_ontbrekende_leerdoelen(vak: str, sector: str) -> list[str]:
    """Toetsen waarvan de leerdoelen nog niet uit het correctievoorschrift zijn gehaald: nu doen."""
    import pipeline                                         # pas hier: pipeline importeert register
    zonder = sorted({it["toets_id"] for it in items(vak, sector) if not it["leerdoel"]})
    niet_gelukt = []
    for toets_id in zonder:
        try:
            if sessie.lees(toets_id):
                pipeline.vul_leerdoelen(toets_id)
            else:
                niet_gelukt.append(toets_id)
        except Exception:  # noqa: BLE001
            niet_gelukt.append(toets_id)
    return niet_gelukt


def bijwerken(vak: str, sector: str, client=None) -> dict:
    """Eén knop: leerdoelen ophalen waar ze ontbreken, dan opbouwen (leeg register) of koppelen."""
    zonder = vul_ontbrekende_leerdoelen(vak, sector)
    client = client or ai.maak_client()
    uit = bouw_op(vak, sector, client) if not register(vak, sector) else koppel_nieuwe(vak, sector, client)
    uit["zonder_leerdoelen"] = zonder
    return uit


def bijwerken_voor_toets(toets_id: str, client=None) -> dict | None:
    sc = sector_van(toets_id)
    return bijwerken(*sc, client) if sc else None


CODE_PATROON = re.compile(r"\b((?:BECO|ECO)(?:-M)?-[A-Z0-9]{2,8}-\d{2,3})\b")


def codes_uit_cv(cv_tekst: str) -> dict[int, str]:
    """Leerdoelcodes die de oefentool in het correctievoorschrift zet (regel 'LEERDOELCODES:' of de
    toetsmatrijs-tabel: rij met vraagnummer en code). Alleen codes die in het register staan."""
    bekend = set(alle_codes())
    uit: dict[int, str] = {}
    blok = re.search(r"LEERDOELCODES:\s*(.+)", cv_tekst or "")
    if blok:
        for nr, code in re.findall(r"(\d+)\s*=\s*([A-Z0-9-]+)", blok.group(1)):
            if code in bekend:
                uit[int(nr)] = code
    for regel in (cv_tekst or "").splitlines():
        m = re.match(r"^\s*(\d{1,2})\s*\|", regel)
        c = CODE_PATROON.search(regel)
        if m and c and c.group(1) in bekend:
            uit.setdefault(int(m.group(1)), c.group(1))
    return uit


# ---------------------------------------------------------------------------- docent
def te_controleren(vak: str, sector: str) -> list[dict]:
    return [it for it in items(vak, sector) if it["koppel_status"] == "controleren"
            or (it["leerdoel"] and not it["leerdoel_code"])]


def overzicht(vak: str, sector: str) -> list[dict]:
    """Register met per leerdoel de gekoppelde vragen."""
    per = defaultdict(list)
    for it in items(vak, sector):
        if it["leerdoel_code"]:
            per[it["leerdoel_code"]].append(it)
    uit = []
    for r in register(vak, sector):
        leden = per.get(r["code"], [])
        uit.append(dict(r, vragen=leden,
                        eindtermen=sorted({it["domein_eindterm"] for it in leden if it["domein_eindterm"]}),
                        niveaus=sorted({kern.lees_toetsmap(it["toets_id"]).niveau for it in leden})))
    return uit


def bevestig(item_id: int, code: str) -> None:
    con = kern.open_db()
    try:
        with con:
            if code == "__nieuw__":
                it = con.execute("SELECT toets_id, leerdoel, leerdoel_leerling FROM toetsmatrijs "
                                 "WHERE item_id = ?", (item_id,)).fetchone()
                vak, sector = sector_van(it["toets_id"])
                code = maak_leerdoel(con, vak, sector, "Algemeen", it["leerdoel"] or "Leerdoel",
                                     it["leerdoel_leerling"] or "", "actief")
            elif not con.execute("SELECT 1 FROM leerdoelen_register WHERE code = ?", (code,)).fetchone():
                raise ValueError("Onbekende code.")
            koppel(con, item_id, code, "bevestigd")
    finally:
        con.close()


def wijzig(code: str, omschrijving: str, leerling_zin: str, thema: str) -> None:
    con = kern.open_db()
    try:
        with con:
            con.execute("UPDATE leerdoelen_register SET omschrijving = ?, leerling_zin = ?, thema = ?, "
                        "status = 'actief', gewijzigd = ? WHERE code = ?",
                        (omschrijving.strip(), leerling_zin.strip(), thema.strip(),
                         datetime.now().isoformat(timespec="seconds"), code))
    finally:
        con.close()


def samenvoegen(van: str, naar: str) -> None:
    """Alle vragen van 'van' gaan naar 'naar'; 'van' verdwijnt uit het register."""
    if van == naar:
        raise ValueError("Kies een ander leerdoel.")
    con = kern.open_db()
    try:
        a = con.execute("SELECT vak, sector FROM leerdoelen_register WHERE code = ?", (van,)).fetchone()
        b = con.execute("SELECT vak, sector FROM leerdoelen_register WHERE code = ?", (naar,)).fetchone()
        if not a or not b or tuple(a) != tuple(b):
            raise ValueError("Samenvoegen kan alleen binnen hetzelfde register.")
        with con:
            con.execute("UPDATE toetsmatrijs SET leerdoel_code = ?, koppel_status = 'bevestigd' "
                        "WHERE leerdoel_code = ?", (naar, van))
            con.execute("DELETE FROM leerdoelen_register WHERE code = ?", (van,))
    finally:
        con.close()
