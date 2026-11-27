"""Rekenbare kern van de toetsopdracht: inlezen, narekenen en vergelijken met de matrijs.

Gebruik: python3 bouw/kwaliteit.py toetsen/<id>      (toont de fouten en waarschuwingen)
De app gebruikt dezelfde functies om goedkeuring te blokkeren zolang de kern niet klopt.
"""
import json, re, sys
from pathlib import Path

BLOOM = ["Onthouden", "Begrijpen", "Toepassen", "Analyseren", "Evalueren", "Creëren"]


def lees_kern(tekst):
    """Geeft (kern, fouten). Zoekt het blok ```kern ... ``` in de toetsopdracht."""
    m = re.search(r"```\s*kern\s*\n(.*?)```", tekst or "", re.S | re.I)
    if not m:
        return None, ["De toetsopdracht heeft nog geen rekenbare kern (blok ```kern)."]
    try:
        kern = json.loads(m.group(1))
    except Exception as e:
        return None, [f"De rekenbare kern is geen geldige JSON: {e}."]
    if not isinstance(kern, dict):
        return None, ["De rekenbare kern moet een JSON-object zijn."]
    return kern, []


def _getal(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _som(d):
    return sum(v for v in d.values() if _getal(v))


def controleer_kern(kern):
    """Geeft (fouten, waarschuwingen). Fouten blokkeren de goedkeuring."""
    fouten, waarsch = [], []
    if not kern:
        return ["Geen rekenbare kern."], []
    tot = kern.get("totaal_punten")
    if not _getal(tot) or tot <= 0:
        fouten.append("totaal_punten ontbreekt of is geen positief getal.")
        return fouten, waarsch
    min_ = kern.get("minuten")
    if not _getal(min_) or min_ <= 0:
        fouten.append("minuten ontbreekt.")
    else:
        tempo = min_ / tot
        if tempo < 2.4 or tempo > 3.4:
            waarsch.append(f"Tempo {str(round(tempo, 2)).replace('.', ',')} minuut per punt ligt buiten 2,4 tot 3,4 (CE: havo 3,2, vwo 3,1).")
    ond = kern.get("onderdelen")
    if not isinstance(ond, dict) or not ond:
        fouten.append("onderdelen (punten per onderdeel) ontbreekt.")
    else:
        if any(not _getal(v) or v != int(v) for v in ond.values()):
            fouten.append("Elk onderdeel moet een heel aantal punten hebben.")
        s = _som(ond)
        if s != tot:
            fouten.append(f"De onderdelen tellen op tot {s:g} punten, niet tot het totaal van {tot:g}.")
        pct = kern.get("procenten")
        if isinstance(pct, dict) and pct:
            if abs(_som(pct) - 100) > 0.6:
                fouten.append(f"De procenten tellen op tot {_som(pct):g}, niet tot 100.")
            for naam, p in pct.items():
                if naam not in ond:
                    fouten.append(f"Procent voor '{naam}' heeft geen onderdeel met dezelfde naam.")
                    continue
                exact = p * tot / 100
                if abs(exact - round(exact)) > 0.17 or round(exact) != ond[naam]:
                    fouten.append(f"{str(p).replace('.', ',')}% van {tot:g} punten is {str(round(exact, 2)).replace('.', ',')}, geen {ond[naam]:g} hele punten: kies een ander totaal of pas de verdeling aan.")
    bl = kern.get("bloom")
    if not isinstance(bl, dict) or not bl:
        fouten.append("bloom (punten per Bloom-niveau) ontbreekt.")
    else:
        onbekend = [b for b in bl if b not in BLOOM]
        if onbekend:
            fouten.append("Onbekend Bloom-niveau: " + ", ".join(onbekend) + ".")
        if _som(bl) != tot:
            fouten.append(f"Bloom telt op tot {_som(bl):g} punten, niet tot {tot:g}.")
    for sleutel, naam in (("vraagsoorten", "De vraagsoorten"), ("moeilijkheid", "De moeilijkheidsverdeling")):
        d = kern.get(sleutel)
        if isinstance(d, dict) and d and _som(d) != tot:
            fouten.append(f"{naam} telt op tot {_som(d):g} punten, niet tot {tot:g}.")
        elif not d:
            waarsch.append(f"{sleutel} ontbreekt in de kern.")
    if kern.get("tweefasen"):
        lab = kern.get("labels") or {}
        if _som(lab) != tot:
            fouten.append(f"Tweefasen: de labels HV en V tellen op tot {_som(lab):g}, niet tot {tot:g}.")
        if not lab.get("HV"):
            fouten.append("Tweefasen: er zijn geen punten met label HV.")
    v = kern.get("vragen")
    if _getal(v) and v > 0 and not (1.4 <= tot / v <= 3.5):
        waarsch.append(f"{tot:g} punten in {v:g} vragen is gemiddeld {str(round(tot / v, 1)).replace('.', ',')} punt per vraag; ongebruikelijk voor CE-stijl.")
    if not kern.get("versies"):
        waarsch.append("versies ontbreekt in de kern.")
    return fouten, waarsch


def vergelijk_matrijs(kern, matrijs):
    """Afwijkingen tussen de goedgekeurde kern en de matrijs (lijst met zinnen)."""
    if not kern or not matrijs or not matrijs.get("rijen"):
        return []
    rijen = matrijs["rijen"]
    uit = []
    tot = sum(r.get("punten") or 0 for r in rijen)
    if kern.get("totaal_punten") and tot != kern["totaal_punten"]:
        uit.append(f"De matrijs heeft {tot} punten; de opdracht {kern['totaal_punten']}.")
    per = {}
    for r in rijen:
        per[r.get("onderdeel", "")] = per.get(r.get("onderdeel", ""), 0) + (r.get("punten") or 0)
    for naam, p in (kern.get("onderdelen") or {}).items():
        if per.get(naam, 0) != p:
            uit.append(f"Onderdeel '{naam}': {per.get(naam, 0)} punten in de matrijs, {p} in de opdracht.")
    for naam in per:
        if naam not in (kern.get("onderdelen") or {}):
            uit.append(f"Onderdeel '{naam}' staat in de matrijs maar niet in de opdracht.")
    bl = {}
    for r in rijen:
        bl[r.get("bloom", "")] = bl.get(r.get("bloom", ""), 0) + (r.get("punten") or 0)
    for b, p in (kern.get("bloom") or {}).items():
        if bl.get(b, 0) != p:
            uit.append(f"Bloom {b}: {bl.get(b, 0)} punten in de matrijs, {p} in de opdracht.")
    if kern.get("tweefasen"):
        lab = {}
        for r in rijen:
            lab[r.get("label") or "HV"] = lab.get(r.get("label") or "HV", 0) + (r.get("punten") or 0)
        for k, p in (kern.get("labels") or {}).items():
            if lab.get(k, 0) != p:
                uit.append(f"Label {k}: {lab.get(k, 0)} punten in de matrijs, {p} in de opdracht.")
    zonder_p = [str(r.get("nr")) for r in rijen if not _getal(r.get("p_verwacht"))]
    if zonder_p:
        uit.append("Geen verwachte p-waarde bij vraag " + ", ".join(zonder_p) + ".")
    return uit


if __name__ == "__main__":
    d = Path(sys.argv[1])
    if not (d / "toetsopdracht.md").exists():
        sys.exit("Deze toets heeft nog geen toetsopdracht.")
    tekst = (d / "toetsopdracht.md").read_text(encoding="utf-8")
    kern, f = lees_kern(tekst)
    f2, w = controleer_kern(kern) if kern else ([], [])
    for x in f + f2:
        print("FOUT:", x)
    for x in w:
        print("LET OP:", x)
    m = d / "matrijs.json"
    if kern and m.exists():
        for x in vergelijk_matrijs(kern, json.loads(m.read_text(encoding="utf-8"))):
            print("MATRIJS:", x)
    if not (f + f2):
        print("De rekenbare kern klopt.")
