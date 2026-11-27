# -*- coding: utf-8 -*-
"""Kwaliteit: rekencontrole, structuurcontrole, kwaliteitsregels en de leercurve van de oefentool.

Zelflerend in drie lussen:
1. Zelfkritiek: elke opgave wordt door een tweede, strenge Claude-beurt beoordeeld en zo nodig herzien.
   Fouten die vaker terugkomen worden voorgesteld als kwaliteitsregel.
2. Docent: elke afkeuring of aanpassing met een reden wordt een kwaliteitsregel (na een korte
   samenvatting door Claude). Actieve regels gaan mee in elke volgende opdracht.
3. Leerlingen: na het scannen van een oefentoets krijgen opgaven een P-waarde en Rit. Goede opgaven
   worden vaker hergebruikt; zwakke opgaven worden gemarkeerd om te herzien.
"""
from __future__ import annotations

import ast
import math
import operator
import re
from collections import defaultdict
from datetime import datetime

from . import claude, opslag

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod}
_FUNCS = {"round": round, "abs": abs, "min": min, "max": max, "sqrt": math.sqrt, "log": math.log, "exp": math.exp}


def reken(expr: str) -> float:
    expr = expr.replace("×", "*").replace("·", "*").replace("−", "-").replace("^", "**").replace(":", "/")
    if re.search(r"\d\.\d{3},\d", expr):           # Nederlandse notatie 1.250,50
        expr = re.sub(r"(?<=\d)\.(?=\d{3})", "", expr)
    expr = re.sub(r"(?<=\d),(?=\d)", ".", expr)
    expr = expr.replace("%", "/100")

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError("niet toegestaan")
    if len(expr) > 300:
        raise ValueError("te lang")
    return float(ev(ast.parse(expr.strip(), mode="eval")))


def rekencheck(item: dict) -> dict:
    controles, ok = [], True
    for b in item.get("berekeningen", []):
        c = dict(b)
        try:
            w = reken(b["expressie"])
            c["berekend"] = round(w, 6)
            verschil = abs(w - b["uitkomst"])
            c["ok"] = verschil <= max(0.011, abs(b["uitkomst"]) * 0.005)
        except Exception as fout:  # noqa: BLE001
            c["berekend"], c["ok"], c["melding"] = None, None, f"niet na te rekenen: {fout}"
        if c["ok"] is False:
            ok = False
        controles.append(c)
    # structuur: deelscores tellen op tot het maximum
    cv = item.get("cv") or {}
    regels = cv.get("regels", [])
    som = sum(r["punten"] for r in regels)
    indien = regels and all(r["tekst"].lower().startswith("indien") for r in regels)
    per_fout = len(regels) == 1 and "per gemaakte fout" in regels[0]["tekst"].lower()
    structuur = []
    if not regels:
        structuur.append("geen deelscores in het correctievoorschrift")
    elif not (som == item.get("punten") or per_fout or (indien and max(r["punten"] for r in regels) == item.get("punten"))):
        structuur.append(f"deelscores tellen op tot {som}, maximum is {item.get('punten')}")
    if item.get("soort") == "meerkeuze" and not (0 <= item.get("juist", -1) < len(item.get("opties", []))):
        structuur.append("meerkeuze zonder geldig juist antwoord")
    if item.get("soort") == "berekening" and item.get("antwoord_kort"):
        try:
            float(item["antwoord_kort"].replace(".", "").replace(",", ".") if re.search(r"\d\.\d{3}", item["antwoord_kort"])
                  else item["antwoord_kort"].replace(",", "."))
        except ValueError:
            structuur.append("kort antwoord is geen getal")
    if len(item.get("hints", [])) < 2:
        structuur.append("minder dan twee hints")
    from . import figuur
    if figuur.heeft(item):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            if not figuur.teken(item, Path(tmp) / "f.png"):
                structuur.append("de figuur kan niet getekend worden (controleer de gegevens van de figuur)")
    return {"ok": ok and not structuur, "controles": controles, "structuur": structuur}


def score(beoordeling: dict) -> float:
    s = beoordeling.get("scores") or {}
    return round(sum(s.values()) / len(s), 2) if s else 0.0


