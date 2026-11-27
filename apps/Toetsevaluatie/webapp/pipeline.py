# -*- coding: utf-8 -*-
"""Fase 2 (AI-verwerking) en de AI-stappen na publicatie. Draait op de achtergrond.

Stappen van de analyse:
  1. correctievoorschrift lezen en toetsmatrijs opstellen
  2. elke pagina van de klassenscan als afbeelding bewaren
  3. namen lezen en de scan per leerling opknippen (koppelen via de klassenlijst)
  4. per leerling nakijken: docentscore lezen, eigen score geven, leergat labelen
Elke stap bewaart zijn resultaat in de sessie, zodat 'Hervatten' na een fout verder gaat waar het
gebleven was. Er wordt hier niets in de database geschreven.
"""
from __future__ import annotations

import logging
import os
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor

import abonnement
import ai
import database
import rapport
import sessie
from config import BEELD_ZIJDE_PX, WERKERS, kern
from scores import begrens

log = logging.getLogger("webapp")
anthropic = kern.anthropic
_taken: dict[str, threading.Thread] = {}
# PyMuPDF kan niet veilig in meerdere threads tegelijk werken: PDF's worden één voor één gelezen.
PDF_SLOT = threading.Lock()


class Stop(Exception):
    """Fout met een boodschap die de docent kan lezen."""


def loopt(toets_id: str) -> bool:
    taak = _taken.get(toets_id)
    return taak is not None and taak.is_alive()


def _voortgang(toets_id: str, stap: int, tekst: str, gedaan: int = 0, totaal: int = 0) -> None:
    with sessie.bewerk(toets_id) as s:
        s["voortgang"] = {"stap": stap, "tekst": tekst, "gedaan": gedaan, "totaal": totaal}


def _stap_klaar(toets_id: str) -> None:
    with sessie.bewerk(toets_id) as s:
        s["voortgang"]["gedaan"] += 1


ABO_MELDING = ("Je Claude-abonnement kan nu niet verder (limiet bereikt of niet ingelogd). Er is geen "
               "API-tegoed gebruikt. Wat al klaar was, blijft bewaard.")


def _start(toets_id: str, werk) -> None:
    def veilig():
        s0 = sessie.lees(toets_id) or {}
        abonnement.zet(s0.get("api_toestemming"))
        toestemming = False
        try:
            werk(toets_id)
            with sessie.bewerk(toets_id) as s:
                s["api_toestemming"] = False
            return
        except Stop as fout:
            melding = str(fout)
        except abonnement.ToestemmingNodig as fout:
            toestemming = True
            melding = f"{ABO_MELDING} Melding: {str(fout)[:160]}"
        except anthropic.AuthenticationError:
            melding = ("De API-sleutel is geweigerd. Start de webapp opnieuw via "
                       "'Start Webapp.command' en plak een geldige sleutel.")
        except (anthropic.APIError, kern.ApiFout) as fout:
            melding = f"De Anthropic API gaf een fout: {fout}"
        except Exception as fout:  # noqa: BLE001
            log.exception("Onverwachte fout bij %s", toets_id)
            melding = f"Onverwachte fout: {type(fout).__name__}: {fout}"
        with sessie.bewerk(toets_id) as s:
            s["status"], s["fout"] = "fout", melding
            s["api_toestemming"] = False
            s["toestemming_nodig"] = {"stap": "analyse"} if toestemming else None

    with sessie.bewerk(toets_id) as s:
        s["toestemming_nodig"] = None
        if not s.get("api_toestemming"):
            abonnement.weer_proberen()          # zelf op een knop geklikt: eerst weer het abonnement proberen
    taak = threading.Thread(target=veilig, daemon=True, name=f"taak-{toets_id}")
    _taken[toets_id] = taak
    taak.start()


# ---------------------------------------------------------------------------- analyse
def start_analyse(toets_id: str) -> None:
    with sessie.bewerk(toets_id) as s:
        s["status"], s["fout"] = "bezig", ""
    _start(toets_id, _analyse)


