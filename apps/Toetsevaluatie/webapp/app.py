# -*- coding: utf-8 -*-
"""Toetsevaluatie - lokale webapp (Flask).

Starten:  dubbelklik op 'Start Webapp.command'   (of: python3 webapp/app.py)
Daarna:   http://127.0.0.1:5055

Fasen:  /                    1  uploaden en de AI-analyse starten
        /voortgang/<toets>   2  AI-verwerking (nog niets in de database)
        /review              3  validatie door de docent
        /publiceer           4  definitief maken -> database + feedbackstroken (/feedback/<id>)
        /klassenrapportage   5  toetsanalyse en lesplan
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import date, datetime

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)

import abonnement
import ai
import analyse
import config
import leerprofiel
import register
import database
import pipeline
import rapport
import scores
import sessie
import versie
from config import kern

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024       # klassenscans kunnen groot zijn
log = logging.getLogger("webapp")


# ---------------------------------------------------------------------------- hulp
@app.template_filter("getal")
def getal(waarde, decimalen=None) -> str:
    """12.5 -> '12,5'; 3.0 -> '3'; None -> '-'."""
    if waarde is None or waarde == "":
        return "-"
    tekst = f"{waarde:.{decimalen}f}" if decimalen is not None else f"{waarde:g}"
    return tekst.replace(".", ",")


@app.template_filter("reeksen")
def reeksen(paginas) -> str:
    return kern._reeksen(list(paginas))


@app.context_processor
def algemeen():
    return {"heeft_sleutel": ai.kan_werken(), "api_sleutel": ai.heeft_sleutel(), "model": kern.MODEL,
            "motor": abonnement.motor(), "verouderd": versie.verouderd(),
            "leergatnamen": {c: t.split(":")[0] for c, t in kern.LEERGATEN.items()}}


@app.get("/api/versie")
def api_versie():
    return jsonify(versie=versie.GESTART)


# ---------------------------------------------------------------------------- abonnement of API-tegoed
ABO_LOS = ("Je Claude-abonnement kan nu niet verder (limiet bereikt of niet ingelogd). Er is geen API-tegoed "
           "gebruikt. Herlaad de pagina: daar kun je kiezen om het later opnieuw te proberen of nu API-tegoed te gebruiken.")
@app.post("/claude-instellingen")
def claude_instellingen():
    abonnement.bewaar_instellingen(request.form.get("motor", ""), request.form.get("abonnement_model", ""))
    flash("Opgeslagen.")
    return redirect(url_for("index") + "#claude")


@app.post("/toestemming")
def toestemming():
    """De docent kiest na een volle limiet: opnieuw via het abonnement, of deze ene stap met API-tegoed."""
    toets_id = request.form.get("toets", "")
    s = sessie.lees(toets_id)
    if not s:
        abort(404)
    api = request.form.get("keuze") == "api"
    nodig = s.get("toestemming_nodig") or {}
    with sessie.bewerk(toets_id) as s2:
        s2["api_toestemming"] = api
        s2["toestemming_nodig"] = None
    if not api:
        abonnement.weer_proberen()
    stap = nodig.get("stap", "")
    if pipeline.loopt(toets_id):
        flash("Er loopt al een stap voor deze toets. Wacht tot die klaar is.")
    elif stap == "analyse" and s["status"] == "fout":
        if s.get("aanvulling"):
            pipeline.hervat_aanvulling(toets_id)
        else:
            pipeline.start_analyse(toets_id)
        return redirect(url_for("voortgang", toets_id=toets_id))
    elif stap == "leerling" and nodig.get("lid") in s["leerlingen"]:
        pipeline.start_leerling(toets_id, nodig["lid"])
    elif stap == "herkijk":
        pipeline.herkijk_mislukt(toets_id)
    elif stap == "publicatie":
        pipeline.start_publicatie(toets_id)
        return redirect(url_for("voortgang", toets_id=toets_id))
    else:
        flash("Klik nu opnieuw op de knop die net niet lukte." + (" Dat gaat dan met API-tegoed." if api else ""))
    terug = request.form.get("terug", "")
    return redirect(terug if terug.startswith("/") else url_for("review", toets=toets_id))


@app.post("/herkijken")
def herkijken():
    toets_id = request.form.get("toets", "")
    if not sessie.lees(toets_id):
        abort(404)
    n = pipeline.herkijk_mislukt(toets_id)
    flash(f"{n} leerling(en) worden opnieuw nagekeken." if n else "Er zijn geen leerlingen om opnieuw na te kijken.")
    return redirect(url_for("review", toets=toets_id))


def _los(toets_id: str | None, werk):
    """Een AI-stap die direct in het verzoek draait (klassenadvies, leerdoelen): met de toestemming van de toets."""
    s = sessie.lees(toets_id) if toets_id else None
    abonnement.zet(bool(s and s.get("api_toestemming")))
    try:
        return werk()
    except abonnement.ToestemmingNodig:
        if s:
            with sessie.bewerk(toets_id) as s2:
                s2["toestemming_nodig"] = {"stap": "los"}
                s2["api_toestemming"] = False
        raise
    finally:
        if s and s.get("api_toestemming"):
            with sessie.bewerk(toets_id) as s2:
                s2["api_toestemming"] = False


def _toets_uit_verzoek(statussen=None) -> str | None:
    """De toets uit ?toets=..., of anders de meest recente sessie (met een van de statussen)."""
    toets_id = request.args.get("toets") or request.form.get("toets")
    if toets_id:
        return toets_id if sessie.geldig_id(toets_id) else None
    for kort in sessie.alle():
        if statussen is None or kort["status"] in statussen:
            return kort["toets_id"]
    return None


def _json_sessie():
    data = request.get_json(silent=True) or {}
    toets_id = data.get("toets", "")
    if not sessie.lees(toets_id):
        abort(404)
    return toets_id, data


def _fout(tekst: str, code: int = 400):
    return jsonify(ok=False, fout=tekst), code


# ---------------------------------------------------------------------------- leerling toevoegen
# Een leerling die (nog) niet in de klassenlijst staat, bijvoorbeeld na een klaswissel. Hij komt in de klassenlijst
# van deze toets én in Klassenlijsten/Klassenlijsten extra.xlsx, zodat hij bij volgende toetsen herkend wordt.
EXTRA_LIJST = kern.KLASSENLIJST_MAP / "Klassenlijsten extra.xlsx"


def _schoon_naam(naam: str) -> str:
    return re.sub(r"[^a-zà-ÿ ]", "", " ".join(str(naam).lower().split()))


def _zelfde_naam(a: str, b: str) -> bool:
    from difflib import SequenceMatcher
    a, b = _schoon_naam(a), _schoon_naam(b)
    return bool(a and b) and (a == b or SequenceMatcher(None, a, b).ratio() >= 0.8)


def _alle_leerlingen() -> dict:
    try:
        return kern.laad_klassenlijst(config.BASIS / "_bestaat_niet_", None)[0]
    except Exception:  # noqa: BLE001
        return {}


def _groep_voor(s: dict, toets_id: str) -> str:
    from collections import Counter
    tel = Counter(k.get("groep") for k in s["klassenlijst"].values() if k.get("groep"))
    if tel:
        return tel.most_common(1)[0][0]
    info = kern.lees_toetsmap(toets_id)
    jaar = re.search(r"\d", info.klas)
    return f"{jaar.group() if jaar else ''}{info.niveau[0]}.{'beco' if info.vak == 'Bedrijfseconomie' else 'econ'}"


def _bewaar_extra(lid: str, naam: str, klas: str, groep: str) -> None:
    import openpyxl
    nieuw = not EXTRA_LIJST.exists()
    wb = openpyxl.Workbook() if nieuw else openpyxl.load_workbook(EXTRA_LIJST)
    if nieuw:
        wb.remove(wb.active)
    blad = (groep or "extra")[:31]
    if blad in wb.sheetnames:
        ws = wb[blad]
    else:
        ws = wb.create_sheet(blad)
        ws.append(["Stamnr.", "Naam", "Klas"])
    for rij in ws.iter_rows(min_row=2):
        if str(rij[0].value).strip() == lid:
            rij[1].value, rij[2].value = naam, klas
            break
    else:
        ws.append([int(lid), naam, klas])
    EXTRA_LIJST.parent.mkdir(parents=True, exist_ok=True)
    wb.save(EXTRA_LIJST)


def _voeg_leerling_toe(s: dict, toets_id: str, d: dict) -> str:
    """Zet een leerling in de klassenlijst van deze toets en in de extra klassenlijst. Geeft het leerlingnummer."""
    lid = re.sub(r"\D", "", str(d.get("nummer", "")))
    naam = " ".join(str(d.get("naam", "")).split())
    klas = " ".join(str(d.get("klas", "")).split())
    if not 3 <= len(lid) <= 10:
        raise ValueError("Vul het leerlingnummer in (alleen cijfers, zoals in Magister).")
    if len(naam) < 3:
        raise ValueError("Vul de naam van de leerling in.")
    if not klas:
        raise ValueError("Vul de klas van de leerling in.")
    if lid in s["klassenlijst"]:
        bestaand = s["klassenlijst"][lid]["naam"]
        if _zelfde_naam(bestaand, naam):
            return lid
        raise ValueError(f"Leerlingnummer {lid} staat al in de klassenlijst als {bestaand}. Klopt het nummer?")
    alle = _alle_leerlingen()
    if lid in alle and not _zelfde_naam(alle[lid].naam, naam):
        raise ValueError(f"Leerlingnummer {lid} hoort in de klassenlijsten bij {alle[lid].naam}. Klopt het nummer?")
    groep = _groep_voor(s, toets_id)
    _bewaar_extra(lid, naam, klas, groep)
    s["klassenlijst"][lid] = {"naam": naam, "klas": klas, "groep": groep}
    s.setdefault("toegevoegd", []).append(lid)
    log.info("Leerling %s toegevoegd aan de klassenlijst van %s", lid, toets_id)
    return lid


def _suggesties(s: dict) -> dict:
    """Per wees-pagina: lijkt de gelezen naam op een leerling uit een ándere klassenlijst (klaswissel)?"""
    from difflib import SequenceMatcher
    open_ = [w for w in s["wezen"] if w["status"] == "open" and w.get("naam_gelezen")]
    if not open_:
        return {}
    anders = {lid: l for lid, l in _alle_leerlingen().items() if lid not in s["klassenlijst"]}
    uit = {}
    for w in open_:
        gelezen = _schoon_naam(w["naam_gelezen"])
        nummer = re.findall(r"\d{5,}", w["naam_gelezen"])
        kandidaten = []
        for lid, l in anders.items():
            r = 1.0 if lid in nummer else SequenceMatcher(None, gelezen, _schoon_naam(l.naam)).ratio()
            if r >= 0.6:
                kandidaten.append((r, {"nummer": lid, "naam": l.naam, "klas": l.klas, "groep": l.groep}))
        uit[w["id"]] = [k for _, k in sorted(kandidaten, key=lambda x: -x[0])[:3]]
    return uit


@app.post("/api/leerling-toevoegen")
def api_leerling_toevoegen():
    toets_id, d = _json_sessie()
    with sessie.bewerk(toets_id) as s:
        try:
            lid = _voeg_leerling_toe(s, toets_id, d)
        except ValueError as fout:
            return _fout(str(fout))
        except Exception as fout:  # noqa: BLE001
            log.exception("Leerling toevoegen mislukt")
            return _fout(f"Toevoegen is mislukt: {fout}. Staat Klassenlijsten extra.xlsx misschien open in Excel?")
    return jsonify(ok=True, leerling_id=lid, herladen=True)


# ---------------------------------------------------------------------------- fase 1: upload
@app.get("/")
def index():
    lijst, _ = kern.laad_klassenlijst(config.BASIS / "_bestaat_niet_", None)
    return render_template(
        "index.html", klassen=config.KLASSEN, vakken=config.VAKKEN, soorten=config.SOORTEN,
        vandaag=date.today().isoformat(),
        sessies=sessie.alle(), opgeslagen_lijst=len(lijst), abo=abonnement.status(),
        claude_inst=abonnement.instellingen(), MOTOREN=abonnement.MOTOREN, MODELLEN=abonnement.MODELLEN,
        bezig={k["toets_id"] for k in sessie.alle() if pipeline.loopt(k["toets_id"])})


@app.get("/api/klassenlijst")
def api_klassenlijst():
    """Welke groep uit de opgeslagen klassenlijsten hoort bij deze toetsnaam?"""
    try:
        info = kern.lees_toetsmap(request.args.get("toets_id", ""))
    except ValueError:
        return jsonify(aantal=0, groepen=[], precies=False)
    lijst, precies = kern.laad_klassenlijst(config.BASIS / "_bestaat_niet_", info)
    return jsonify(aantal=len(lijst), precies=precies,
                   groepen=sorted({l.groep for l in lijst.values() if l.groep}) if precies else [])


@app.post("/start")
def start():
    f = request.form
    code = re.sub(r"[^A-Za-z0-9.\-]", "", f.get("toetscode", "").strip())
    klas, vak = f.get("klas", ""), f.get("vak", "")
    if not code or klas not in {k[0] for k in config.KLASSEN} or vak not in config.VAKKEN:
        flash("Vul een toetscode in en kies een klas en een vak.")
        return redirect(url_for("index"))
    soort = f.get("soort", "SE") if f.get("soort") in {s[0] for s in config.SOORTEN} else "SE"
    toets_id = f"{code}_{soort}_{klas}_{vak}" + ("_tweefasen" if f.get("tweefasen") else "")
    try:
        info = kern.lees_toetsmap(toets_id)
        duur = int(f.get("duur") or 0)
        datum = date.fromisoformat(f.get("datum") or date.today().isoformat()).isoformat()
    except ValueError as fout:
        flash(f"Controleer de gegevens van de toets: {fout}")
        return redirect(url_for("index"))
    if not ai.kan_werken():
        flash("Claude Code (je abonnement) is niet gevonden en er is geen API-sleutel. Start de app via 'Start Webapp.command'.")
        return redirect(url_for("index"))

    scan, cv, lijst = (request.files.get(n) for n in ("scan", "correctie", "klassenlijst"))
    if not scan or not scan.filename.lower().endswith(".pdf"):
        flash("Kies de klassenscan als PDF.")
        return redirect(url_for("index"))
    if not cv or not cv.filename.lower().endswith((".docx", ".pdf")):
        flash("Kies het correctievoorschrift als .docx (of als PDF met tekst).")
        return redirect(url_for("index"))
    if lijst and lijst.filename and not lijst.filename.lower().endswith((".csv", ".xlsx")):
        flash("De klassenlijst moet een .csv of .xlsx zijn.")
        return redirect(url_for("index"))

    if sessie.lees(toets_id):
        if pipeline.loopt(toets_id):
            flash(f"{toets_id} wordt op dit moment al geanalyseerd.")
            return redirect(url_for("voortgang", toets_id=toets_id))
        if not f.get("overschrijf"):
            flash(f"Er bestaat al een analyse van {toets_id}. Vink 'Opnieuw analyseren' aan om "
                  "die te vervangen; wat al in de database staat blijft daar tot je opnieuw "
                  "definitief maakt.")
            return redirect(url_for("index"))
        sessie.verwijder(toets_id)

    smap = sessie.map_van(toets_id)
    smap.mkdir(parents=True, exist_ok=True)
    scan.save(smap / "scan.pdf")
    cv_naam = "correctievoorschrift" + ("." + cv.filename.rsplit(".", 1)[-1].lower())
    cv.save(smap / cv_naam)
    if lijst and lijst.filename:
        lijst.save(smap / ("_klassenlijst." + lijst.filename.rsplit(".", 1)[-1].lower()))
    sessie.nieuw(toets_id, {"vak": info.vak, "klas": info.klas, "niveau": info.niveau,
                            "tweefasen": info.tweefasen, "datum": datum, "duur": duur or None,
                            "scan_bestand": "scan.pdf", "cv_bestand": cv_naam})
    pipeline.start_analyse(toets_id)
    return redirect(url_for("voortgang", toets_id=toets_id))


# ---------------------------------------------------------------------------- fase 2: voortgang
@app.get("/voortgang/<toets_id>")
def voortgang(toets_id):
    s = sessie.lees(toets_id)
    if not s:
        abort(404)
    return render_template("voortgang.html", s=s)


@app.get("/api/status/<toets_id>")
def api_status(toets_id):
    s = sessie.lees(toets_id)
    if not s:
        abort(404)
    return jsonify(status=s["status"], voortgang=s["voortgang"], fout=s["fout"],
                   melding=s.get("melding", ""), loopt=pipeline.loopt(toets_id),
                   open=scores.open_punten(s),
                   leerlingen={lid: l["status"] for lid, l in s["leerlingen"].items()})


@app.post("/hervat")
def hervat():
    toets_id = request.form.get("toets", "")
    s = sessie.lees(toets_id)
    if not s:
        abort(404)
    if s["status"] == "fout" and not pipeline.loopt(toets_id):
        if s.get("aanvulling"):
            pipeline.hervat_aanvulling(toets_id)
        else:
            pipeline.start_analyse(toets_id)
    return redirect(url_for("voortgang", toets_id=toets_id))


@app.get("/beeld/<toets_id>/<int:pagina>")
def beeld(toets_id, pagina):
    if not sessie.geldig_id(toets_id):
        abort(404)
    return send_from_directory(sessie.map_van(toets_id) / "paginas", f"{pagina:04d}.jpg",
                               max_age=3600)


# ---------------------------------------------------------------------------- fase 3: validatie
@app.get("/review")
def review():
    toets_id = _toets_uit_verzoek(("review", "gepubliceerd", "publiceren"))
    s = sessie.lees(toets_id) if toets_id else None
    if not s:
        return render_template("leeg.html", titel="Valideren",
                               tekst="Er is nog geen analyse om te valideren.")
    if s["status"] in ("bezig", "fout"):
        return redirect(url_for("voortgang", toets_id=toets_id))

    leerlingen = []
    for lid, l in sorted(s["leerlingen"].items(), key=lambda kv: kv[1]["naam"].lower()):
        vragen = sorted(l["vragen"].values(), key=lambda v: v["nr"])
        for v in vragen:
            v["soort"], v["open"] = scores.soort(v), scores.is_open(v)
            v["eind"] = scores.eindscore(v)
            v["eind_leergat"] = scores.eind_leergat(v)[0]
        leerlingen.append(dict(l, leerling_id=lid, vragen=vragen,
                               bespreek=[v for v in vragen if v["soort"]],
                               totaal=sum(v["eind"] for v in vragen),
                               maximum=sum(v["max"] for v in vragen)))
    weggelaten = [{"soort": "leeg", "id": str(i), "paginas": [i], "naam_gelezen": ""}
                  for i, p in enumerate(s["paginas"]) if p.get("leeg")]
    weggelaten += [{"soort": "genegeerd", "id": w["id"], "paginas": w["paginas"],
                    "naam_gelezen": w["naam_gelezen"]}
                   for w in s["wezen"] if w["status"] == "genegeerd"]
    weggelaten.sort(key=lambda w: w["paginas"][0])
    vrij = [(lid, k["naam"]) for lid, k in sorted(s["klassenlijst"].items(),
                                                 key=lambda kv: kv[1]["naam"].lower())]
    from collections import Counter
    klassen = Counter(k["klas"] for k in s["klassenlijst"].values() if k.get("klas"))
    return render_template(
        "review.html", s=s, leerlingen=leerlingen, open=scores.open_punten(s), klassenlijst=vrij,
        suggesties=_suggesties(s), klassen=sorted(klassen), standaard_klas=(klassen.most_common(1) or [("", 0)])[0][0],
        weggelaten=weggelaten,
        afwezig=[s["klassenlijst"][lid]["naam"] for lid in s["afwezig"]
                 if lid in s["klassenlijst"] and lid not in s["leerlingen"]],
        leergaten=kern.LEERGATEN, matrijs={m["vraagnummer"]: m for m in s["matrijs"]})


@app.post("/api/besluit")
def api_besluit():
    toets_id, d = _json_sessie()
    keuze = d.get("keuze")
    with sessie.bewerk(toets_id) as s:
        try:
            v = s["leerlingen"][d["leerling"]]["vragen"][str(d["vraag"])]
        except KeyError:
            return _fout("Vraag niet gevonden.", 404)
        if keuze == "open":
            v.update(besluit=None, definitief=None, def_leergat=None, def_omschrijving=None)
        else:
            if keuze == "claude":
                score = v["ai"]
            elif keuze == "docent" and v["docent"] is not None:
                score = v["docent"]
            elif keuze == "akkoord":
                score = scores.eindscore(dict(v, besluit=None))
            elif keuze == "aangepast":
                try:
                    score = float(str(d.get("score", "")).replace(",", "."))
                except ValueError:
                    return _fout("Vul een score in.")
                if not 0 <= score <= v["max"]:
                    return _fout(f"De score moet tussen 0 en {v['max']} liggen.")
            else:
                return _fout("Onbekende keuze.")
            leergat = d.get("leergat") or v["leergat"]
            if score < v["max"] and leergat not in kern.LEERGATEN:
                return _fout("Kies een leergat bij dit puntenverlies.")
            v.update(besluit=keuze, definitief=scores.begrens(score, v["max"]),
                     def_leergat=leergat if score < v["max"] else None,
                     def_omschrijving=(d.get("omschrijving") or "").strip() or None)
            reden = (d.get("reden") or "").strip()
            if reden and keuze in ("docent", "aangepast"):
                sessie.voeg_leerregel_toe({
                    "vak": s["vak"], "niveau": s["niveau"], "toets_id": toets_id, "vraag": v["nr"],
                    "domein": v["domein"], "max_score": v["max"], "antwoord": v["antwoord"],
                    "ai_score": v["ai"], "docent_score": v["definitief"],
                    "ai_toelichting": v["toelichting"], "reden": reden})
        antwoord = dict(ok=True, besluit=v["besluit"], eind=scores.eindscore(v),
                        leergat=scores.eind_leergat(v)[0], open=scores.open_punten(s))
    return jsonify(antwoord)


@app.post("/api/bulk")
def api_bulk():
    """Alle nog open conflicten of twijfelgevallen in één keer afhandelen."""
    toets_id, d = _json_sessie()
    soort, keuze = d.get("soort"), d.get("keuze")
    if (soort, keuze) not in {("conflict", "docent"), ("twijfel", "akkoord")}:
        return _fout("Onbekende actie.")
    overgeslagen = 0
    with sessie.bewerk(toets_id) as s:
        for l in s["leerlingen"].values():
            for v in l["vragen"].values():
                if not scores.is_open(v) or scores.soort(v) != soort:
                    continue
                score = scores.eindscore(v)             # docentscore, of AI-score bij twijfel
                if score < v["max"] and v["leergat"] not in kern.LEERGATEN:
                    overgeslagen += 1                   # leergat ontbreekt: zelf beslissen
                    continue
                v.update(besluit=keuze, definitief=score,
                         def_leergat=v["leergat"] if score < v["max"] else None)
        tel = scores.open_punten(s)
    return jsonify(ok=True, open=tel, overgeslagen=overgeslagen)


@app.post("/api/wees")
def api_wees():
    toets_id, d = _json_sessie()
    lid = d.get("leerling_id", "")
    with sessie.bewerk(toets_id) as s:
        wees = next((w for w in s["wezen"] if w["id"] == d.get("wees")), None)
        if not wees or wees["status"] != "open":
            return _fout("Deze pagina's zijn al afgehandeld.", 404)
        if lid == "__negeer__":
            wees["status"] = "genegeerd"
            return jsonify(ok=True, open=scores.open_punten(s))
        if lid == "__nieuw__":
            try:
                lid = _voeg_leerling_toe(s, toets_id, d.get("nieuw") or {})
            except ValueError as fout:
                return _fout(str(fout))
            except Exception as fout:  # noqa: BLE001
                log.exception("Leerling toevoegen mislukt")
                return _fout(f"Toevoegen is mislukt: {fout}. Staat Klassenlijsten extra.xlsx misschien open in Excel?")
        if lid not in s["klassenlijst"]:
            return _fout("Kies een leerling uit de klassenlijst.")
        bestaand = s["leerlingen"].get(lid)
        if bestaand and bestaand["status"] == "bezig":
            return _fout("Deze leerling wordt op dit moment nagekeken. Probeer het zo opnieuw.")
        if bestaand:
            bestaand["paginas"] = sorted(set(bestaand["paginas"]) | set(wees["paginas"]))
        else:
            k = s["klassenlijst"][lid]
            s["leerlingen"][lid] = pipeline._nieuwe_leerling(
                k["naam"], k["klas"], list(wees["paginas"]), wees["naam_gelezen"])
            s["leerlingen"][lid]["koppeling"]["ok"] = True
        s["feedforward"].pop(lid, None)
        wees.update(status="gekoppeld", gekoppeld_aan=lid)
    pipeline.start_leerling(toets_id, lid)
    return jsonify(ok=True, herladen=True)


@app.post("/api/terughalen")
def api_terughalen():
    """Een weggelaten pagina (leeg of 'hoort niet bij deze toets') alsnog aan een leerling koppelen."""
    toets_id, d = _json_sessie()
    lid, soort, sleutel = d.get("leerling_id", ""), d.get("soort"), str(d.get("id", ""))
    with sessie.bewerk(toets_id) as s:
        if lid not in s["klassenlijst"]:
            return _fout("Kies een leerling uit de klassenlijst.")
        bestaand = s["leerlingen"].get(lid)
        if bestaand and bestaand["status"] == "bezig":
            return _fout("Deze leerling wordt op dit moment nagekeken. Probeer het zo opnieuw.")
        if soort == "leeg" and sleutel.isdigit() and int(sleutel) < len(s["paginas"]) \
                and s["paginas"][int(sleutel)].get("leeg"):
            paginas = [int(sleutel)]
            s["paginas"][paginas[0]].update(leeg=False, toegevoegd=lid)
            s["lege_paginas"] = max(0, (s.get("lege_paginas") or 1) - 1)
        elif soort == "genegeerd":
            w = next((w for w in s["wezen"] if w["id"] == sleutel and w["status"] == "genegeerd"), None)
            if not w:
                return _fout("Deze pagina's zijn al toegevoegd.", 404)
            paginas = list(w["paginas"])
            w.update(status="gekoppeld", gekoppeld_aan=lid)
        else:
            return _fout("Deze pagina is al toegevoegd.", 404)
        pipeline.voeg_paginas_toe(s, paginas, lid)
    pipeline.start_leerling(toets_id, lid)
    return jsonify(ok=True, herladen=True)


@app.post("/bijscannen")
def bijscannen():
    """Extra pagina's (vergeten blad, inhaaltoets) achter de klassenscan plakken en verwerken."""
    toets_id = request.form.get("toets", "")
    s = sessie.lees(toets_id)
    if not s:
        abort(404)
    if pipeline.loopt(toets_id) or s["status"] not in ("review", "gepubliceerd"):
        flash("Er loopt nog een verwerking. Wacht tot die klaar is.")
        return redirect(url_for("review", toets=toets_id))
    if any(l["status"] == "bezig" for l in s["leerlingen"].values()):
        flash("Er wordt nog een leerling nagekeken. Probeer het zo opnieuw.")
        return redirect(url_for("review", toets=toets_id))
    bestand = request.files.get("scan")
    if not bestand or not bestand.filename.lower().endswith(".pdf"):
        flash("Kies de bijgescande pagina's als PDF.")
        return redirect(url_for("review", toets=toets_id))
    pad = sessie.map_van(toets_id) / f"bijscan_{datetime.now():%Y%m%d_%H%M%S}.pdf"
    bestand.save(pad)
    try:
        aantal = pipeline.start_aanvulling(toets_id, pad)
    except pipeline.Stop as fout:
        flash(str(fout))
        return redirect(url_for("review", toets=toets_id))
    except Exception as fout:  # noqa: BLE001
        log.exception("Bijscannen mislukt")
        flash(f"De PDF kon niet worden toegevoegd: {fout}")
        return redirect(url_for("review", toets=toets_id))
    flash(f"{aantal} pagina('s) toegevoegd aan de scan.")
    return redirect(url_for("voortgang", toets_id=toets_id))