# ---------------------------------------------------------------------------- regels
def nieuwe_regel(vak: str, niveau: str, tekst: str, aanleiding: str, bron: str, status: str = "actief",
                 geldt_voor: str = "vak_en_niveau") -> dict | None:
    tekst = tekst.strip()
    if not tekst:
        return None
    bestaande = claude.kwaliteitsregels(alleen_actief=False)
    if any(_lijkt(tekst, r["regel"]) for r in bestaande if r.get("status") != "verwijderd"):
        return None
    regel = {"id": f"KR-{len(bestaande) + 1:03d}", "datum": datetime.now().strftime("%Y-%m-%d"),
             "vak": "" if geldt_voor == "alle" else vak, "niveau": niveau if geldt_voor == "vak_en_niveau" else "",
             "regel": tekst, "aanleiding": aanleiding[:300], "bron": bron, "status": status}
    claude.sla_regel_op(regel)
    return regel


def _lijkt(a: str, b: str) -> bool:
    wa, wb = set(re.findall(r"\w{4,}", a.lower())), set(re.findall(r"\w{4,}", b.lower()))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) > 0.6


def regel_uit_docent(client, vak: str, niveau: str, item: dict, oordeel: str, reden: str) -> dict | None:
    try:
        uit = claude.vraag_json(client, claude.systeem_index(vak),
                                claude.regel_tekst(vak, niveau, item, oordeel, reden), claude.SCHEMA_REGEL,
                                max_tokens=1500, model=claude.model_voor("kritiek"), soort="regel")
    except Exception:  # noqa: BLE001
        uit = {"regel": reden, "geldt_voor": "vak_en_niveau", "nuttig": True}
    if not uit.get("nuttig"):
        return None
    return nieuwe_regel(vak, niveau, uit["regel"], f"Docent: {reden}", "docent", "actief", uit["geldt_voor"])


def voorstel_uit_kritiek(vak: str, niveau: str, terugkerend: list[str], opdracht_id: str) -> int:
    n = 0
    for t in terugkerend[:3]:
        if nieuwe_regel(vak, niveau, t, f"Zelfkritiek in opdracht {opdracht_id}", "zelfkritiek", "voorstel"):
            n += 1
    return n


# ---------------------------------------------------------------------------- leercurve
def leercurve() -> dict:
    """Per week: gemiddelde zelfkritiekscore van eerste versies, aandeel dat de docent meteen goedkeurt,
    en het aantal herzieningen. Zo is te zien of de kwaliteit stijgt."""
    with opslag.db() as con:
        rijen = [dict(r) for r in con.execute(
            "SELECT uid, versie, ouder_uid, kritiek_score, docent_oordeel, status, gemaakt, p_waarde, rit FROM items")]
    per_week = defaultdict(lambda: {"eerste": [], "docent_goed": 0, "docent_totaal": 0, "herzien": 0, "items": 0})
    for r in rijen:
        try:
            jaar, week, _ = datetime.fromisoformat(r["gemaakt"]).isocalendar()
        except (TypeError, ValueError):
            continue
        w = per_week[f"{jaar}-W{week:02d}"]
        w["items"] += 1
        if r["versie"] == 1 and r["kritiek_score"] is not None:
            w["eerste"].append(r["kritiek_score"])
        if r["versie"] > 1:
            w["herzien"] += 1
        if r["docent_oordeel"]:
            w["docent_totaal"] += 1
            w["docent_goed"] += r["docent_oordeel"] == "goed"
    uit = []
    for wk, w in sorted(per_week.items()):
        uit.append({"week": wk, "items": w["items"], "herzien": w["herzien"],
                    "zelfkritiek": round(sum(w["eerste"]) / len(w["eerste"]), 2) if w["eerste"] else None,
                    "docent_goed": round(w["docent_goed"] / w["docent_totaal"], 2) if w["docent_totaal"] else None})
    met_p = [r for r in rijen if r["p_waarde"] is not None]
    return {"weken": uit, "totaal": len(rijen),
            "goedgekeurd": sum(1 for r in rijen if r["status"] == "goedgekeurd"),
            "met_resultaten": len(met_p),
            "zwak": sum(1 for r in met_p if r["p_waarde"] < 0.3 or (r["rit"] is not None and r["rit"] < 0.15))}
