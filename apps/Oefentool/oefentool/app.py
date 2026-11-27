# -*- coding: utf-8 -*-
"""Oefentool: lokale webapp op http://127.0.0.1:5056 (start met 'Start Oefentool.command')."""
from __future__ import annotations

import json
import logging
import os
import re
import webbrowser
from collections import defaultdict
from pathlib import Path

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory,
                   url_for)

from . import bibliotheek, claude, dashboard, figuur, kwaliteit, leerloop, opdracht, opslag, te, uitvoer, versie, zelfcheck
from .instellingen import (BLOOM, DOELEN, EIGEN_BRONNEN, LEERGATEN, LOG, MODELLEN, MOTOREN, OPDRACHTEN, POORT, PRIJZEN, VORMEN,
                           drive_map, laad_instellingen, bewaar_instellingen)

app = Flask(__name__)
app.secret_key = os.environ.get("OEFENTOOL_GEHEIM", "oefentool-lokaal")
app.config["MAX_CONTENT_LENGTH"] = 400 * 1024 * 1024
log = logging.getLogger("oefentool")


@app.context_processor
def overal():
    return {"heeft_sleutel": claude.kan_werken(), "api_sleutel": claude.heeft_sleutel(), "LEERGATEN": LEERGATEN,
            "DOELEN": DOELEN, "VORMEN": VORMEN, "verouderd": versie.verouderd()}


@app.get("/api/versie")
def api_versie():
    return jsonify(versie=versie.GESTART)


@app.template_filter("pct")
def _pct(x):
    return "-" if x is None else f"{round(x * 100)}%"


def _json_fout(fout, code=400):
    return jsonify(ok=False, fout=str(fout)), code


# ---------------------------------------------------------------------------- opdrachten
@app.get("/")
def index():
    ok, waarom = te.beschikbaar()
    toetsen, leerlingen = [], []
    if ok:
        toetsen = te.toetsen()
        per = defaultdict(lambda: {"klas": "", "toetsen": []})
        for t in toetsen:
            for r in te.leerlingen_van([t["toets_id"]]):
                p = per[r["leerling_id"]]
                p["klas"] = r["klas"] or p["klas"]
                p["toetsen"].append(t["toets_id"])
        leerlingen = [{"id": k, **v} for k, v in sorted(per.items(), key=lambda kv: (kv[1]["klas"], kv[0]))]
    bouw_ok, bouw_waarom = uitvoer.bouwmotor_klaar()
    return render_template("index.html", opdrachten=opdracht.alle(), toetsen=toetsen, leerlingen=leerlingen,
                           abo=claude.abonnement_status(), motor=claude.motor(),
                           te_ok=ok, te_waarom=waarom, bouw_ok=bouw_ok, bouw_waarom=bouw_waarom,
                           drive=drive_map(), bibliotheek=bibliotheek.overzicht()["tel"])


@app.post("/opdracht/nieuw")
def nieuw():
    f = request.form
    try:
        oid = opdracht.maak(
            vak="", toets_ids=f.getlist("toets"), doel=f.get("doel", ""), klassen=f.getlist("klas"),
            leerling_ids=f.getlist("leerling"), codes=[], naam=f.get("naam", ""),
            instellingen={"vormen": f.getlist("vorm"), "minuten": f.get("minuten"), "oefenexamen": f.get("oefenexamen"),
                          "groepen": f.get("groepen"), "max_leerdoelen": f.get("max_leerdoelen"),
                          "examenminuten": f.get("examenminuten")})
    except (opdracht.Stop, te.GeenToetsevaluatie, ValueError) as fout:
        flash(str(fout))
        return redirect(url_for("index"))
    return redirect(url_for("toon", oid=oid))


def _opdracht_of_404(oid):
    o = opdracht.lees(oid)
    if not o:
        abort(404)
    return o


