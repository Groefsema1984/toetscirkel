# -*- coding: utf-8 -*-
"""Alle verzoeken aan de Anthropic API die specifiek zijn voor de webapp.

De vakregels, het lezen van de toetsmatrijs en het lezen van namen komen uit toets_evaluator.py
(kern). Hier staan: nakijken met plaats van het antwoord op de pagina, de leerregels van de docent,
de feedforward per leerling en het klassenadvies.
"""
from __future__ import annotations

import copy
import os

import abonnement  # noqa: F401  (vervangt kern.vraag_json: eerst je abonnement, API alleen met toestemming)
from config import MAX_LEERREGELS_IN_PROMPT, kern

anthropic = kern.anthropic
ToestemmingNodig = abonnement.ToestemmingNodig


def heeft_sleutel() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def kan_werken() -> bool:
    """Er is Claude Code (je abonnement) of een API-sleutel."""
    return abonnement.kan_werken()


def maak_client():
    """API-client, of None zonder sleutel (via het abonnement is er geen client nodig)."""
    if not heeft_sleutel():
        return None
    return anthropic.Anthropic(max_retries=6, timeout=900.0)


# ---------------------------------------------------------------------------- nakijken
SCHEMA_NAKIJKEN = copy.deepcopy(kern.SCHEMA_EVALUATIE)
_vraag = SCHEMA_NAKIJKEN["properties"]["vragen"]["items"]
_vraag["properties"].update({
    "niet_gemaakt": {"type": "boolean"},
    "pagina": {"type": "integer"},
    "y_boven": {"type": "integer"},
    "y_onder": {"type": "integer"},
})
_vraag["required"] = _vraag["required"] + ["niet_gemaakt", "pagina", "y_boven", "y_onder"]

AANVULLING = """AANVULLING OP DE WERKWIJZE PER VRAAG
8. niet_gemaakt: true als de leerling bij deze vraag niets heeft opgeschreven (ook geen begin).
9. pagina: het nummer van de pagina (zoals bij de afbeelding vermeld) waarop het antwoord op deze \
vraag begint. 0 als de vraag niet gemaakt of niet te vinden is.
10. y_boven en y_onder: waar het antwoord op die pagina staat, als percentage van de paginahoogte \
(0 = bovenrand, 100 = onderrand). Neem de docentscore in de kantlijn mee in het gebied. Loopt het \
antwoord door op de volgende pagina, geef dan y_onder = 100. Bij pagina 0: beide 0.
11. Wijkt jouw score af van de docentscore, baseer type_leergat en foutomschrijving dan op het \
puntenverlies dat jij zelf ziet (dit gaat voor op de regel hierboven) en leg in toelichting in \
maximaal 25 woorden uit waarom je afwijkt. De docent beslist daarna."""


def leerregels_tekst(regels: list[dict], vak: str) -> str:
    """Eerdere afwijkingen van de docent, als voorbeeld van zijn nakijkstijl."""
    eigen = [r for r in regels if r.get("vak") == vak] or regels
    eigen = eigen[-MAX_LEERREGELS_IN_PROMPT:]
    if not eigen:
        return ""
    regels_tekst = []
    for r in eigen:
        regels_tekst.append(
            f"- {r.get('vak', '')} {r.get('niveau', '')}, vraag over: {r.get('domein', 'onbekend')}. "
            f"Leerling schreef: \"{r.get('antwoord', '')}\". Jij gaf {r.get('ai_score')} van "
            f"{r.get('max_score')}, de docent gaf {r.get('docent_score')}. "
            f"Reden van de docent: {r.get('reden', '')}")
    tekst = "\n".join(regels_tekst)[-9000:]
    return (
        "NAKIJKSTIJL VAN DEZE DOCENT\n"
        "Bij eerdere toetsen week de docent bewust af van jouw score. Leid hieruit af hoe hij "
        "nakijkt en pas dezelfde lijn toe in vergelijkbare gevallen. Het correctievoorschrift en "
        "de vakregels blijven leidend waar ze iets anders voorschrijven.\n" + tekst)


def systeem_nakijken(info, cv_tekst: str, matrijs: list[dict], regels: list[dict]) -> list[dict]:
    blokken = kern.systeemprompt(info, cv_tekst, matrijs)
    extra = AANVULLING
    stijl = leerregels_tekst(regels, info.vak)
    if stijl:
        extra += "\n\n" + stijl
    blokken.insert(1, {"type": "text", "text": extra})   # vóór het gecachete correctievoorschrift
    return blokken