@app.post("/api/verplaats")
def api_verplaats():
    """Pagina's van de ene leerling naar een andere verplaatsen, of uit de toets weglaten."""
    toets_id, d = _json_sessie()
    van, naar = d.get("van", ""), d.get("naar", "")
    try:
        paginas = sorted({int(p) for p in d.get("paginas", [])})
    except (TypeError, ValueError):
        return _fout("Ongeldige pagina's.")
    opnieuw = []
    with sessie.bewerk(toets_id) as s:
        l = s["leerlingen"].get(van)
        if not l:
            return _fout("Leerling niet gevonden.", 404)
        if not paginas or not set(paginas) <= set(l["paginas"]):
            return _fout("Vink pagina's van deze leerling aan.")
        if naar == van:
            return _fout("Kies een andere leerling.")
        if naar != "__weglaten__" and naar not in s["klassenlijst"]:
            return _fout("Kies een leerling uit de klassenlijst.")
        for lid in (van, naar):
            if lid in s["leerlingen"] and s["leerlingen"][lid]["status"] == "bezig":
                return _fout(f"{s['leerlingen'][lid]['naam']} wordt op dit moment nagekeken. "
                             "Probeer het zo opnieuw.")
        rest = [p for p in l["paginas"] if p not in paginas]
        s["feedforward"].pop(van, None)
        if rest:
            l["paginas"] = rest
            opnieuw.append(van)
        else:                                          # alle pagina's weg: geen werk meer
            s["leerlingen"].pop(van)
            if s.get("lijst_precies") and van not in s.get("afwezig", []):
                s.setdefault("afwezig", []).append(van)
        if naar == "__weglaten__":
            s["wezen"].append({"id": f"wees_weg_{van}_{len(s['wezen'])}", "paginas": paginas,
                               "status": "genegeerd", "naam_gelezen": l["naam"],
                               "gekoppeld_aan": ""})
        else:
            pipeline.voeg_paginas_toe(s, paginas, naar)
            s["leerlingen"][naar]["koppeling"]["ok"] = True
            opnieuw.append(naar)
    for lid in opnieuw:
        pipeline.start_leerling(toets_id, lid)
    return jsonify(ok=True, herladen=True)