@app.get("/opdracht/<oid>")
def toon(oid):
    o = _opdracht_of_404(oid)
    o["bezig"] = opdracht.loopt(oid)
    items = opslag.items_van([u for us in o["pool"].values() for u in us])
    reg = te.alle_codes()
    doelen = {d["code"]: d for d in o["doelen"]}
    oudere = {}
    for it in items.values():
        if it.get("ouder_uid"):
            oudere[it["uid"]] = opslag.item(it["ouder_uid"])
    te_maken = sum(1 for ond in (o.get("plan") or {}).get("onderdelen", [])
                   if any(s["aantal"] > 0 for s in ond["stappen"]) or o["instellingen"].get("oefenexamen"))
    hergebruik = sum(len(ond.get("hergebruik", [])) for ond in (o.get("plan") or {}).get("onderdelen", []))
    per_doel = opslag.kosten_per_leerdoel()
    nog = sum(1 for ond in (o.get("plan") or {}).get("onderdelen", []) if not o["pool"].get(ond["leerdoel_code"]))
    stap = (o.get("toestemming_nodig") or {}).get("stap", "")
    api_schatting = {"_plan": 0.15, "_genereer": max(nog, 1) * per_doel, "_klaarzetten": 0.05}.get(stap, per_doel / 3)
    return render_template("opdracht.html", o=o, items=items, reg=reg, doelen=doelen, oudere=oudere, BLOOM=BLOOM,
                           kosten=opslag.kosten_van(oid), schatting=te_maken * per_doel,
                           te_maken=te_maken, hergebruik=hergebruik, motor=claude.motor(),
                           api_schatting=api_schatting, nog=nog)


@app.post("/opdracht/<oid>/api-toestemming")
def api_toestemming(oid):
    _opdracht_of_404(oid)
    try:
        opdracht.geef_api_toestemming(oid)
    except opdracht.Stop as fout:
        flash(str(fout))
    return redirect(url_for("toon", oid=oid))


@app.post("/opdracht/<oid>/via-abonnement")
def via_abonnement(oid):
    _opdracht_of_404(oid)
    try:
        opdracht.opnieuw_via_abonnement(oid)
    except opdracht.Stop as fout:
        flash(str(fout))
    return redirect(url_for("toon", oid=oid))


@app.get("/api/opdracht/<oid>/status")
def status(oid):
    o = _opdracht_of_404(oid)
    return jsonify(status=o["status"], voortgang=o.get("voortgang") or {}, fout=o.get("fout", ""),
                   bezig=opdracht.loopt(oid), in_bewerking=o.get("in_bewerking", []))


@app.post("/opdracht/<oid>/plan")
def plan(oid):
    try:
        opdracht.start_plan(oid, request.form.get("feedback", ""))
    except opdracht.Stop as fout:
        flash(str(fout))
    return redirect(url_for("toon", oid=oid))


@app.post("/api/opdracht/<oid>/plan")
def plan_aanpassen(oid):
    d = request.get_json(force=True)
    try:
        if d.get("weglaten"):
            opdracht.laat_leerdoel_weg(oid, d["code"])
        else:
            opdracht.pas_plan_aan(oid, d["code"], int(d["stap"]), int(d["aantal"]))
    except (KeyError, ValueError) as fout:
        return _json_fout(fout)
    return jsonify(ok=True)


@app.post("/opdracht/<oid>/genereer")
def genereer(oid):
    try:
        opdracht.start_genereren(oid)
    except opdracht.Stop as fout:
        flash(str(fout))
    return redirect(url_for("toon", oid=oid))


@app.post("/api/opdracht/<oid>/oordeel")
def oordeel(oid):
    d = request.get_json(force=True)
    try:
        opdracht.oordeel(oid, d["uid"], d["soort"], d.get("reden", ""))
    except (opdracht.Stop, KeyError) as fout:
        return _json_fout(fout)
    return jsonify(ok=True)


