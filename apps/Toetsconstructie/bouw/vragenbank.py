"""Vragenbank: elke gebouwde vraag met haar metadata en, na afname, haar statistiek. Plus de kalibratie van verwachte p-waarden.

Gebruik (vanuit de map Toetsconstructie):
  python3 bouw/vragenbank.py toevoegen toetsen/<id>          vragen van een gebouwde toets opnemen of bijwerken
  python3 bouw/vragenbank.py statistiek toetsen/<id>         p, rit en leergaten uit itemanalyse_<versie>.json koppelen
  python3 bouw/vragenbank.py zoek [--vak V] [--niveau N] [--tekst woord] [--zwak]
  python3 bouw/vragenbank.py themas [--vak V] [--niveau N]    gebruikte thema's en contexten (niet opnieuw gebruiken)
  python3 bouw/vragenbank.py kalibratie                      vergelijkt verwachte en werkelijke p-waarden
"""
import argparse, json, re, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "kennisbank" / "vragenbank.json"
KALIBRATIE = ROOT / "kennisbank" / "kalibratie.json"


def _lees(p, standaard):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return standaard


def _schrijf(p, data):
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def laad():
    return _lees(BANK, [])


def _vragen_uit_toets(toets):
    """(opgavetitel, eerste contextzin, vraag-item) voor elke vraag."""
    for opg in toets.get("opgaven", []):
        context = ""
        for it in opg.get("items", []):
            if it.get("t") == "p" and not context:
                context = it.get("text", "")[:240]
            if it.get("t") == "q":
                yield opg.get("titel", ""), context, it


def toevoegen(projectmap):
    d = Path(projectmap)
    pr = _lees(d / "project.json", {})
    rijen = {r.get("nr"): r for r in (_lees(d / "matrijs.json", {}) or {}).get("rijen", [])}
    bank = laad()
    index = {v["id"]: v for v in bank}
    nieuw = 0
    for tf in sorted(d.glob("toets_*.json")):
        toets = _lees(tf, {})
        versie = (toets.get("meta") or {}).get("versie") or tf.stem.split("_")[-1]
        for titel, context, q in _vragen_uit_toets(toets):
            r = rijen.get(q.get("nr"), {})
            vid = f"{pr.get('id', d.name)}:{versie}:{q.get('nr')}"
            v = index.get(vid) or {"id": vid, "afnames": [], "toegevoegd": datetime.now().strftime("%Y-%m-%d")}
            v.update({"project": pr.get("id", d.name), "naam": pr.get("naam"), "vak": pr.get("vak"), "niveau": pr.get("niveau"), "klas": pr.get("klas"),
                      "at": pr.get("at"), "versie": versie, "nr": q.get("nr"), "punten": q.get("p"), "opgave": titel, "context": context,
                      "thema": r.get("thema") or titel, "onderdeel": r.get("onderdeel"), "leerdoel": r.get("leerdoel"), "eindterm": r.get("eindterm"),
                      "bloom": r.get("bloom"), "werkwoord": r.get("werkwoord"), "label": r.get("label"), "koppeling": r.get("koppeling"),
                      "vraagmodel": r.get("vraagmodel"), "p_verwacht": r.get("p_verwacht"), "vraag": q.get("text")})
            if vid not in index:
                bank.append(v); index[vid] = v; nieuw += 1
    _schrijf(BANK, bank)
    return nieuw


def statistiek(projectmap):
    """Koppelt elke itemanalyse_<versie>.json in de projectmap aan de vragen van die versie."""
    d = Path(projectmap)
    pr = _lees(d / "project.json", {})
    bank = laad()
    index = {v["id"]: v for v in bank}
    gekoppeld = 0
    for f in sorted(d.glob("itemanalyse_*.json")):
        ia = _lees(f, {})
        versie = f.stem.split("_", 1)[1]
        for vr in ia.get("vragen", []):
            v = index.get(f"{pr.get('id', d.name)}:{versie}:{vr['nr']}")
            if not v:
                continue
            af = {"bron": ia.get("toets_id") or ia.get("bron"), "datum": ia.get("geimporteerd"), "n": vr.get("n"), "p": vr.get("p"),
                  "rit": vr.get("rit"), "rir": vr.get("rir"), "leergaten": vr.get("leergaten"), "signalen": vr.get("signalen")}
            v["afnames"] = [a for a in v.get("afnames", []) if a.get("bron") != af["bron"]] + [af]
            gekoppeld += 1
    _schrijf(BANK, bank)
    return gekoppeld