def _analyse(toets_id: str) -> None:
    info = kern.lees_toetsmap(toets_id)
    smap = sessie.map_van(toets_id)
    client = ai.maak_client()
    s = sessie.lees(toets_id)

    # 0. klassenlijst (lokaal, geen API)
    if not s["klassenlijst"]:
        lijst, precies = kern.laad_klassenlijst(smap, info)
        if not lijst:
            raise Stop("Geen klassenlijst gevonden. Upload een klassenlijst of zet er een in de "
                       "map Klassenlijsten.")
        with sessie.bewerk(toets_id) as s2:
            s2["klassenlijst"] = {lid: {"naam": l.naam, "klas": l.klas, "groep": l.groep}
                                  for lid, l in lijst.items()}
            s2["lijst_precies"] = precies

    # 1. toetsmatrijs
    cv_pad = smap / "cv.txt"
    if not s["matrijs"]:
        _voortgang(toets_id, 1, "Correctievoorschrift lezen en toetsmatrijs opstellen")
        with PDF_SLOT:
            cv_tekst = kern.lees_document(smap / s["cv_bestand"])
        if len(cv_tekst) < 300:
            raise Stop("Het correctievoorschrift bevat nauwelijks leesbare tekst. Is het een "
                       "gescande PDF? Lever het dan als .docx aan.")
        cv_pad.write_text(cv_tekst, encoding="utf-8")
        matrijs = kern.haal_toetsmatrijs(client, info, cv_tekst)
        try:
            extra = ai.leerdoelen(client, info, cv_tekst, [m["vraagnummer"] for m in matrijs])
            for m in matrijs:
                m.update(extra.get(m["vraagnummer"], {}))
        except abonnement.ToestemmingNodig:
            raise
        except Exception as fout:  # noqa: BLE001  (leerdoelen zijn een aanvulling)
            log.warning("Leerdoelen ophalen mislukt: %s", fout)
        try:                                   # oefentoets uit de oefentool: codes staan in het cv
            import register
            codes = register.codes_uit_cv(cv_tekst)
            for m in matrijs:
                if m["vraagnummer"] in codes:
                    m["leerdoel_code"] = codes[m["vraagnummer"]]
        except Exception as fout:  # noqa: BLE001
            log.warning("Leerdoelcodes uit het correctievoorschrift lezen mislukt: %s", fout)
        with sessie.bewerk(toets_id) as s2:
            s2["matrijs"] = matrijs

    # 2. pagina's als afbeelding
    _bewaar_paginas(toets_id)

    # 3. namen lezen en opknippen
    _lees_namen(toets_id, client)
    _groepeer(toets_id)

    # 4. nakijken
    _kijk_alle_na(toets_id, client, info)
    with sessie.bewerk(toets_id) as s2:
        s2["status"] = "review"
        s2["voortgang"] = {"stap": 5, "tekst": "Klaar voor validatie", "gedaan": 0, "totaal": 0}


def _bewaar_paginas(toets_id: str) -> None:
    smap = sessie.map_van(toets_id)
    doel = smap / "paginas"
    doel.mkdir(exist_ok=True)
    s = sessie.lees(toets_id)
    with PDF_SLOT:
        with kern.fitz.open(str(smap / s["scan_bestand"])) as pdf:
            aantal = pdf.page_count
            if aantal == 0:
                raise Stop("De klassenscan bevat geen pagina's.")
            _voortgang(toets_id, 2, "Pagina's van de scan voorbereiden", 0, aantal)
            maten = []
            for i in range(aantal):
                rect = pdf.load_page(i).rect
                maten.append([round(rect.width, 1), round(rect.height, 1)])
                pad = doel / f"{i:04d}.jpg"
                if not pad.exists():
                    pad.write_bytes(kern._render(pdf, i, BEELD_ZIJDE_PX, 72))
                _stap_klaar(toets_id)
    with sessie.bewerk(toets_id) as s2:
        s2["aantal_paginas"], s2["maten"] = aantal, maten