@app.post("/api/opdracht/<oid>/bewerk")
def bewerk_item(oid):
    d = request.get_json(force=True)
    velden = {k: d[k] for k in ("titel", "vraag", "voorbeeld", "uitleg") if k in d}
    if "context" in d:
        velden["context"] = [p.strip() for p in d["context"].split("\n\n") if p.strip()]
    try:
        opdracht.bewerk_item(oid, d["uid"], velden)
    except (KeyError, ValueError) as fout:
        return _json_fout(fout)
    return jsonify(ok=True)


@app.post("/opdracht/<oid>/extra")
def extra(oid):
    try:
        opdracht.extra_opgave(oid, request.form["code"], int(request.form.get("stap", 2)))
        flash("Er wordt een extra opgave gemaakt. Die verschijnt hieronder zodra hij klaar en beoordeeld is.")
    except (opdracht.Stop, KeyError, ValueError) as fout:
        flash(str(fout))
    return redirect(url_for("toon", oid=oid) + f"#ld-{request.form.get('code', '')}")


@app.post("/opdracht/<oid>/klaarzetten")
def klaarzetten(oid):
    try:
        opdracht.start_klaarzetten(oid)
    except opdracht.Stop as fout:
        flash(str(fout))
    return redirect(url_for("toon", oid=oid))


@app.post("/opdracht/<oid>/verwijder")
def verwijder(oid):
    try:
        opdracht.verwijder(oid)
        flash("De opdracht is verwijderd. De opgaven blijven in de itembank.")
    except opdracht.Stop as fout:
        flash(str(fout))
        return redirect(url_for("toon", oid=oid))
    return redirect(url_for("index"))


@app.get("/opdracht/<oid>/bestand/<path:pad>")
def bestand(oid, pad):
    _opdracht_of_404(oid)
    return send_from_directory(OPDRACHTEN / oid, pad, as_attachment=not pad.endswith(".html"))


@app.get("/opdracht/<oid>/print")
def printen(oid):
    """Printversie met namen: de namen komen alleen hier op het scherm, nergens in een bestand."""
    o = _opdracht_of_404(oid)
    kies = request.args.get("set")
    sets = [s for s in o["sets"] if not kies or s["naam"] == kies]
    met_uitwerking = request.args.get("uitwerkingen") == "1"
    namen = te.namen(o["leerling_ids"])
    alle = opslag.items_van(sorted({u for s in sets for u in s.get("items", [])}))
    reg = te.alle_codes()
    bladen = []
    for s in sets:
        ontvangers = s["leerlingen"] if request.args.get("perleerling") == "1" or s["soort"] == "leerling" else [None]
        for lid in ontvangers:
            bladen.append({"set": s, "lid": lid, "naam": namen.get(lid, "") if lid else "",
                           "items": [alle[u] for u in s.get("items", []) if u in alle]})
    return render_template("print.html", o=o, bladen=bladen, reg=reg, met_uitwerking=met_uitwerking)


@app.get("/opdracht/<oid>/codes")
def codes(oid):
    o = _opdracht_of_404(oid)
    namen = te.namen(o["leerling_ids"])
    rijen = sorted(({"lid": lid, "naam": namen.get(lid, ""), "code": c} for lid, c in o.get("codes_leerling", {}).items()),
                   key=lambda r: (r["naam"] or "~", r["lid"]))
    return render_template("codes.html", o=o, rijen=rijen)


@app.get("/opdracht/<oid>/leerloop")
def leerloop_pagina(oid):
    o = _opdracht_of_404(oid)
    try:
        st = leerloop.stand(o)
        ex = leerloop.gescande_oefentoetsen(o)
    except te.GeenToetsevaluatie as fout:
        flash(str(fout))
        return redirect(url_for("toon", oid=oid))
    vervolg = [x for x in opdracht.alle() if x.get("leerloop", {}).get("vorige") == oid]
    return render_template("leerloop.html", o=o, st=st, ex=ex, reg=te.alle_codes(), vervolg=vervolg,
                           zc=zelfcheck.overzicht(o))