def kijk_na(client, systeem: list[dict], beelden: list[bytes]) -> dict:
    inhoud: list[dict] = []
    for nr, beeld in enumerate(beelden, 1):
        inhoud.append({"type": "text", "text": f"Pagina {nr} van {len(beelden)}:"})
        inhoud.append(kern._beeldblok(beeld))
    inhoud.append({"type": "text", "text":
                   "Dit is het volledige ingescande werk van één leerling. Beoordeel alle vragen "
                   "uit de toetsmatrijs volgens de werkwijze en de aanvulling."})
    return kern.vraag_json(client, systeem, inhoud, SCHEMA_NAKIJKEN, max_tokens=32000)


# ---------------------------------------------------------------------------- leerdoelen
SCHEMA_LEERDOELEN = {
    "type": "object", "additionalProperties": False, "required": ["vragen"],
    "properties": {"vragen": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["vraagnummer", "leerdoel", "leerdoel_leerling", "onderwerp"],
        "properties": {"vraagnummer": {"type": "integer"}, "leerdoel": {"type": "string"},
                       "leerdoel_leerling": {"type": "string"}, "onderwerp": {"type": "string"}}}}},
}


def leerdoelen(client, info, cv_tekst: str, nummers: list[int]) -> dict[int, dict]:
    """Leerdoel, leerdoel in leerlingtaal en onderwerp per vraag, uit het correctievoorschrift."""
    opdracht = f"""Hieronder staat het correctievoorschrift met toetsmatrijs van toets {info.toets_id} \
({info.vak}, {info.niveau}). Geef voor elke vraag ({', '.join(map(str, nummers))}):

- leerdoel: het leerdoel letterlijk zoals in de kolom Leerdoel van de toetsmatrijs. Staat er geen \
leerdoel bij de vraag, formuleer het dan kort zelf vanuit de vraag en het voorbeeldantwoord.
- leerdoel_leerling: hetzelfde leerdoel als zin voor de leerling, beginnend met "Ik kan", op \
taalniveau {info.taalniveau}. Concreet en controleerbaar, maximaal 15 woorden, geen vraagnummer. \
Voorbeeld: "Inkomstenbelasting box 1 berekenen" wordt "Ik kan uitrekenen hoeveel \
inkomstenbelasting iemand in box 1 betaalt."
- onderwerp: waar de vraag over gaat, letterlijk uit de kolom Thema / context als die er is \
(bijvoorbeeld "Noordland: kringloop"), anders in maximaal 5 woorden.

Neem alles over uit het document; verzin geen leerdoelen als ze er staan.

<correctievoorschrift>
{cv_tekst}
</correctievoorschrift>"""
    systeem = [{"type": "text", "text": "Je haalt gegevens uit een correctievoorschrift van een "
                "Nederlands schoolexamen. Schrijf in het Nederlands."}]
    uit = kern.vraag_json(client, systeem, [{"type": "text", "text": opdracht}],
                          SCHEMA_LEERDOELEN, max_tokens=16000)
    per = {}
    for v in uit.get("vragen", []):
        if int(v["vraagnummer"]) in nummers:
            per.setdefault(int(v["vraagnummer"]), {k: v[k].strip() for k in
                                                   ("leerdoel", "leerdoel_leerling", "onderwerp")})
    return per


# ---------------------------------------------------------------------------- feedforward
SCHEMA_FEEDFORWARD = {
    "type": "object", "additionalProperties": False,
    "required": ["sterk", "leergaten", "actieplan", "reflectievragen"],
    "properties": {
        "sterk": {"type": "string"},
        "leergaten": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["code", "uitleg"],
            "properties": {"code": {"type": "string", "enum": list(kern.LEERGATEN)},
                           "uitleg": {"type": "string"}}}},
        "actieplan": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["stap", "hoe"],
            "properties": {"stap": {"type": "string"}, "hoe": {"type": "string"}}}},
        "reflectievragen": {"type": "array", "items": {"type": "string"}},
    },
}