@app.post("/api/herkoppel")
def api_herkoppel():
    """Het werk van 'van' hoort bij een andere leerling ('naar'): alles verhuist mee."""
    toets_id, d = _json_sessie()
    van, naar = d.get("van", ""), d.get("naar", "")
    with sessie.bewerk(toets_id) as s:
        if van not in s["leerlingen"] or naar not in s["klassenlijst"]:
            return _fout("Leerling niet gevonden.", 404)
        if naar in s["leerlingen"]:
            return _fout(f"{s['klassenlijst'][naar]['naam']} heeft al werk in deze scan. "
                         "Koppel dat eerst aan iemand anders.")
        if s["leerlingen"][van]["status"] == "bezig":
            return _fout("Deze leerling wordt op dit moment nagekeken.")
        l = s["leerlingen"].pop(van)
        l.update(naam=s["klassenlijst"][naar]["naam"], klas=s["klassenlijst"][naar]["klas"])
        l["koppeling"].update(vlag="", ok=True)
        s["leerlingen"][naar] = l
        s["feedforward"].pop(van, None)
    return jsonify(ok=True, herladen=True)


@app.post("/api/koppeling_ok")
def api_koppeling_ok():
    toets_id, d = _json_sessie()
    with sessie.bewerk(toets_id) as s:
        if d.get("leerling") not in s["leerlingen"]:
            return _fout("Leerling niet gevonden.", 404)
        s["leerlingen"][d["leerling"]]["koppeling"]["ok"] = True
        return jsonify(ok=True, open=scores.open_punten(s))