@app.post("/opdracht/<oid>/zelfcheck")
def zelfcheck_verwerk(oid):
    o = _opdracht_of_404(oid)
    uit = zelfcheck.verwerk(o, request.form.get("codes", ""))
    if not uit["gevonden"]:
        flash("Er staat geen resultaatcode in de geplakte tekst. Een code begint met OEF- en eindigt op vier cijfers.")
    else:
        flash(f"{uit['nieuw']} resultaatcode(s) verwerkt" + (f", {uit['dubbel']} al eerder ontvangen" if uit["dubbel"] else "")
              + ". " + " ".join(uit["meldingen"]))
    return redirect(url_for("leerloop_pagina", oid=oid) + "#zelfcheck")


@app.post("/opdracht/<oid>/digitaal")
def digitaal_opnieuw(oid):
    try:
        n = len(opdracht.maak_digitaal(oid))
        flash(f"{n} oefenpagina('s) opnieuw gemaakt, met de resultaatcode aan het eind. Deel deze nieuwe bestanden.")
    except opdracht.Stop as fout:
        flash(str(fout))
    return redirect(url_for("toon", oid=oid))


@app.post("/opdracht/<oid>/leerloop/resultaten")
def leerloop_resultaten(oid):
    o = _opdracht_of_404(oid)
    n = leerloop.resultaten_terug(o)
    flash(f"{n} opgaven hebben nu een P-waarde en Rit uit de oefentoets." if n else
          "Er zijn nog geen gescande oefentoetsen van deze opdracht gevonden in de Toetsevaluatie.")
    return redirect(url_for("leerloop_pagina", oid=oid))


@app.post("/opdracht/<oid>/volgende")
def volgende(oid):
    try:
        nieuw_id = leerloop.volgende_ronde(oid)
    except opdracht.Stop as fout:
        flash(str(fout))
        return redirect(url_for("leerloop_pagina", oid=oid))
    return redirect(url_for("toon", oid=nieuw_id))


# ---------------------------------------------------------------------------- figuren
@app.get("/figuur/<uid>.png")
def figuur_png(uid):
    it = opslag.item(uid)
    p = figuur.pad_voor(uid, it["inhoud"]) if it else None
    if not p:
        abort(404)
    return send_from_directory(p.parent, p.name, max_age=3600)


# ---------------------------------------------------------------------------- dashboard
@app.get("/dashboard")
def dashboard_pagina():
    ok, waarom = te.beschikbaar()
    if not ok:
        flash(waarom)
        return redirect(url_for("index"))
    keuzes = dashboard.scopes()
    if not keuzes:
        return render_template("dashboard.html", keuzes=[], d=None)
    vak = request.args.get("vak") or keuzes[0]["vak"]
    sector = request.args.get("sector") or keuzes[0]["sector"]
    klas = request.args.get("klas", "")
    d = dashboard.klas(vak, sector, klas)
    return render_template("dashboard.html", keuzes=keuzes, d=d, trend_svg=dashboard.svg_trend(d["trend"]))


@app.post("/dashboard/opdracht")
def dashboard_opdracht():
    f = request.form
    doel = f.get("doel", "klas")
    try:
        oid = opdracht.maak("", f.getlist("toets"), doel, [k for k in [f.get("klas", "")] if k], [], f.getlist("code"),
                            {"vormen": ["papier", "digitaal"], "minuten": 15 if doel == "onderhoud" else 40,
                             "oefenexamen": doel != "onderhoud", "groepen": 3, "max_leerdoelen": 10, "examenminuten": 30},
                            naam=f.get("naam", ""))
    except (opdracht.Stop, ValueError) as fout:
        flash(str(fout))
        return redirect(request.referrer or url_for("dashboard_pagina"))
    return redirect(url_for("toon", oid=oid))