def _lees_namen(toets_id: str, client, van: int = 0, tot: int | None = None) -> None:
    """Leest de namen op pagina van..tot-1 (standaard de hele scan)."""
    s = sessie.lees(toets_id)
    smap = sessie.map_van(toets_id)
    aantal = s["aantal_paginas"] if tot is None else tot
    lijst = {lid: kern.Leerling(lid, l["naam"], l["klas"]) for lid, l in s["klassenlijst"].items()}
    systeem = kern._index_systeem(lijst)
    starts = [st for st in range(van, aantal, kern.INDEX_BATCH) if str(st) not in s["index_cache"]]
    _voortgang(toets_id, 3, "Namen lezen en de scan per leerling opknippen",
               aantal - van - sum(min(kern.INDEX_BATCH, aantal - st) for st in starts), aantal - van)

    def beeld(i: int) -> dict:
        return kern._beeldblok((smap / "paginas" / f"{i:04d}.jpg").read_bytes())

    def reeks(start: int) -> None:
        eind = min(start + kern.INDEX_BATCH, aantal)
        inhoud: list[dict] = []
        if start > van:                              # bij bijgescande pagina's geen vergelijking
            inhoud.append({"type": "text", "text":
                           f"Ter vergelijking van het handschrift: pagina {start} (de pagina vóór "
                           "deze reeks). Neem deze pagina NIET op in je antwoord."})
            inhoud.append(beeld(start - 1))
        for i in range(start, eind):
            inhoud.append({"type": "text", "text": f"Pagina {i + 1}:"})
            inhoud.append(beeld(i))
        inhoud.append({"type": "text",
                       "text": f"Geef de gegevens voor pagina {start + 1} tot en met {eind}."})
        uit = kern.vraag_json(client, systeem, inhoud, kern.SCHEMA_INDEX, max_tokens=8000)
        per = {int(r["pagina"]): r for r in uit.get("paginas", [])}
        rijen = [per.get(i + 1) or {"pagina": i + 1, "leeg": False, "naam_gelezen": "",
                                    "leerling_id": "", "begin_nieuw_werk": False,
                                    "zekerheid": "laag", "ontbreekt": True}
                 for i in range(start, eind)]
        with sessie.bewerk(toets_id) as s2:
            s2["index_cache"][str(start)] = rijen
            s2["voortgang"]["gedaan"] += eind - start

    with ThreadPoolExecutor(WERKERS) as pool:
        list(pool.map(abonnement.met_context(reeks), starts))


def _nieuwe_leerling(naam: str, klas: str, paginas: list[int], gelezen: str = "",
                     vlag: str = "") -> dict:
    return {"naam": naam, "klas": klas, "paginas": paginas, "status": "wacht", "fout": "",
            "vragen": {}, "reflectie": "", "tips": [], "opmerking": "",
            "koppeling": {"gelezen": gelezen, "vlag": vlag, "ok": False}}


def _verdeel(rijen: list[tuple[int, dict]], lijst: dict, voorvoegsel: str = "wees_"):
    """Verdeelt (paginanummer, indexrij) over leerlingen. Geeft (groepen, gelezen namen, lage zekerheid)."""
    groepen: dict[str, list[int]] = {}
    gelezen: dict[str, str] = {}
    laag: set[str] = set()
    huidig, zonder_naam = None, 0
    for i, p in rijen:
        if p["leeg"]:
            continue
        lid, naam = p["leerling_id"].strip(), p["naam_gelezen"].strip()
        if lid not in lijst:
            lid = ""
        if lid:
            huidig = lid
            if naam:
                gelezen.setdefault(lid, naam)
            if p["zekerheid"] == "laag":
                laag.add(lid)
        elif naam:
            huidig = voorvoegsel + ("-".join(kern._woorden(naam)) or "naam")[:40]
            gelezen[huidig] = naam
        elif p["begin_nieuw_werk"] or huidig is None:
            zonder_naam += 1
            huidig = f"{voorvoegsel}zonder-naam-{zonder_naam}"
        groepen.setdefault(huidig, []).append(i)
    return groepen, gelezen, laag


def _groepeer(toets_id: str) -> None:
    """Verdeelt de pagina's over leerlingen; wat niet te koppelen is wordt een wees."""
    with sessie.bewerk(toets_id) as s:
        if s.get("gegroepeerd"):
            return
        lijst = s["klassenlijst"]
        paginas = [r for st in sorted(s["index_cache"], key=int) for r in s["index_cache"][st]]
        s["paginas"] = paginas

        groepen, gelezen, laag = _verdeel(list(enumerate(paginas)), lijst)

        aantallen = [len(p) for k, p in groepen.items() if k in lijst]
        mediaan = statistics.median(aantallen) if len(aantallen) >= 5 else None
        s["leerlingen"], s["wezen"] = {}, []
        for sleutel, pags in groepen.items():
            if sleutel not in lijst:
                s["wezen"].append({"id": sleutel, "paginas": pags, "status": "open",
                                   "naam_gelezen": gelezen.get(sleutel, ""), "gekoppeld_aan": ""})
                continue
            vlaggen = []
            naam = gelezen.get(sleutel, "")
            if naam and not kern._naam_past(naam, kern.Leerling(sleutel, lijst[sleutel]["naam"])):
                vlaggen.append(f"gelezen als '{naam}'")
            elif sleutel in laag:
                vlaggen.append("naam met lage zekerheid gelezen")
            if "," in kern._reeksen(pags):
                vlaggen.append("pagina's staan niet achter elkaar in de scan")
            if mediaan and len(pags) >= 1.6 * mediaan and len(pags) - mediaan >= 3:
                vlaggen.append(f"{len(pags)} pagina's (klas meestal {mediaan:g}): werk van twee "
                               "leerlingen?")
            elif mediaan and len(pags) <= 0.5 * mediaan:
                vlaggen.append(f"maar {len(pags)} pagina('s) (klas meestal {mediaan:g})")
            s["leerlingen"][sleutel] = _nieuwe_leerling(
                lijst[sleutel]["naam"], lijst[sleutel]["klas"], pags, naam, "; ".join(vlaggen))
        s["afwezig"] = ([lid for lid in lijst if lid not in s["leerlingen"]]
                        if s["lijst_precies"] else [])
        s["lege_paginas"] = sum(1 for p in paginas if p["leeg"])
        s["gegroepeerd"] = True
        if not s["leerlingen"] and not s["wezen"]:
            raise Stop("In de scan is geen leerlingwerk gevonden (alle pagina's lijken leeg).")


