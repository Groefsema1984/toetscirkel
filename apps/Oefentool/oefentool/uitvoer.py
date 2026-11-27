# -*- coding: utf-8 -*-
"""Ronde 3: de bestanden.

- Papier: per set Word-bestanden in de toetsstijl van de docent (bouwmotor uit Toetsconstructie):
  opgaven en uitwerkingen (correctievoorschrift + nabespreekversie).
- Digitaal: zelfstandige HTML-oefenpagina's met hints, directe controle en zelf nakijken. Bij
  'per leerling' één klaspagina waarop elke leerling met een persoonlijke code zijn eigen set opent.
  Er staan geen namen of leerlingnummers in.
- Oefenexamen per groep: opgaven + correctievoorschrift met toetsmatrijs en leerdoelcodes, klaar om
  na het maken te scannen in de Toetsevaluatie als oefentoets (soort OE).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import doelgroep, figuur, opslag, te
from .instellingen import BASIS, OPDRACHTEN, PAKKET, TOETSCONSTRUCTIE

log = logging.getLogger("oefentool")
BOUW = BASIS / "bouw"
MAANDEN = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
           "oktober", "november", "december"]
_env = Environment(loader=FileSystemLoader(str(PAKKET / "templates")), autoescape=select_autoescape(["html"]))


def _datum() -> str:
    d = date.today()
    return f"{d.day} {MAANDEN[d.month - 1]} {d.year}"


def _veilig(t: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", t).strip("_")[:60] or "set"


# ---------------------------------------------------------------------------- bouwmotor
def node() -> str | None:
    kandidaten = [os.environ.get("OEFENTOOL_NODE"), str(TOETSCONSTRUCTIE / ".node" / "bin" / "node"),
                  shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node"]
    return next((k for k in kandidaten if k and Path(k).exists()), None)


def bouwmotor_klaar() -> tuple[bool, str]:
    if not (BOUW / "node_modules" / "docx").exists():
        bron = TOETSCONSTRUCTIE / "bouw" / "node_modules"
        if bron.exists():
            shutil.copytree(bron, BOUW / "node_modules", dirs_exist_ok=True)
    if not node():
        return False, "Node.js niet gevonden (nodig voor de Word-bestanden)."
    if not (BOUW / "node_modules" / "docx").exists():
        return False, "De bouwmotor van Toetsconstructie (bouw/node_modules) is niet gevonden."
    return True, ""


def bouw_word(toets: dict, map_: Path) -> list[str]:
    """Schrijft toets.json en bouwt de Word-bestanden. Geeft de bestandsnamen."""
    map_.mkdir(parents=True, exist_ok=True)
    for naam, bron in (toets.pop("_figuren", None) or {}).items():
        shutil.copy(bron, map_ / naam)
    pad = map_ / "toets.json"
    pad.write_text(json.dumps(toets, ensure_ascii=False, indent=1), encoding="utf-8")
    ok, melding = bouwmotor_klaar()
    if not ok:
        return _bouw_python(toets, map_, melding)
    n = node()
    c = subprocess.run([n, str(BOUW / "controle.js"), str(pad)], capture_output=True, text=True, timeout=120)
    if c.returncode != 0:
        try:
            fouten = json.loads(c.stdout).get("fouten", [])
        except json.JSONDecodeError:
            fouten = [c.stdout[-400:] or c.stderr[-400:]]
        raise RuntimeError("Controle van de bouwmotor niet akkoord: " + "; ".join(fouten))
    b = subprocess.run([n, str(BOUW / "build.js"), str(pad), str(map_)], capture_output=True, text=True, timeout=300)
    if b.returncode != 0:
        raise RuntimeError("Bouwfout: " + (b.stderr or b.stdout)[-400:])
    verslag = json.loads((map_ / f"bouwverslag_{toets['meta']['versie']}.json").read_text(encoding="utf-8"))
    try:
        subprocess.run([sys.executable, str(BOUW / "leesverder.py"), str(map_ / verslag["opgaven_bestand"])],
                       capture_output=True, timeout=60)
    except Exception:  # noqa: BLE001
        pass
    return verslag["bestanden"]


def _bouw_python(toets: dict, map_: Path, reden: str) -> list[str]:
    """Noodweg zonder Node.js: eenvoudige Word-bestanden met python-docx."""
    from docx import Document
    from docx.shared import Pt
    M = toets["meta"]
    uit = []
    for soort in ("opgaven", "correctievoorschrift"):
        doc = Document()
        doc.styles["Normal"].font.name, doc.styles["Normal"].font.size = "Arial", Pt(11)
        doc.add_heading(f"{M['titel']} – {soort}", 1)
        doc.add_paragraph(f"{M['klas']} · {M['onderwerp']} · {M['datum']}  ({reden})")
        if soort == "opgaven":
            for o in toets["opgaven"]:
                doc.add_heading(o["titel"], 2)
                for it in o["items"]:
                    if it["t"] == "p":
                        doc.add_paragraph(it["text"])
                    elif it["t"] == "tab":
                        tab = doc.add_table(rows=0, cols=len(it["rows"][0]))
                        tab.style = "Table Grid"
                        for r in ([it["head"]] if it.get("head") else []) + it["rows"]:
                            cellen = tab.add_row().cells
                            for i, w in enumerate(r[:len(cellen)]):
                                cellen[i].text = str(w)
                    elif it["t"] == "fig":
                        from docx.shared import Inches
                        doc.add_picture(str(map_ / it["file"]), width=Inches(4.5))
                    elif it["t"] == "q":
                        doc.add_paragraph(f"{it['p']}p   {it['nr']}   {it['text']}")
                        for e in it.get("extra", []):
                            doc.add_paragraph(e)
        else:
            if M.get("leerdoelcodes"):
                doc.add_paragraph("LEERDOELCODES: " + M["leerdoelcodes"])
            for v in toets["cv"]:
                if v.get("opgave"):
                    doc.add_heading(v["opgave"], 2)
                    continue
                doc.add_paragraph(f"{v['nr']}   maximumscore {v['max']}")
                if v.get("intro"):
                    doc.add_paragraph(v["intro"])
                for t, s in v["bullets"]:
                    doc.add_paragraph(f"• {t}   {s}")
                doc.add_paragraph(f"Voorbeeldantwoord: {v['voorbeeld']}")
            tab = doc.add_table(rows=1, cols=5)
            tab.style = "Table Grid"
            for i, k in enumerate(["Nr", "Leerdoel", "Eindterm", "Leerdoelcode", "P"]):
                tab.rows[0].cells[i].text = k
            for r in toets["matrijs"]["rijen"]:
                c = tab.add_row().cells
                for i, w in enumerate([r["nr"], r["leerdoel"], r["eindterm"], r["koppeling"], r["punten"]]):
                    c[i].text = str(w)
        naam = f"{M['bestandsbasis']}_{soort}.docx"
        doc.save(str(map_ / naam))
        uit.append(naam)
    return uit


# ---------------------------------------------------------------------------- toetsbestand maken
def _klasnaam(o: dict) -> str:
    try:
        i = te.info(o["toets_ids"][0])
        nr = re.sub(r"\D", "", i.klas) or ""
        return f"{nr} {i.niveau}".strip() if i.niveau != "mavo" else f"{nr or ''} mavo".strip()
    except Exception:  # noqa: BLE001
        return o["niveau"]


def _normaliseer(inh: dict) -> dict:
    """Deelscores en punten gelijk trekken, zodat de controle van de bouwmotor akkoord is."""
    inh = json.loads(json.dumps(inh))
    regels = (inh.get("cv") or {}).get("regels") or [{"tekst": "juist antwoord", "punten": inh.get("punten", 1)}]
    per_fout = len(regels) == 1 and "per gemaakte fout" in regels[0]["tekst"].lower()
    indien = all(r["tekst"].lower().startswith("indien") for r in regels)
    if indien:
        inh["punten"] = max(r["punten"] for r in regels)
    elif not per_fout:
        inh["punten"] = sum(r["punten"] for r in regels) or 1
    inh.setdefault("cv", {})["regels"] = regels
    inh["punten"] = max(1, int(inh["punten"]))
    return inh


def toets_json(o: dict, items: list[dict], *, titel: str, soortnaam: str, code: str, basis: str,
               onderwerp: str, minuten: int, voorblad: list | None, cvzin: str | None, examen: bool,
               kort: str = "") -> dict:
    codes = te.alle_codes()
    opgaven, cv, rijen, ld_codes, figuren = [], [], [], [], {}
    for nr, it in enumerate(items, 1):
        inh = _normaliseer(it["inhoud"])
        titel_o = f"Opgave {nr}  {inh.get('titel') or 'Oefenopgave'}"
        regels = [{"t": "p", "text": p} for p in inh.get("context", []) if p.strip()]
        tab = inh.get("tabel") or {}
        if tab.get("rijen"):
            breed = max(len(r) for r in tab["rijen"] + ([tab["kop"]] if tab.get("kop") else []))
            rows = [r + [""] * (breed - len(r)) for r in tab["rijen"]]
            regels.append({"t": "tab", "titel": tab.get("titel") or "Tabel", "rows": rows,
                           **({"head": (tab["kop"] + [""] * (breed - len(tab["kop"])))} if tab.get("kop") else {})})
        extra = list(inh.get("extra", []))
        if inh.get("soort") == "meerkeuze" and inh.get("opties"):
            extra += [f"{chr(65 + i)}   {opt}" for i, opt in enumerate(inh["opties"])]
        fig = figuur.pad_voor(it["uid"], it["inhoud"])
        if fig:
            w, h = figuur.GROOTTE[it["inhoud"]["figuur"]["soort"]]
            regels.append({"t": "fig", "titel": it["inhoud"]["figuur"].get("titel") or "Figuur", "file": fig.name, "w": w, "h": h})
            figuren[fig.name] = str(fig)
        regels.append({"t": "q", "nr": nr, "p": inh["punten"], "text": inh["vraag"], **({"extra": extra} if extra else {})})
        opgaven.append({"titel": titel_o, "doorlopend": nr > 1, "items": regels})
        label = "V" if (o.get("tweefasen") and re.search(r"vwo", inh.get("eindterm", ""), re.I)
                        and not re.search(r"havo", inh.get("eindterm", ""), re.I)) else "HV"
        bullets = [[r["tekst"], r["punten"]] for r in inh["cv"]["regels"]]
        if inh.get("soort") == "meerkeuze" and inh.get("opties") and 0 <= inh.get("juist", -1) < len(inh["opties"]):
            bullets = [[f"{chr(65 + inh['juist'])}", inh["punten"]]]
        rij_cv = {"nr": nr, "max": inh["punten"], "bloom": inh.get("bloom") or "Toepassen",
                  "intro": inh["cv"].get("intro") or "Een voorbeeld van een juist antwoord is:",
                  "bullets": bullets, "voorbeeld": inh.get("voorbeeld") or "-", "uitleg": inh.get("uitleg") or "-"}
        for k in ("opm", "fout"):
            if inh["cv"].get(k):
                rij_cv[k] = inh["cv"][k]
        if o.get("tweefasen"):
            rij_cv["label"] = label
        cv += [{"opgave": titel_o}, rij_cv]
        ld = codes.get(it["leerdoel_code"], {})
        rijen.append({"nr": nr, "onderdeel": ld.get("thema") or "Oefenen", "opgave": nr,
                      "leerdoel": ld.get("omschrijving") or it["leerdoel_code"], "eindterm": inh.get("eindterm") or "-",
                      "werkwoord": (inh.get("werkwoord") or inh["vraag"].split()[0]).capitalize(),
                      "bloom": rij_cv["bloom"], "koppeling": it["leerdoel_code"], "thema": inh.get("titel", ""),
                      "bron": "–", "punten": inh["punten"], **({"label": label} if o.get("tweefasen") else {})})
        ld_codes.append(f"{nr}={it['leerdoel_code']}")
    niveau_kort = {"havo": "Havo", "vwo": "Vwo", "mavo": "Mavo"}.get(o["niveau"], o["niveau"])
    meta = {"vak": o["vak"], "niveau": niveau_kort, "klas": _klasnaam(o), "titel": titel, "onderwerp": onderwerp,
            "kort": (kort or onderwerp)[:60], "soortnaam": soortnaam, "at": code, "datum": _datum(), "maker": "GRA",
            "wegingtekst": "Telt niet mee voor je cijfer." if not examen else
            "Deze oefentoets telt niet mee voor je cijfer. De uitslag laat zien welke leerdoelen je nu beheerst.",
            "weging": "0%", "minuten": minuten, "versie": "A", "tweefasen": bool(o.get("tweefasen")),
            "bestandsbasis": basis, "koppelkop": "Leerdoelcode", "leerdoelcodes": "; ".join(ld_codes)}
    if voorblad:
        meta["voorblad"] = voorblad
    if cvzin:
        meta["cvzin"] = cvzin
    return {"meta": meta, "opgaven": opgaven, "cv": cv, "matrijs": {"rijen": rijen, "gevraagd": {}}, "_figuren": figuren}


# ---------------------------------------------------------------------------- per onderdeel
def _items_lijst(uids: list[str]) -> list[dict]:
    alle = opslag.items_van(uids)
    return [alle[u] for u in uids if u in alle]


def _leerdoel_zinnen(items: list[dict]) -> list[dict]:
    codes = te.alle_codes()
    gezien, uit = set(), []
    for it in items:
        c = it["leerdoel_code"]
        if c not in gezien:
            gezien.add(c)
            uit.append({"code": c, "zin": codes.get(c, {}).get("leerling_zin") or codes.get(c, {}).get("omschrijving", c)})
    return uit


def papier(o: dict, s: dict, doel: Path) -> list[str]:
    items = _items_lijst(s["items"])
    if not items:
        return []
    zinnen = _leerdoel_zinnen(items)
    voor = "Leerling " + s["naam"] if s["soort"] == "leerling" else s["naam"]
    voorblad = [["Vak", o["vak"]], ["Klas", _klasnaam(o)], ["Voor", voor],
                ["Je oefent", "\n".join(z["zin"] for z in zinnen)],
                ["Tijd", f"Ongeveer {s.get('minuten') or o['instellingen']['minuten']} minuten."],
                ["Aantal opgaven", f"{len(items)} opgaven, van basis naar examenniveau."],
                ["Zo werk je", "Maak de opgaven op volgorde. Schrijf bij rekenopgaven de hele berekening op. "
                               "Kijk daarna na met de uitwerkingen en schrijf op wat je anders moet doen."],
                ["Telt mee?", "Nee. Dit is oefenmateriaal."]]
    basis = _veilig(f"Oefenset_{s['naam']}")
    toets = toets_json(o, items, titel=f"Oefenset {o['vak'].lower()}", soortnaam="oefenset", code="Oefenset", kort=voor,
                       basis=basis, onderwerp=", ".join(z["code"] for z in zinnen), minuten=s.get("minuten") or 45,
                       voorblad=voorblad, cvzin="Uitwerkingen bij de oefenset. Deel A is voor de docent (deelscores), "
                                                "deel B is de nabespreekversie voor de leerling.", examen=False)
    return [str((doel / f).relative_to(OPDRACHTEN / o["id"])) for f in bouw_word(toets, doel)]


def _item_voor_web(it: dict, zinnen: dict) -> dict:
    inh = _normaliseer(it["inhoud"])
    return {"uid": it["uid"], "code": it["leerdoel_code"], "zin": zinnen.get(it["leerdoel_code"], ""),
            "stap": it["stap"], "titel": inh.get("titel", ""), "context": inh.get("context", []),
            "tabel": inh.get("tabel") or {}, "vraag": inh["vraag"], "extra": inh.get("extra", []),
            "soort": inh.get("soort", "open"), "punten": inh["punten"], "opties": inh.get("opties", []),
            "juist": inh.get("juist", -1), "antwoord": inh.get("antwoord_kort", ""), "tolerantie": inh.get("tolerantie", 0),
            "cv": inh.get("cv", {}), "voorbeeld": inh.get("voorbeeld", ""), "uitleg": inh.get("uitleg", ""),
            "hints": inh.get("hints", [])[:2], "figuur": figuur.data_uri(it["uid"], it["inhoud"]),
            "figuurtitel": (it["inhoud"].get("figuur") or {}).get("titel", "")}


def digitaal(o: dict, sets: list[dict], doel: Path, persoonlijk: dict[str, str] | None) -> list[str]:
    """Eén pagina per set, of bij 'per leerling' één pagina met persoonlijke codes."""
    doel.mkdir(parents=True, exist_ok=True)
    codes = te.alle_codes()
    zinnen = {c: d.get("leerling_zin") or d.get("omschrijving", c) for c, d in codes.items()}
    uit = []
    tpl = _env.get_template("oefenpagina.html")

    def schrijf(naam: str, titel: str, data: dict):
        tekst = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "\\u003c!--")
        html = tpl.render(titel=titel, vak=o["vak"], klas=_klasnaam(o), data=tekst)
        (doel / naam).write_text(html, encoding="utf-8")
        uit.append(str((doel / naam).relative_to(OPDRACHTEN / o["id"])))

    if persoonlijk:
        alle_uids = sorted({u for s in sets for u in s["items"]})
        items = {u: _item_voor_web(it, zinnen) for u, it in opslag.items_van(alle_uids).items()}
        data = {"modus": "code", "opdracht": o["id"], "items": items,
                "sets": {persoonlijk[s["naam"]]: {"naam": "Jouw oefenset", "items": s["items"]}
                         for s in sets if s["naam"] in persoonlijk and s["items"]}}
        schrijf(f"Oefenpagina_{_veilig(o['klas'] or o['niveau'])}_persoonlijk.html",
                f"Oefenen {o['vak'].lower()}", data)
    else:
        for s in sets:
            if not s["items"]:
                continue
            items = {u: _item_voor_web(it, zinnen) for u, it in opslag.items_van(s["items"]).items()}
            data = {"modus": "vast", "opdracht": o["id"], "items": items, "sets": {"vast": {"naam": s["naam"], "items": s["items"]}}}
            schrijf(f"Oefenpagina_{_veilig(s['naam'])}.html", f"Oefenen {o['vak'].lower()}: {s['naam']}", data)
    return uit


def oefenexamens(o: dict, doel: Path) -> list[dict]:
    from .opdracht import volgnummer_oefenexamen
    prof = te.profielen(o["toets_ids"], set(o["leerling_ids"]), set(o["codes"]) or None)
    groepen = doelgroep.examengroepen(o, prof, o["doelen"])
    alle = opslag.items_van([u for us in o["pool"].values() for u in us])
    per = max(1, int(o["plan"]["oefenexamen"]["vragen_per_leerdoel"]))
    nr = volgnummer_oefenexamen()
    try:
        i = te.info(o["toets_ids"][0])
        te_klas, te_vak = i.klas, i.vak
    except Exception:  # noqa: BLE001
        te_klas, te_vak = o["klas"], o["vak"]
    uit = []
    for gi, g in enumerate(groepen):
        geoefend = set()
        for s in o["sets"]:
            if set(s["leerlingen"]) & set(g["leerlingen"]):
                geoefend |= set(s.get("items", []))
        gekozen, minuten = [], 0.0
        for d in g["doelen"]:
            kand = [alle[u] for u in o["pool"].get(d["code"], []) if u in alle and alle[u]["status"] == "goedgekeurd"]
            toets = [it for it in kand if it["stap"] == 4] + [it for it in kand if it["stap"] == 3 and it["uid"] not in geoefend]
            for it in toets[:per]:
                m = it["inhoud"].get("punten", 2) * 3.2
                if gekozen and minuten + m > o["instellingen"]["examenminuten"] * 1.15:
                    break
                gekozen.append(it)
                minuten += m
        if not gekozen:
            continue
        letter = chr(65 + gi)
        code = f"OEF{nr}{letter}"
        toets_id = f"{code}_OE_{te_klas}_{te_vak}" + ("_tweefasen" if o.get("tweefasen") else "")
        map_ = doel / _veilig(f"{g['naam']}_{code}")
        punten = sum(_normaliseer(it["inhoud"])["punten"] for it in gekozen)
        minuten_tot = max(10, int(round(punten * 3.2 / 5.0) * 5))
        tj = toets_json(o, gekozen, titel=f"Oefentoets {o['vak'].lower()}", soortnaam="oefentoets", code=code,
                        basis=toets_id, onderwerp=", ".join(sorted({it['leerdoel_code'] for it in gekozen})),
                        minuten=minuten_tot, voorblad=None, cvzin=None, examen=True)
        bestanden = bouw_word(tj, map_)
        instructie = (f"Oefentoets {code} voor {g['naam']} ({len(g['leerlingen'])} leerlingen)\n\n"
                      f"Na het maken: scan het werk van deze groep als één PDF en open de Toetsevaluatie.\n"
                      f"- Soort: Oefentoets (oefentool)\n- Toetscode: {code}\n- Klas: {te_klas}\n- Vak: {te_vak}\n"
                      f"- Toetsnaam wordt dan: {toets_id}\n"
                      f"- Correctievoorschrift: {toets_id}_correctievoorschrift.docx uit deze map\n\n"
                      f"De leerdoelcodes staan in het correctievoorschrift en worden direct overgenomen. De resultaten "
                      f"tellen als meting met het label 'oefen'. Daarna zie je in de oefentool onder Leerloop wie "
                      f"de leerdoelen nu beheerst.\n")
        (map_ / "SCANINSTRUCTIE.txt").write_text(instructie, encoding="utf-8")
        uit.append({"groep": g["naam"], "code": code, "toets_id": toets_id, "leerlingen": g["leerlingen"],
                    "items": [it["uid"] for it in gekozen], "punten": punten, "minuten": minuten_tot,
                    "bestanden": [str((map_ / f).relative_to(OPDRACHTEN / o["id"])) for f in bestanden + ["SCANINSTRUCTIE.txt"]]})
    return uit


def bouw_alles(o: dict) -> dict:
    basis = OPDRACHTEN / o["id"] / "uitvoer"
    if basis.exists():
        shutil.rmtree(basis)
    uit = {"papier": [], "digitaal": [], "oefenexamens": [], "fouten": []}
    if "papier" in o["instellingen"]["vormen"]:
        for s in o["sets"]:
            try:
                uit["papier"] += papier(o, s, basis / "papier" / _veilig(s["naam"]))
            except Exception as fout:  # noqa: BLE001
                log.exception("Papier %s", s["naam"])
                uit["fouten"].append(f"Papier {s['naam']}: {fout}")
    if "digitaal" in o["instellingen"]["vormen"]:
        try:
            uit["digitaal"] = digitaal(o, o["sets"], basis / "digitaal",
                                       o.get("codes_leerling") if o["doel"] == "leerling" else None)
        except Exception as fout:  # noqa: BLE001
            log.exception("Digitaal")
            uit["fouten"].append(f"Digitaal: {fout}")
    if o["instellingen"].get("oefenexamen"):
        try:
            uit["oefenexamens"] = oefenexamens(o, basis / "oefenexamen")
        except Exception as fout:  # noqa: BLE001
            log.exception("Oefenexamen")
            uit["fouten"].append(f"Oefenexamen: {fout}")
    zip_pad = OPDRACHTEN / o["id"] / "uitvoer.zip"
    with zipfile.ZipFile(zip_pad, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(basis.rglob("*")):
            if p.is_file() and p.suffix in (".docx", ".html", ".txt"):
                z.write(p, p.relative_to(basis))
    uit["zip"] = "uitvoer.zip"
    return uit