@app.get("/leerling/<lid>")
def leerling_pagina(lid):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", lid):
        abort(404)
    p = dashboard.leerling(lid)
    if not p:
        abort(404)
    naam = te.namen([lid]).get(lid, "") if request.args.get("print") == "1" else ""
    return render_template("leerling.html", p=p, naam=naam, printen=request.args.get("print") == "1",
                           reg=te.alle_codes())


# ---------------------------------------------------------------------------- bibliotheek
@app.get("/bibliotheek")
def bibliotheek_pagina():
    ov = bibliotheek.overzicht()
    dekking = {}
    ok, _ = te.beschikbaar()
    if ok:
        for vak, sec in (("Economie", "havo-vwo"), ("Bedrijfseconomie", "havo-vwo"), ("Economie", "mavo")):
            try:
                d = bibliotheek.dekking(vak, sec)
            except Exception:  # noqa: BLE001
                d = []
            if d:
                dekking[f"{vak} {sec}"] = d
    p = PRIJZEN.get(laad_instellingen().get("model_index"), PRIJZEN["claude-sonnet-5-5"])
    return render_template("bibliotheek.html", ov=ov, job=bibliotheek.JOB, dekking=dekking, batches=bibliotheek.batches()[-5:],
                           prijs_in=p[0], prijs_uit=p[3], index_model=MODELLEN.get(laad_instellingen().get("model_index"), ""),
                           inst=laad_instellingen(), eigen=str(EIGEN_BRONNEN))


@app.post("/bibliotheek/scan")
def bibliotheek_scan():
    uit = bibliotheek.scan()
    flash(f"{uit['gevonden']} bestanden gevonden: {uit['nieuw']} nieuw, {uit['gewijzigd']} gewijzigd, "
          f"{uit['verdwenen']} verdwenen.")
    return redirect(url_for("bibliotheek_pagina"))


@app.post("/bibliotheek/indexeer")
def bibliotheek_indexeer():
    ids = [int(x) for x in request.form.getlist("bron") if x.isdigit()]
    if not ids:
        flash("Vink eerst de bestanden of mappen aan die Claude moet lezen.")
    elif not claude.kan_werken() or (request.form.get("batch") and not claude.heeft_sleutel()):
        flash("Er is geen API-sleutel. Start de oefentool via 'Start Oefentool.command'.")
    elif request.form.get("batch"):
        try:
            uit = bibliotheek.start_batch(ids)
            flash(f"{uit['bestanden']} bestand(en) staan in de wachtrij bij Anthropic (batch, halve prijs). Meestal klaar "
                  "binnen een uur, uiterlijk binnen een dag. Laat de oefentool aan staan of start hem later opnieuw: "
                  "hij haalt de uitkomsten dan vanzelf op." + (f" {uit['overgeslagen']} overgeslagen (niet leesbaar)." if uit["overgeslagen"] else ""))
        except Exception as fout:  # noqa: BLE001
            flash(f"De batch kon niet worden gestart: {fout}")
    elif not bibliotheek.start_indexeren(ids):
        flash("Claude is al bezig met lezen. Wacht tot dat klaar is.")
    return redirect(url_for("bibliotheek_pagina"))


@app.get("/api/bibliotheek/status")
def bibliotheek_status():
    return jsonify(bibliotheek.JOB)


@app.post("/bibliotheek/drive")
def bibliotheek_drive():
    try:
        n = bibliotheek.schrijf_drive()
        flash(f"De bibliotheek in Google Drive is bijgewerkt: {n} leerdoelen.")
    except Exception as fout:  # noqa: BLE001
        flash(f"Bijwerken in Google Drive mislukt: {fout}")
    return redirect(url_for("bibliotheek_pagina"))