# ---------------------------------------------------------------------------- nakijken
def _beelden(pdf_pad, paginas: list[int]) -> list[bytes]:
    if len(paginas) > kern.MAX_PAGINAS:
        raise Stop(f"{len(paginas)} pagina's voor één leerling (maximum {kern.MAX_PAGINAS}). "
                   "Waarschijnlijk zit hier werk van meer leerlingen bij; koppel opnieuw.")
    with PDF_SLOT, kern.fitz.open(str(pdf_pad)) as pdf:
        for kwaliteit in (85, 70, 55, 40):
            beelden = [kern._render(pdf, p, kern.MAX_ZIJDE_PX, kwaliteit) for p in paginas]
            if sum(len(b) for b in beelden) <= kern.MAX_AFBEELDING_BYTES:
                return beelden
    raise Stop("De scan van deze leerling is te groot voor één verzoek.")


def _verwerk(matrijs: list[dict], uit: dict, paginas: list[int]) -> dict:
    """Zet het antwoord van de AI om naar één record per vraag uit de toetsmatrijs."""
    per: dict[int, dict] = {}
    for a in uit.get("vragen", []):
        per.setdefault(int(a["vraagnummer"]), a)
    vragen = {}
    for m in matrijs:
        nr, mx = m["vraagnummer"], m["max_score"]
        basis = {"nr": nr, "max": mx, "domein": m["domein_eindterm"], "bloom": m["bloom_niveau"],
                 "label": m["tweefasen_label"], "besluit": None, "definitief": None,
                 "def_leergat": None, "def_omschrijving": None}
        a = per.get(nr)
        if a is None:
            vragen[str(nr)] = dict(basis, ontbreekt=True, antwoord="", docent=None, ai=0.0,
                                   leergat=None, omschrijving="", zekerheid="laag",
                                   toelichting="De AI heeft deze vraag niet beoordeeld.",
                                   niet_gemaakt=False, pagina=None, y0=0.0, y1=1.0)
            continue
        zekerheid, toelichting = a["zekerheid"], a["toelichting"].strip()
        docent = a["score_docent"]
        if docent is not None:
            docent = float(docent)
            if docent < 0 or docent > mx:
                zekerheid = "laag"
                toelichting = (f"Gelezen docentscore {docent:g} valt buiten 0-{mx}. "
                               + toelichting).strip()
                docent = begrens(docent, mx)
        pag = a["pagina"]
        globaal = paginas[pag - 1] if 1 <= pag <= len(paginas) else None
        y0 = min(max(a["y_boven"], 0), 100) / 100
        y1 = min(max(a["y_onder"], 0), 100) / 100
        if y1 - y0 < 0.10:                               # te smal of ongeldig: ruimer nemen
            midden = (y0 + y1) / 2 if y1 > y0 else y0
            y0, y1 = midden - 0.09, midden + 0.09
        y0, y1 = max(0.0, y0 - 0.03), min(1.0, y1 + 0.03)
        if y1 <= y0:
            y0, y1 = 0.0, 1.0
        leergat = a["type_leergat"] if a["type_leergat"] in kern.LEERGATEN else None
        vragen[str(nr)] = dict(
            basis, ontbreekt=False, antwoord=a["antwoord_leerling"].strip(), docent=docent,
            ai=begrens(a["score_ai"], mx), leergat=leergat,
            omschrijving=a["foutomschrijving"].strip(), zekerheid=zekerheid,
            toelichting=toelichting, niet_gemaakt=bool(a["niet_gemaakt"]),
            pagina=globaal, y0=round(y0, 3), y1=round(y1, 3))
    return vragen