@app.post("/api/leerling")
def api_leerling():
    """Een leerling opnieuw nakijken, of niet meenemen in deze toets."""
    toets_id, d = _json_sessie()
    lid, actie = d.get("leerling", ""), d.get("actie")
    with sessie.bewerk(toets_id) as s:
        l = s["leerlingen"].get(lid)
        if not l:
            return _fout("Leerling niet gevonden.", 404)
        if l["status"] == "bezig":
            return _fout("Deze leerling wordt op dit moment nagekeken.")
        if actie == "verwijder":
            s["leerlingen"].pop(lid)
            s["feedforward"].pop(lid, None)
            s["wezen"].append({"id": f"wees_uit_{lid}_{len(s['wezen'])}", "paginas": l["paginas"],
                               "naam_gelezen": l["naam"], "status": "genegeerd",
                               "gekoppeld_aan": ""})
            return jsonify(ok=True, herladen=True)
    if actie == "opnieuw":
        pipeline.start_leerling(toets_id, lid)
        return jsonify(ok=True, herladen=True)
    return _fout("Onbekende actie.")


# ---------------------------------------------------------------------------- fase 4: publicatie
@app.post("/publiceer")
def publiceer():
    toets_id = request.form.get("toets", "")
    s = sessie.lees(toets_id)
    if not s:
        abort(404)
    if pipeline.loopt(toets_id):
        return redirect(url_for("voortgang", toets_id=toets_id))
    tel = scores.open_punten(s)
    if tel["totaal"]:
        flash(f"Er staan nog {tel['totaal']} punten open. Handel die eerst af.")
        return redirect(url_for("review", toets=toets_id))
    if not any(l["status"] == "klaar" for l in s["leerlingen"].values()):
        flash("Er is geen nagekeken leerlingwerk om te publiceren.")
        return redirect(url_for("review", toets=toets_id))
    try:
        aantal = pipeline.start_publicatie(toets_id)
    except Exception as fout:  # noqa: BLE001
        log.exception("Publiceren mislukt")
        flash(f"Opslaan in de database is mislukt: {fout}")
        return redirect(url_for("review", toets=toets_id))
    flash(f"De scores van {aantal} leerlingen staan in de database.")
    return redirect(url_for("voortgang", toets_id=toets_id))