@app.post("/bibliotheek/upload")
def bibliotheek_upload():
    vak = request.form.get("vak", "Economie")
    niveau = request.form.get("niveau", "havo")
    doel = EIGEN_BRONNEN / ("VAK_BEDRIJFSECONOMIE" if vak == "Bedrijfseconomie" else "VAK_ECONOMIE") / niveau.upper()
    doel.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in request.files.getlist("bestanden"):
        naam = Path(f.filename or "").name
        if naam and Path(naam).suffix.lower() in (".pdf", ".docx", ".pptx", ".txt", ".md", ".html"):
            f.save(str(doel / naam))
            n += 1
    uit = bibliotheek.scan()
    flash(f"{n} bestand(en) toegevoegd aan de bibliotheek ({uit['nieuw']} nieuw). Vink ze aan en laat Claude ze lezen.")
    return redirect(url_for("bibliotheek_pagina"))


@app.post("/bibliotheek/instellingen")
def bibliotheek_instellingen():
    inst = laad_instellingen()
    mappen = [m.strip() for m in request.form.get("bronmappen", "").splitlines() if m.strip()]
    inst["bronmappen"] = [m for m in mappen if m != str(EIGEN_BRONNEN)]
    if request.form.get("bibliotheek_drive", "").strip():
        inst["bibliotheek_drive"] = request.form["bibliotheek_drive"].strip()
    bewaar_instellingen({k: inst[k] for k in ("bronmappen", "bibliotheek_drive", "max_tekens_per_bron", "model_index", "model_kritiek", "motor", "abonnement_model")})
    flash("Instellingen bewaard. Klik op 'Bronnen zoeken' om de lijst bij te werken.")
    return redirect(url_for("bibliotheek_pagina"))


# ---------------------------------------------------------------------------- itembank en kwaliteit
@app.get("/itembank")
def itembank():
    code = request.args.get("code", "")
    st = request.args.get("status", "goedgekeurd")
    sql, arg = "SELECT * FROM items WHERE 1=1", []
    if code:
        sql += " AND leerdoel_code = ?"
        arg.append(code)
    if st:
        sql += " AND status = ?"
        arg.append(st)
    if request.args.get("zwak"):
        sql += " AND p_waarde IS NOT NULL AND (p_waarde < 0.3 OR rit < 0.15)"
    with opslag.db() as con:
        rijen = [opslag.item_uit_rij(r) for r in con.execute(sql + " ORDER BY leerdoel_code, stap, item_id DESC LIMIT 400", arg)]
        codes = [r["leerdoel_code"] for r in con.execute("SELECT DISTINCT leerdoel_code FROM items ORDER BY 1")]
    return render_template("itembank.html", items=rijen, codes=codes, code=code, st=st, reg=te.alle_codes() if te.beschikbaar()[0] else {})


@app.post("/api/item/<uid>/status")
def item_status(uid):
    d = request.get_json(force=True)
    if d.get("status") not in ("goedgekeurd", "afgekeurd"):
        return _json_fout("onbekende status")
    opslag.zet_item(uid, status=d["status"])
    return jsonify(ok=True)


@app.get("/kwaliteit")
def kwaliteit_pagina():
    regels = claude.kwaliteitsregels(alleen_actief=False)
    with opslag.db() as con:
        gebruik = [dict(r) for r in con.execute(
            "SELECT soort || CASE WHEN model LIKE 'abonnement%' THEN ' (abonnement)' ELSE '' END AS soort, COUNT(*) AS n, SUM(invoer + COALESCE(cache_schrijf, 0)) AS invoer, SUM(COALESCE(cache_lees, 0)) AS lees,"
            f" SUM(uitvoer) AS uitvoer, SUM({opslag.KOSTEN_SQL}) AS kosten FROM logboek GROUP BY 1 ORDER BY kosten DESC")]
        per_opdracht = [dict(r) for r in con.execute(
            f"SELECT opdracht, SUM({opslag.KOSTEN_SQL}) AS kosten, COUNT(*) AS n FROM logboek WHERE opdracht != '' "
            "GROUP BY opdracht ORDER BY MIN(moment) DESC LIMIT 15")]
    namen = {o["id"]: o["naam"] for o in opdracht.alle()}
    for r in per_opdracht:
        r["naam"] = namen.get(r["opdracht"], r["opdracht"])
    inst = laad_instellingen()
    return render_template("kwaliteit.html", regels=list(reversed(regels)), curve=kwaliteit.leercurve(), gebruik=gebruik,
                           per_opdracht=per_opdracht, totaal=sum(g["kosten"] or 0 for g in gebruik), inst=inst,
                           MODELLEN=MODELLEN, MOTOREN=MOTOREN, abo=claude.abonnement_status())