def _kijk_leerling(toets_id: str, lid: str, client, systeem: list[dict]) -> None:
    with sessie.bewerk(toets_id) as s:
        if lid not in s["leerlingen"]:
            return
        s["leerlingen"][lid]["status"] = "bezig"
        paginas = list(s["leerlingen"][lid]["paginas"])
        matrijs = s["matrijs"]
        pdf_pad = sessie.map_van(toets_id) / s["scan_bestand"]
    fout = ""
    try:
        uit = ai.kijk_na(client, systeem, _beelden(pdf_pad, paginas))
        vragen = _verwerk(matrijs, uit, paginas)
    except (anthropic.AuthenticationError, abonnement.ToestemmingNodig):
        with sessie.bewerk(toets_id) as s:
            s["leerlingen"][lid]["status"] = "wacht"
        raise
    except Exception as f:  # noqa: BLE001  (één leerling mag de rest niet tegenhouden)
        log.warning("Nakijken van %s mislukt: %s", lid, f)
        fout = str(f) or type(f).__name__
    with sessie.bewerk(toets_id) as s:
        l = s["leerlingen"].get(lid)
        if l is not None:
            if fout:
                l["status"], l["fout"] = "fout", fout
            else:
                for k, v in vragen.items():           # eerdere keuzes van de docent bewaren
                    o = (l.get("vragen") or {}).get(k)
                    if o and o.get("besluit") and o.get("docent") == v["docent"] \
                            and abs((o.get("ai") or 0) - v["ai"]) < 0.01:
                        for veld in ("besluit", "definitief", "def_leergat", "def_omschrijving"):
                            v[veld] = o.get(veld)
                l.update(status="klaar", fout="", vragen=vragen,
                         reflectie=uit.get("reflectie", "").strip(),
                         tips=[t.strip() for t in uit.get("tips", []) if t.strip()],
                         opmerking=uit.get("opmerking_docent", "").strip())
        s["voortgang"]["gedaan"] += 1


def _systeem(toets_id: str, info) -> list[dict]:
    s = sessie.lees(toets_id)
    cv_tekst = (sessie.map_van(toets_id) / "cv.txt").read_text(encoding="utf-8")
    return ai.systeem_nakijken(info, cv_tekst, s["matrijs"], sessie.laad_leerregels())


def _kijk_alle_na(toets_id: str, client, info) -> None:
    s = sessie.lees(toets_id)
    te_doen = [lid for lid, l in s["leerlingen"].items() if l["status"] != "klaar"]
    _voortgang(toets_id, 4, "Leerlingwerk nakijken", 0, len(te_doen))
    systeem = _systeem(toets_id, info)
    with ThreadPoolExecutor(WERKERS) as pool:
        list(pool.map(abonnement.met_context(lambda lid: _kijk_leerling(toets_id, lid, client, systeem)), te_doen))
    s = sessie.lees(toets_id)
    mislukt = [l for l in s["leerlingen"].values() if l["status"] == "fout"]
    if s["leerlingen"] and len(mislukt) == len(s["leerlingen"]):
        raise Stop("Geen enkele leerling kon worden nagekeken. Eerste fout: " + mislukt[0]["fout"])


def start_leerling(toets_id: str, lid: str) -> None:
    """Kijkt één leerling (opnieuw) na, bijvoorbeeld nadat een wees-pagina is gekoppeld."""
    with sessie.bewerk(toets_id) as s:
        s["leerlingen"][lid].update(status="wacht", fout="")
        ok = bool(s.get("api_toestemming"))
        s["toestemming_nodig"] = None
    if not ok:
        abonnement.weer_proberen()

    def werk():
        abonnement.zet(ok)
        try:
            info = kern.lees_toetsmap(toets_id)
            _kijk_leerling(toets_id, lid, ai.maak_client(), _systeem(toets_id, info))
            if ok:
                with sessie.bewerk(toets_id) as s:
                    s["api_toestemming"] = False
        except abonnement.ToestemmingNodig as f:
            with sessie.bewerk(toets_id) as s:
                if lid in s["leerlingen"]:
                    s["leerlingen"][lid].update(status="fout", fout=f"{ABO_MELDING} {str(f)[:120]}")
                s["toestemming_nodig"] = {"stap": "leerling", "lid": lid}
                s["api_toestemming"] = False
        except Exception as f:  # noqa: BLE001
            log.exception("Los nakijken van %s mislukt", lid)
            with sessie.bewerk(toets_id) as s:
                if lid in s["leerlingen"]:
                    s["leerlingen"][lid].update(status="fout", fout=str(f) or type(f).__name__)

    threading.Thread(target=werk, daemon=True, name=f"leerling-{lid}").start()