def feedforward(client, info, gegevens: str) -> dict:
    systeem = [{"type": "text", "text": f"""Je schrijft de feedbackstrook bij een schoolexamen \
{info.vak} voor één leerling ({info.niveau}). De docent werkt formatief: de strook kijkt vooruit \
(feedforward) en noemt geen cijfer, geen totaalscore en geen percentages.

Schrijf in het Nederlands, in de je-vorm, op taalniveau {info.taalniveau} van het ERK. \
{'Gebruik korte zinnen en alledaagse woorden; leg een vakterm kort uit.' if info.niveau == 'mavo' else ''}\
{'Gebruik heldere zinnen; vaktermen mag je gebruiken.' if info.niveau == 'havo' else ''}\
{'Je mag abstracter formuleren en vaktermen zonder uitleg gebruiken.' if info.niveau == 'vwo' else ''}

Lever:
- sterk: 1 of 2 zinnen over wat deze leerling aantoonbaar goed deed (noem het onderdeel).
- leergaten: voor elke leergatcode die bij deze leerling voorkomt één regel in leerlingtaal: wat \
ging er bij jou mis en bij welke vragen. Gebruik de code alleen in het veld 'code', niet in de tekst.
- actieplan: precies 3 stappen, van belangrijkste naar minst belangrijke. 'stap' is wat de leerling \
gaat doen (begint met een werkwoord), 'hoe' is concreet: welk onderdeel, welke soort opgaven, \
hoe controleer je of het gelukt is. Geen algemeenheden als 'beter leren'.
- reflectievragen: 2 open vragen waarmee de leerling zelf terugkijkt op de voorbereiding en de \
aanpak tijdens de toets. Passend bij de fouten van deze leerling.

Verzin geen fouten die niet in de gegevens staan. Noem geen naam."""}]
    return kern.vraag_json(client, systeem, [{"type": "text", "text": gegevens}],
                           SCHEMA_FEEDFORWARD, max_tokens=16000)


# ---------------------------------------------------------------------------- klassenadvies
SCHEMA_KLASADVIES = {
    "type": "object", "additionalProperties": False,
    "required": ["samenvatting", "kennisbank", "groepen", "nabespreking"],
    "properties": {
        "samenvatting": {"type": "string"},
        "kennisbank": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["soort", "titel", "waarom", "vragen"],
            "properties": {"soort": {"type": "string", "enum": ["begrip", "rekenregel", "casus"]},
                           "titel": {"type": "string"}, "waarom": {"type": "string"},
                           "vragen": {"type": "string"}}}},
        "groepen": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["naam", "focus", "leerling_ids", "aanpak"],
            "properties": {"naam": {"type": "string"}, "focus": {"type": "string"},
                           "leerling_ids": {"type": "array", "items": {"type": "string"}},
                           "aanpak": {"type": "string"}}}},
        "nabespreking": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["onderwerp", "vragen", "aanpak"],
            "properties": {"onderwerp": {"type": "string"}, "vragen": {"type": "string"},
                           "aanpak": {"type": "string"}}}},
    },
}


def klasadvies(client, info, gegevens: str) -> dict:
    systeem = [{"type": "text", "text": f"""Je bent vakdidacticus {info.vak} en helpt een docent \
een schoolexamen ({info.niveau}, klas {info.klas}) na te bespreken. Je krijgt de toetsanalyse per \
vraag en het foutenprofiel per leerling. Leergatcodes: KD kennisdeficit, PR procedurele rekenfout, \
RF redeneerfout, LI leesfout, DW doorwerkfout, MC misconceptie.

Lever in het Nederlands:
- samenvatting: 3 of 4 zinnen met het belangrijkste beeld van deze klas bij deze toets.
- kennisbank: wat de docent aan zijn eigen kennisbank moet toevoegen vóór een volgende toets over \
deze stof, afgeleid uit de gemaakte fouten. Per advies: soort (begrip, rekenregel of casus), een \
korte titel, waarom (welke fout laat dit zien) en bij welke vragen het speelde. Alleen adviezen die \
door minstens twee leerlingen of een duidelijke misconceptie worden gedragen; maximaal 8.
- groepen: een groepsplan voor de nabespreking. Deel de leerlingen in 3 tot 5 groepen in op basis \
van hun overheersende leergat en de onderdelen waar ze punten verloren. Elke leerling staat in \
precies één groep; gebruik de leerlingnummers uit de gegevens. Leerlingen met weinig fouten vormen \
een verdiepingsgroep. Per groep: naam, focus (leergat en onderdeel) en een concrete aanpak voor \
ongeveer 20 minuten.
- nabespreking: maximaal 5 focuspunten voor de klassikale nabespreking, in volgorde van belang, \
met de vragen waar het om gaat en hoe je het aanpakt.

Baseer alles op de gegevens. Bij een kleine groep (minder dan 8 leerlingen) zeg je in de \
samenvatting dat de cijfers per vraag weinig zeggen."""}]
    return kern.vraag_json(client, systeem, [{"type": "text", "text": gegevens}],
                           SCHEMA_KLASADVIES, max_tokens=32000)
