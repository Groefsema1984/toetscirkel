# -*- coding: utf-8 -*-
"""Regels voor de score per vraag: wat is definitief, en wat moet de docent nog beslissen."""
from __future__ import annotations


def begrens(waarde: float, maximum: float) -> float:
    return round(min(max(float(waarde), 0.0), float(maximum)), 2)


def soort(v: dict) -> str:
    """Waarom staat deze vraag op de validatielijst? '' = hoeft niet bekeken te worden."""
    if v.get("ontbreekt"):
        return "ontbreekt"                       # de AI heeft de vraag niet beoordeeld
    if v["docent"] is not None and abs(v["docent"] - v["ai"]) >= 0.5:
        return "conflict"                        # docent en AI geven een andere score
    if v["zekerheid"] == "laag":
        return "twijfel"                         # handschrift of score slecht leesbaar
    return ""


def is_open(v: dict) -> bool:
    return bool(soort(v)) and not v.get("besluit")


def eindscore(v: dict) -> float:
    """Docentscore is leidend als die er staat; een besluit van de docent gaat boven alles."""
    if v.get("besluit"):
        return begrens(v["definitief"], v["max"])
    return begrens(v["docent"] if v["docent"] is not None else v["ai"], v["max"])


def eind_leergat(v: dict) -> tuple[str | None, str | None]:
    """(leergatcode, foutomschrijving) die bij de definitieve score hoort."""
    if eindscore(v) >= v["max"]:
        return None, None
    if v.get("besluit"):
        return (v.get("def_leergat") or v.get("leergat"),
                v.get("def_omschrijving") or v.get("omschrijving") or None)
    return v.get("leergat"), v.get("omschrijving") or None


def open_punten(s: dict) -> dict:
    """Telt wat er nog open staat in een sessie."""
    tel = {"conflict": 0, "twijfel": 0, "ontbreekt": 0, "wezen": 0, "koppeling": 0,
           "bezig": 0, "fout": 0}
    for l in s["leerlingen"].values():
        if l["status"] in ("bezig", "wacht"):
            tel["bezig"] += 1
        elif l["status"] == "fout":
            tel["fout"] += 1
        if l["koppeling"].get("vlag") and not l["koppeling"].get("ok"):
            tel["koppeling"] += 1
        for v in l["vragen"].values():
            if is_open(v):
                tel[soort(v)] += 1
    tel["wezen"] = sum(1 for w in s["wezen"] if w["status"] == "open")
    tel["totaal"] = sum(tel.values())
    return tel