def _feedback_toets(lid: str | None = None) -> str | None:
    toets_id = request.args.get("toets")
    if toets_id:
        return toets_id
    for t in database.toetsen():                       # nieuwste toets waar deze leerling in zit
        if t["aantal"] and (lid is None or any(r["leerling_id"] == lid
                                               for r in database.rijen(t["toets_id"]))):
            return t["toets_id"]
    return None


@app.get("/feedback/<leerling_id>")
def feedback(leerling_id):
    toets_id = _feedback_toets(leerling_id)
    strook = rapport.feedback(toets_id, leerling_id) if toets_id else None
    if not strook:
        return render_template("leeg.html", titel="Feedbackstrook",
                               tekst="Voor deze leerling staan nog geen definitieve scores in "
                                     "de database."), 404
    return render_template("feedback.html", stroken=[strook], toets_id=toets_id, alle=False)


@app.get("/feedback")
def feedback_alle():
    toets_id = _feedback_toets()
    ids = sorted({r["leerling_id"] for r in database.rijen(toets_id)}) if toets_id else []
    stroken = [rapport.feedback(toets_id, lid) for lid in ids]
    stroken.sort(key=lambda f: (f["naam"] or f["leerling_id"]).lower())
    if not stroken:
        return render_template("leeg.html", titel="Feedbackstroken",
                               tekst="Er staan nog geen definitieve scores in de database.")
    return render_template("feedback.html", stroken=stroken, toets_id=toets_id, alle=True)