def herkijk_mislukt(toets_id: str) -> int:
    """Kijkt alle leerlingen opnieuw na die nog niet (goed) zijn nagekeken, bijvoorbeeld omdat het tegoed op was."""
    with sessie.bewerk(toets_id) as s:
        lids = [lid for lid, l in s["leerlingen"].items() if l["status"] in ("fout", "wacht")]
        for lid in lids:
            s["leerlingen"][lid].update(status="wacht", fout="")
        ok = bool(s.get("api_toestemming"))
        s["toestemming_nodig"] = None
        s["voortgang"] = dict(s.get("voortgang") or {}, gedaan=0, totaal=len(lids))
    if not lids:
        return 0
    if not ok:
        abonnement.weer_proberen()

    def werk():
        abonnement.zet(ok)
        try:
            info = kern.lees_toetsmap(toets_id)
            client, systeem = ai.maak_client(), _systeem(toets_id, info)
            with ThreadPoolExecutor(WERKERS) as pool:
                list(pool.map(abonnement.met_context(lambda lid: _kijk_leerling(toets_id, lid, client, systeem)), lids))
            if ok:
                with sessie.bewerk(toets_id) as s:
                    s["api_toestemming"] = False
        except Exception as f:  # noqa: BLE001
            abo = isinstance(f, abonnement.ToestemmingNodig)
            if not abo:
                log.exception("Opnieuw nakijken mislukt")
            with sessie.bewerk(toets_id) as s:
                for lid in lids:
                    l = s["leerlingen"].get(lid)
                    if l and l["status"] in ("wacht", "bezig"):
                        l.update(status="fout", fout=ABO_MELDING if abo else (str(f) or type(f).__name__))
                if abo:
                    s["toestemming_nodig"] = {"stap": "herkijk"}
                s["api_toestemming"] = False

    threading.Thread(target=werk, daemon=True, name=f"herkijk-{toets_id}").start()
    return len(lids)


# ---------------------------------------------------------------------------- pagina's toevoegen
def voeg_paginas_toe(s: dict, paginas: list[int], lid: str) -> None:
    """Voegt pagina's toe aan een leerling (binnen sessie.bewerk). De leerling wordt daarna opnieuw nagekeken."""
    l = s["leerlingen"].get(lid)
    if l:
        l["paginas"] = sorted(set(l["paginas"]) | set(paginas))
    else:
        k = s["klassenlijst"][lid]
        s["leerlingen"][lid] = _nieuwe_leerling(k["naam"], k["klas"], sorted(paginas))
        s["leerlingen"][lid]["koppeling"]["ok"] = True
        if lid in s.get("afwezig", []):
            s["afwezig"].remove(lid)
    s["feedforward"].pop(lid, None)


def start_aanvulling(toets_id: str, extra_pdf) -> int:
    """Plakt een bijgescande PDF achter de klassenscan en verwerkt alleen de nieuwe pagina's."""
    smap = sessie.map_van(toets_id)
    s = sessie.lees(toets_id)
    scan = smap / s["scan_bestand"]
    with PDF_SLOT:
        with kern.fitz.open(str(extra_pdf)) as extra:
            nieuw = extra.page_count
            if nieuw == 0:
                raise Stop("De bijgescande PDF bevat geen pagina's.")
            if nieuw > 200:
                raise Stop("Dat zijn meer dan 200 pagina's. Start voor een nieuwe klas een nieuwe analyse.")
            with kern.fitz.open(str(scan)) as hoofd:
                van = hoofd.page_count
                hoofd.insert_pdf(extra)
                tijdelijk = smap / "scan_nieuw.pdf"
                hoofd.save(str(tijdelijk))
        os.replace(tijdelijk, scan)
    with sessie.bewerk(toets_id) as s2:
        s2["aanvulling"] = {"van": van, "tot": van + nieuw, "gegroepeerd": False,
                            "status_voor": s2["status"]}
        s2["status"], s2["fout"] = "bezig", ""
    _start(toets_id, _aanvul)
    return nieuw