def laatste(v):
    return (v.get("afnames") or [None])[-1]


def regel(v):
    a = laatste(v)
    stat = f"p {a['p']} rit {a['rit']} (n {a['n']})" if a else "nog niet afgenomen"
    zwak = " ZWAK" if a and ((a.get("rit") is not None and a["rit"] < 0.15) or (a.get("p") is not None and (a["p"] < 0.3 or a["p"] > 0.9))) else ""
    return f"[{v['id']}] {v.get('at') or ''} {v.get('klas') or v.get('niveau') or ''} | {v.get('punten')}p {v.get('bloom') or ''} | {v.get('leerdoel') or ''} | anker: {v.get('koppeling') or '-'} | thema: {v.get('thema') or ''} | verwacht p {v.get('p_verwacht')} | {stat}{zwak}"


def filter_bank(vak=None, niveau=None, tekst=None, zwak=False):
    uit = []
    for v in laad():
        if vak and (v.get("vak") or "").lower() != vak.lower():
            continue
        if niveau and (v.get("niveau") or "").lower() != niveau.lower():
            continue
        if tekst and tekst.lower() not in json.dumps(v, ensure_ascii=False).lower():
            continue
        a = laatste(v)
        if zwak and not (a and a.get("signalen")):
            continue
        uit.append(v)
    return uit


def kalibratie():
    paren = []
    for v in laad():
        a = laatste(v)
        pv = v.get("p_verwacht")
        if a and a.get("p") is not None and isinstance(pv, (int, float)):
            paren.append((v, a["p"] - pv))
    def samen(groep):
        g = {}
        for v, verschil in paren:
            g.setdefault(groep(v) or "onbekend", []).append(verschil)
        return {k: {"n": len(x), "gemiddeld_verschil": round(sum(x) / len(x), 3)} for k, x in g.items()}
    uit = {"bijgewerkt": datetime.now().strftime("%Y-%m-%d %H:%M"), "n": len(paren),
           "uitleg": "verschil = werkelijke p min verwachte p. Positief: de vragen bleken makkelijker dan verwacht; negatief: moeilijker. Tel het gemiddelde verschil op bij je volgende schatting voor dezelfde groep.",
           "totaal": round(sum(x for _, x in paren) / len(paren), 3) if paren else None,
           "per_niveau": samen(lambda v: v.get("niveau")), "per_bloom": samen(lambda v: v.get("bloom")),
           "per_vak_niveau": samen(lambda v: f"{v.get('vak')} {v.get('niveau')}")}
    _schrijf(KALIBRATIE, uit)
    return uit


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("actie", choices=["toevoegen", "statistiek", "zoek", "themas", "kalibratie"])
    a.add_argument("map", nargs="?")
    a.add_argument("--vak"); a.add_argument("--niveau"); a.add_argument("--tekst"); a.add_argument("--zwak", action="store_true")
    x = a.parse_args()
    if x.actie == "toevoegen":
        print(f"{toevoegen(x.map)} nieuwe vragen opgenomen in de vragenbank.")
    elif x.actie == "statistiek":
        print(f"{statistiek(x.map)} vragen gekoppeld aan afnamegegevens.")
    elif x.actie == "zoek":
        r = filter_bank(x.vak, x.niveau, x.tekst, x.zwak)
        print("\n".join(regel(v) for v in r) or "Geen vragen gevonden.")
    elif x.actie == "themas":
        gezien = {}
        for v in filter_bank(x.vak, x.niveau):
            gezien.setdefault(v.get("thema") or v.get("opgave"), set()).add(f"{v.get('at') or v.get('naam')} {v.get('versie')}")
        print("\n".join(f"{t}: {', '.join(sorted(s))}" for t, s in sorted(gezien.items(), key=lambda z: str(z[0]))) or "Nog geen thema's in de vragenbank.")
    else:
        print(json.dumps(kalibratie(), ensure_ascii=False, indent=1))