# ---------------------------------------------------------------------------- fase 5: rapportage
CHART_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"


def _chart_bron() -> str:
    """Chart.js van internet (jsDelivr). Zet je zelf chart.umd.min.js in static/js, dan wordt die gebruikt."""
    lokaal = config.APP_MAP / "static" / "js" / "chart.umd.min.js"
    if lokaal.exists() and lokaal.stat().st_size > 50_000:
        return url_for("static", filename="js/chart.umd.min.js")
    return CHART_CDN


@app.get("/klassenrapportage")
def klassenrapportage():
    toetsen = [t for t in database.toetsen() if t["aantal"]]
    toets_id = request.args.get("toets") or (toetsen[0]["toets_id"] if toetsen else None)
    stat = rapport.statistiek_voor(toets_id) if toets_id else None
    if not stat:
        return render_template("leeg.html", titel="Klassenrapportage",
                               tekst="Er staan nog geen definitieve scores in de database. Maak "
                                     "eerst een toets definitief.")
    s = sessie.lees(toets_id) or {}
    namen = {l["leerling_id"]: l["naam"] or l["leerling_id"] for l in stat["leerlingen"]}
    trend = rapport.trend_voor(toets_id)
    grafieken = {
        "verdeling": stat["verdeling"],
        "trend": [{"label": t["toets_id"].split("_")[0] + " " + t["datum"][:7], "gem": t["gem"],
                   "huidig": t["toets_id"] == toets_id, "n": t["n"]} for t in trend],
        "bloom": [{"naam": b["naam"], "uitval": b["uitval"], "vragen": b["vragen"]}
                  for b in stat["bloom"]],
        "vragen": [{"nr": v["nr"], "p": None if v["p"] is None else round(v["p"] * 100),
                    "signaal": bool(v["signalen"])} for v in stat["vragen"]],
        "leergaten": [{"naam": f"{g['code']} {g['naam']}", "punten": g["punten"]}
                      for g in stat["leergaten"]],
        "tweefasen": ([{"naam": "Stof voor havo en vwo (H+V)", "pct": stat["tweefasen"]["hv"]["pct"]}
                       if stat["tweefasen"]["hv"] else None,
                       {"naam": "Stof alleen voor vwo (V)", "pct": stat["tweefasen"]["v"]["pct"]}
                       if stat["tweefasen"]["v"] else None] if stat["tweefasen"] else []),
        "domein": [{"naam": d["naam"], "pct": d["pct"]} for d in stat["domein"]],
    }
    grafieken["tweefasen"] = [x for x in grafieken["tweefasen"] if x]
    grafieken["scores"] = [[l["score"], l["max"], l["hv_score"], l["hv_max"]]
                           for l in stat["leerlingen"]]
    return render_template(
        "klassenrapportage.html", toets_id=toets_id, toetsen=toetsen, stat=stat, trend=trend,
        grafieken=grafieken, advies=s.get("klasadvies"), namen=namen,
        toets=database.toets(toets_id), heeft_sessie=bool(s), bezig=pipeline.loopt(toets_id),
        melding=s.get("melding", ""), chart_bron=_chart_bron(), sessie_s=s,
        cesuur=analyse.cesuur(stat["tijd"]["punten"], stat["n_term"]),
        mist_leerdoelen=bool(s.get("matrijs")) and not all(m.get("leerdoel") for m in s["matrijs"]))