def hervat_aanvulling(toets_id: str) -> None:
    with sessie.bewerk(toets_id) as s:
        s["status"], s["fout"] = "bezig", ""
    _start(toets_id, _aanvul)


def _aanvul(toets_id: str) -> None:
    info = kern.lees_toetsmap(toets_id)
    client = ai.maak_client()
    a = sessie.lees(toets_id)["aanvulling"]
    _bewaar_paginas(toets_id)
    _lees_namen(toets_id, client, a["van"], a["tot"])
    _groepeer_aanvulling(toets_id)
    _kijk_alle_na(toets_id, client, info)
    with sessie.bewerk(toets_id) as s:
        a = s.pop("aanvulling")
        if a["status_voor"] in ("gepubliceerd", "publiceren"):
            s["was_gepubliceerd"] = True
        s["status"] = "review"
        s["voortgang"] = {"stap": 5, "tekst": "Klaar voor validatie", "gedaan": 0, "totaal": 0}


def _groepeer_aanvulling(toets_id: str) -> None:
    with sessie.bewerk(toets_id) as s:
        a = s["aanvulling"]
        if a["gegroepeerd"]:
            return
        van, tot = a["van"], a["tot"]
        rijen = [r for st in sorted(s["index_cache"], key=int) if van <= int(st) < tot
                 for r in s["index_cache"][st]]
        paginas = list(range(van, tot))
        s["paginas"] = s["paginas"][:van] + rijen
        groepen, gelezen, _ = _verdeel(list(zip(paginas, rijen)), s["klassenlijst"],
                                       f"wees_bij{van}_")
        for sleutel, pags in groepen.items():
            if sleutel not in s["klassenlijst"]:
                s["wezen"].append({"id": sleutel, "paginas": pags, "status": "open",
                                   "naam_gelezen": gelezen.get(sleutel, ""), "gekoppeld_aan": ""})
                continue
            bestond = sleutel in s["leerlingen"]
            voeg_paginas_toe(s, pags, sleutel)
            l = s["leerlingen"][sleutel]
            l["status"] = "wacht"
            l["koppeling"].update(ok=False, vlag=(
                f"pagina {kern._reeksen(pags)} bijgescand" + (", bij eerder werk gevoegd" if bestond else "")))
        s["lege_paginas"] = sum(1 for p in s["paginas"] if p["leeg"])
        a["gegroepeerd"] = True


# ---------------------------------------------------------------------------- publicatie
def start_publicatie(toets_id: str) -> int:
    """Schrijft de scores naar de database (direct) en start daarna de AI-rapportage."""
    aantal = database.publiceer(sessie.lees(toets_id))
    with sessie.bewerk(toets_id) as s:
        s["status"], s["fout"], s["melding"] = "publiceren", "", ""
        s.pop("was_gepubliceerd", None)
        s["voortgang"] = {"stap": 5, "tekst": "Feedbackstroken schrijven", "gedaan": 0,
                          "totaal": aantal + 1}
    _start(toets_id, _na_publicatie)
    return aantal