@app.post("/kwaliteit/modellen")
def kwaliteit_modellen():
    inst = laad_instellingen()
    for k in ("model_index", "model_kritiek"):
        if request.form.get(k) in MODELLEN:
            inst[k] = request.form[k]
    if request.form.get("motor") in MOTOREN:
        inst["motor"] = request.form["motor"]
    if request.form.get("abonnement_model") in ("sonnet", "opus"):
        inst["abonnement_model"] = request.form["abonnement_model"]
    bewaar_instellingen({k: inst[k] for k in ("bronmappen", "bibliotheek_drive", "max_tekens_per_bron", "model_index", "model_kritiek", "motor", "abonnement_model")})
    flash("Bewaard. Het nieuwe model geldt voor de volgende verzoeken.")
    return redirect(url_for("kwaliteit_pagina") + "#kosten")


@app.post("/api/regel")
def regel():
    d = request.get_json(force=True)
    regels = claude.kwaliteitsregels(alleen_actief=False)
    for r in regels:
        if r.get("id") == d.get("id"):
            if d.get("actie") == "activeer":
                r["status"] = "actief"
            elif d.get("actie") == "verwijder":
                r["status"] = "verwijderd"
            elif d.get("actie") == "wijzig" and d.get("regel", "").strip():
                r["regel"] = d["regel"].strip()
    if d.get("actie") == "nieuw" and d.get("regel", "").strip():
        kwaliteit.nieuwe_regel(d.get("vak", ""), d.get("niveau", ""), d["regel"], "Zelf toegevoegd door de docent",
                               "docent", "actief", "vak_en_niveau" if d.get("niveau") else ("vak" if d.get("vak") else "alle"))
        return jsonify(ok=True)
    claude.bewaar_regels(regels)
    return jsonify(ok=True)


# ---------------------------------------------------------------------------- starten
def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG, encoding="utf-8"), logging.StreamHandler()])
    for luid in ("httpx", "httpcore", "anthropic", "werkzeug"):
        logging.getLogger(luid).setLevel(logging.WARNING)
    opslag.maak()
    ok, waarom = te.beschikbaar()
    if not ok:
        log.warning("Toetsevaluatie niet beschikbaar: %s", waarom)
    for o in opdracht.alle():                      # na een herstart: niets staat meer 'bezig'
        if o["status"] in ("plan", "genereren", "klaarzetten") and not o.get("bezig"):
            with opdracht.bewerk(o["id"]) as o2:
                o2["status"] = {"plan": "plan_fout", "genereren": "genereren_fout", "klaarzetten": "beoordelen"}[o["status"]]
                o2["fout"] = "De app is gestopt terwijl deze stap liep. Start de stap opnieuw; wat klaar was, blijft bewaard."
        if o.get("in_bewerking"):
            with opdracht.bewerk(o["id"]) as o2:
                o2["in_bewerking"] = []
    bibliotheek.start_poller()                     # batches die nog liepen: uitkomsten ophalen
    if os.environ.get("OEFENTOOL_GEEN_BROWSER") != "1":
        webbrowser.open(f"http://127.0.0.1:{POORT}/")
    app.run(host="127.0.0.1", port=POORT, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