@app.post("/api/normering")
def api_normering():
    """N-term van een toets opslaan (en eventueel een aparte N-term voor het havo-cijfer)."""
    d = request.get_json(silent=True) or {}
    toets_id = d.get("toets", "")
    if not database.toets(toets_id):
        return _fout("Toets niet gevonden.", 404)

    def lees(waarde):
        if waarde in (None, ""):
            return None
        try:
            n = round(float(str(waarde).replace(",", ".")), 1)
        except ValueError:
            raise ValueError("Vul de N-term in als getal, bijvoorbeeld 1,2.")
        if not 0.0 <= n <= 3.0:
            raise ValueError("De N-term moet tussen 0,0 en 3,0 liggen.")
        return n
    try:
        n, n_havo = lees(d.get("n")), lees(d.get("n_havo"))
    except ValueError as fout:
        return _fout(str(fout))
    if n is None:
        return _fout("Vul een N-term in.")
    database.zet_normering(toets_id, n, None if n_havo == n else n_havo)
    return jsonify(ok=True, herladen=True)


@app.post("/api/leerdoelen")
def api_leerdoelen():
    """Leerdoelen en onderwerpen ophalen voor een toets die al geanalyseerd is."""
    toets_id, _ = _json_sessie()
    if not ai.kan_werken():
        return _fout("Claude Code (je abonnement) is niet gevonden en er is geen API-sleutel.")
    try:
        _los(toets_id, lambda: pipeline.vul_leerdoelen(toets_id))
    except pipeline.Stop as fout:
        return _fout(str(fout))
    except abonnement.ToestemmingNodig:
        return _fout(ABO_LOS, 409)
    except Exception as fout:  # noqa: BLE001
        log.exception("Leerdoelen ophalen mislukt")
        return _fout(f"Leerdoelen ophalen is niet gelukt: {fout}", 502)
    return jsonify(ok=True, herladen=True)


@app.post("/api/klasadvies")
def api_klasadvies():
    toets_id, _ = _json_sessie()
    if not ai.kan_werken():
        return _fout("Claude Code (je abonnement) is niet gevonden en er is geen API-sleutel.")
    try:
        _los(toets_id, lambda: pipeline.maak_klasadvies(toets_id))
    except abonnement.ToestemmingNodig:
        return _fout(ABO_LOS, 409)
    except Exception as fout:  # noqa: BLE001
        log.exception("Klassenadvies mislukt")
        return _fout(f"Het klassenadvies is niet gelukt: {fout}", 502)
    return jsonify(ok=True, herladen=True)


# ---------------------------------------------------------------------------- leerdoelenregister
def _scope_uit_verzoek():
    scopes = register.scopes()
    gekozen = (request.args.get("vak"), request.args.get("sector"))
    return scopes, (gekozen if gekozen in scopes else (scopes[0] if scopes else None))


@app.get("/leerdoelen")
def leerdoelen_pagina():
    scopes, scope = _scope_uit_verzoek()
    if not scope:
        return render_template("leeg.html", titel="Leerdoelen",
                               tekst="Er staan nog geen toetsen in de database.")
    vak, sector = scope
    alle = register.items(vak, sector)
    return render_template(
        "leerdoelen.html", scopes=[(v, s, register.naam_scope(v, s)) for v, s in scopes],
        vak=vak, sector=sector, naam=register.naam_scope(vak, sector),
        overzicht=register.overzicht(vak, sector), controleren=register.te_controleren(vak, sector),
        zonder_code=sum(1 for it in alle if not it["leerdoel_code"]),
        zonder_tekst=sorted({it["toets_id"] for it in alle if not it["leerdoel"]}),
        aantal_vragen=len(alle), register_leeg=not register.register(vak, sector))