def _na_publicatie(toets_id: str) -> None:
    meldingen = []
    try:
        info = kern.lees_toetsmap(toets_id)
        client = ai.maak_client()
        s = sessie.lees(toets_id)
        klaar = [lid for lid, l in s["leerlingen"].items() if l["status"] == "klaar"]
        mislukt = []
        abo_op = []

        def strook(lid: str) -> None:
            try:
                uit = ai.feedforward(client, info, rapport.feedforward_gegevens(toets_id, lid))
                with sessie.bewerk(toets_id) as s2:
                    s2["feedforward"][lid] = uit
            except abonnement.ToestemmingNodig:
                abo_op.append(lid)
                mislukt.append(lid)
            except Exception as f:  # noqa: BLE001
                log.warning("Feedforward voor %s mislukt: %s", lid, f)
                mislukt.append(lid)
            _stap_klaar(toets_id)

        with ThreadPoolExecutor(max(WERKERS, 3)) as pool:
            list(pool.map(abonnement.met_context(strook), klaar))
        if abo_op:
            with sessie.bewerk(toets_id) as s2:
                s2["toestemming_nodig"] = {"stap": "publicatie"}
            raise abonnement.ToestemmingNodig(
                f"Bij {len(abo_op)} leerling(en) is de feedbackstrook niet gemaakt omdat je abonnement niet verder kon. "
                "De scores staan wel in de database.")
        if mislukt:
            meldingen.append(f"Bij {len(mislukt)} leerling(en) is het persoonlijke actieplan niet "
                             "gelukt; daar staat de standaardtekst.")
        with sessie.bewerk(toets_id) as s2:
            s2["voortgang"]["tekst"] = "Leerdoelen koppelen aan het register"
        try:
            import register
            register.bijwerken_voor_toets(toets_id, client)
        except Exception as f:  # noqa: BLE001
            log.warning("Leerdoelenregister bijwerken mislukt: %s", f)
            meldingen.append("De leerdoelen zijn niet aan het register gekoppeld; doe dat op de "
                             "pagina Leerdoelen.")
        with sessie.bewerk(toets_id) as s2:
            s2["voortgang"]["tekst"] = "Klassenadvies schrijven"
        try:
            maak_klasadvies(toets_id, client)
        except Exception as f:  # noqa: BLE001
            log.warning("Klassenadvies mislukt: %s", f)
            meldingen.append("Het klassenadvies (kennisbank en groepsplan) is niet gelukt; maak "
                             "het opnieuw op de klassenrapportage.")
    except abonnement.ToestemmingNodig as f:
        meldingen.append(f"{f} Kies hieronder: opnieuw via je abonnement, of verder met API-tegoed.")
    except Exception as f:  # noqa: BLE001
        log.exception("Rapportage na publicatie mislukt")
        meldingen.append(f"De AI-rapportage is niet afgemaakt ({f}). De scores staan wel in de "
                         "database.")
    with sessie.bewerk(toets_id) as s:
        s["status"] = "gepubliceerd"
        s["melding"] = " ".join(meldingen)
        s["voortgang"] = {"stap": 5, "tekst": "Gepubliceerd", "gedaan": 0, "totaal": 0}


def maak_klasadvies(toets_id: str, client=None) -> None:
    info = kern.lees_toetsmap(toets_id)
    uit = ai.klasadvies(client or ai.maak_client(), info, rapport.klas_gegevens(toets_id))
    s = sessie.lees(toets_id)
    bekend = [lid for lid, l in s["leerlingen"].items() if l["status"] == "klaar"]
    gezien: set[str] = set()
    for g in uit["groepen"]:                          # elke leerling precies één keer
        g["leerling_ids"] = [lid for lid in g["leerling_ids"]
                             if lid in bekend and lid not in gezien and not gezien.add(lid)]
    uit["groepen"] = [g for g in uit["groepen"] if g["leerling_ids"]]
    rest = [lid for lid in bekend if lid not in gezien]
    if rest:
        uit["groepen"].append({"naam": "Nog in te delen", "focus": "Door de AI niet ingedeeld",
                               "leerling_ids": rest, "aanpak": "Deel deze leerlingen zelf in."})
    with sessie.bewerk(toets_id) as s2:
        from datetime import datetime
        s2["klasadvies"] = dict(uit, gemaakt=datetime.now().strftime("%d-%m-%Y %H:%M"))


def vul_leerdoelen(toets_id: str) -> int:
    """Haalt leerdoelen en onderwerpen op voor een toets die al geanalyseerd is."""
    s = sessie.lees(toets_id)
    cv_pad = sessie.map_van(toets_id) / "cv.txt"
    if not s or not cv_pad.exists():
        raise Stop("Het correctievoorschrift van deze toets is niet bewaard in de app.")
    info = kern.lees_toetsmap(toets_id)
    extra = ai.leerdoelen(ai.maak_client(), info, cv_pad.read_text(encoding="utf-8"),
                          [m["vraagnummer"] for m in s["matrijs"]])
    with sessie.bewerk(toets_id) as s2:
        for m in s2["matrijs"]:
            m.update(extra.get(m["vraagnummer"], {}))
        matrijs = s2["matrijs"]
    if database.toets(toets_id):
        database.zet_leerdoelen(toets_id, matrijs)
    return len(extra)


def herstel_na_start() -> None:
    """Na een herstart van de app: taken die liepen staan stil. Zet ze op 'onderbroken'."""
    for kort in sessie.alle():
        with sessie.bewerk(kort["toets_id"]) as s:
            if s["status"] == "bezig":
                s["status"] = "fout"
                s["fout"] = "De analyse is onderbroken doordat de app is gestopt."
            elif s["status"] == "publiceren":
                s["status"] = "gepubliceerd"
                s["melding"] = ("De AI-rapportage is onderbroken. De scores staan in de "
                                "database; maak de stroken opnieuw via 'Definitief maken'.")
            for l in s["leerlingen"].values():
                if l["status"] == "bezig":
                    l["status"] = "wacht"