@app.post("/api/register/bijwerken")
def api_register_bijwerken():
    d = request.get_json(silent=True) or {}
    if (d.get("vak"), d.get("sector")) not in register.scopes():
        return _fout("Onbekend register.")
    if not ai.kan_werken():
        return _fout("Claude Code (je abonnement) is niet gevonden en er is geen API-sleutel.")
    try:
        abonnement.zet(False)
        uit = register.bijwerken(d["vak"], d["sector"])
    except abonnement.ToestemmingNodig:
        return _fout("Je Claude-abonnement kan nu niet verder (limiet bereikt of niet ingelogd). Er is geen "
                     "API-tegoed gebruikt. Probeer het later opnieuw.", 409)
    except Exception as fout:  # noqa: BLE001
        log.exception("Register bijwerken mislukt")
        return _fout(f"Bijwerken is niet gelukt: {fout}", 502)
    return jsonify(ok=True, herladen=True, **uit)


@app.post("/api/register/bevestig")
def api_register_bevestig():
    d = request.get_json(silent=True) or {}
    try:
        register.bevestig(int(d.get("item_id", 0)), d.get("code", ""))
    except (ValueError, TypeError) as fout:
        return _fout(str(fout) or "Kies een leerdoel.")
    return jsonify(ok=True)


@app.post("/api/register/wijzig")
def api_register_wijzig():
    d = request.get_json(silent=True) or {}
    if not (d.get("omschrijving") or "").strip():
        return _fout("Vul een omschrijving in.")
    register.wijzig(d.get("code", ""), d["omschrijving"], d.get("leerling_zin", ""), d.get("thema", ""))
    return jsonify(ok=True, herladen=True)


@app.post("/api/register/samenvoegen")
def api_register_samenvoegen():
    d = request.get_json(silent=True) or {}
    try:
        register.samenvoegen(d.get("van", ""), d.get("naar", ""))
    except ValueError as fout:
        return _fout(str(fout))
    return jsonify(ok=True, herladen=True)


# ---------------------------------------------------------------------------- export
@app.get("/export")
def export_pagina():
    con = kern.open_db()
    try:
        toetsen = [dict(r) for r in con.execute(
            "SELECT t.toets_id, t.vak, t.datum, COUNT(DISTINCT e.leerling_id) AS aantal,"
            " SUM(CASE WHEN m.leerdoel_code IS NULL THEN 1 ELSE 0 END) AS zonder_code"
            " FROM toetsen t JOIN toetsmatrijs m ON m.toets_id = t.toets_id"
            " LEFT JOIN evaluatieresultaten e ON e.item_id = m.item_id"
            " GROUP BY t.toets_id ORDER BY t.datum DESC")]
        lln = [dict(r) for r in con.execute(
            "SELECT DISTINCT l.leerling_id, l.klas, m.toets_id FROM leerlingen l"
            " JOIN evaluatieresultaten e ON e.leerling_id = l.leerling_id"
            " JOIN toetsmatrijs m ON m.item_id = e.item_id")]
    finally:
        con.close()
    for t in toetsen:
        t["sector"] = (register.sector_van(t["toets_id"]) or ("", ""))[1]
        t["soort"] = config.soort_van(t["toets_id"])
    namen = rapport._alle_namen()
    per_ll = {}
    for r in lln:
        p = per_ll.setdefault(r["leerling_id"], {"id": r["leerling_id"], "klas": r["klas"] or "",
                                                 "naam": namen.get(r["leerling_id"], {}).get("naam", ""),
                                                 "toetsen": []})
        p["toetsen"].append(r["toets_id"])
    codes = [dict(c) for c in register.alle_codes().values()]
    return render_template("export.html", toetsen=toetsen,
                           leerlingen=sorted(per_ll.values(), key=lambda x: (x["klas"], x["naam"] or x["id"])),
                           codes=sorted(codes, key=lambda c: c["code"]), exports=leerprofiel.exports(),
                           map=str(leerprofiel.EXPORTS))


@app.post("/export")
def export_maken():
    f = request.form
    toetsen = f.getlist("toets")
    leerlingen = set(f.getlist("leerling"))
    codes = set(f.getlist("code"))
    if not toetsen:
        flash("Kies minstens één toets.")
        return redirect(url_for("export_pagina"))
    try:
        doel = leerprofiel.bouw_export(toetsen, leerlingen or None, codes or None, f.get("label", ""))
    except ValueError as fout:
        flash(str(fout))
        return redirect(url_for("export_pagina"))
    flash(f"Export gemaakt: {doel.name}. De map staat in Toetsevaluatie/Exports; de zip kun je hieronder downloaden.")
    return redirect(url_for("export_pagina"))


@app.get("/export/download/<naam>")
def export_download(naam):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", naam):
        abort(404)
    return send_from_directory(leerprofiel.EXPORTS, naam + ".zip", as_attachment=True)


# ---------------------------------------------------------------------------- leerregels
@app.get("/leerregels")
def leerregels():
    return render_template("leerregels.html", regels=list(reversed(sessie.laad_leerregels())),
                           maximum=config.MAX_LEERREGELS_IN_PROMPT, bestand=config.LEERREGELS.name)


@app.post("/leerregels/verwijder")
def leerregel_verwijder():
    sessie.verwijder_leerregel(int(request.form.get("id", 0)))
    return redirect(url_for("leerregels"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    for naam in ("httpx", "httpx2", "httpcore", "httpcore2", "anthropic", "werkzeug"):
        logging.getLogger(naam).setLevel(logging.WARNING)
    kern.open_db().close()                              # stopt met een duidelijke melding als de database ontbreekt
    database.zorg_voor_normering()                      # tabel normering + N-term in cijferoverzicht
    database.zorg_voor_leerdoelen()                     # kolommen leerdoel en onderwerp in toetsmatrijs
    register.zorg_voor_register()                       # leerdoelenregister en leerdoelcodes
    config.SESSIES.mkdir(parents=True, exist_ok=True)
    pipeline.herstel_na_start()
    print(f"\nToetsevaluatie draait op http://127.0.0.1:{config.POORT}  (stoppen: Ctrl+C)\n")
    app.run(host="127.0.0.1", port=config.POORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
