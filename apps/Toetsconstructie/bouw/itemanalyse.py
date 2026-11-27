"""Itemanalyse na de afname: p-waarde, rit, rir, Cronbachs alfa en leergaten per vraag.

Bronnen: de database van de Toetsevaluatie (alleen lezen) of een CSV-bestand.
CSV-vorm: eerste regel  leerling;1;2;3;...   tweede regel  max;2;3;2;...   daarna per leerling een regel met de scores.
(komma of puntkomma als scheiding; decimalen met komma of punt.)

Gebruik: python3 bouw/itemanalyse.py <pad naar toetsevaluatie.db> <toets_id>
"""
import csv, io, json, math, sqlite3, sys
from collections import Counter
from datetime import datetime

LEERGATEN = {"KD": "kennisdeficit", "PR": "procedurele rekenfout", "RF": "redeneerfout", "LI": "leesfout", "DW": "doorwerkfout", "MC": "misconceptie"}


def _pearson(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def _var(x):
    n = len(x)
    if n < 2:
        return 0.0
    m = sum(x) / n
    return sum((a - m) ** 2 for a in x) / (n - 1)


def analyse(items, scores, leergaten=None):
    """items: lijst {nr, max, ...}; scores: {leerling: {nr: score}}; leergaten: {nr: Counter}."""
    leergaten = leergaten or {}
    items = sorted(items, key=lambda i: i["nr"])
    leerlingen = [l for l, s in scores.items() if s]
    n = len(leerlingen)
    mat = [[float(scores[l].get(i["nr"], 0) or 0) for i in items] for l in leerlingen]
    totaal = [sum(r) for r in mat]
    max_tot = sum(i["max"] for i in items)
    vragen = []
    for k, it in enumerate(items):
        x = [r[k] for r in mat]
        gem = sum(x) / n if n else 0
        p = gem / it["max"] if it["max"] else None
        rit = _pearson(x, totaal)
        rir = _pearson(x, [t - a for t, a in zip(totaal, x)])
        lg = Counter(leergaten.get(it["nr"], {}))
        fout_n = sum(1 for a in x if a < it["max"])
        sig = []
        if p is not None and p < 0.30:
            sig.append("zeer moeilijk (p < 0,30)")
        if p is not None and p > 0.90:
            sig.append("zeer makkelijk (p > 0,90)")
        if rit is not None and rit < 0:
            sig.append("negatieve rit: sterke leerlingen scoren hier slechter; controleer vraag en correctievoorschrift")
        elif rit is not None and rit < 0.15:
            sig.append("laag onderscheid (rit < 0,15)")
        if rit is None and n >= 3:
            sig.append("geen spreiding: iedereen had dezelfde score")
        tot_lg = sum(lg.values())
        if tot_lg and lg.get("LI", 0) / tot_lg >= 0.30:
            sig.append("veel leesfouten: mogelijk onnodige leeslast of onduidelijke vraag")
        if tot_lg and lg.get("MC", 0) / tot_lg >= 0.30:
            sig.append("veel misconcepties: bruikbaar als afleider of veelgemaakte fout")
        vragen.append({**{k2: v for k2, v in it.items()}, "n": n, "gemiddelde": round(gem, 2), "p": None if p is None else round(p, 2),
                       "rit": None if rit is None else round(rit, 2), "rir": None if rir is None else round(rir, 2),
                       "aantal_niet_vol": fout_n, "leergaten": dict(lg), "signalen": sig})
    k = len(items)
    var_t = _var(totaal)
    alfa = None
    if k > 1 and var_t > 0 and n >= 5:  # bij minder dan 5 leerlingen zegt alfa niets
        alfa = round(k / (k - 1) * (1 - sum(_var([r[j] for r in mat]) for j in range(k)) / var_t), 2)
    uit = {"n": n, "max_totaal": max_tot, "gemiddelde": round(sum(totaal) / n, 2) if n else None,
           "sd": round(math.sqrt(var_t), 2) if n > 1 else None, "p_toets": round(sum(totaal) / n / max_tot, 2) if n and max_tot else None,
           "alfa": alfa, "vragen": vragen, "geimporteerd": datetime.now().strftime("%Y-%m-%d %H:%M")}
    let_op = []
    if n < 25:
        let_op.append(f"Kleine groep ({n} leerlingen): p en rit zijn signalen, geen oordelen. Combineer met andere klassen, versies of jaren.")
    if alfa is not None and alfa < 0.6:
        let_op.append("Lage betrouwbaarheid (alfa < 0,60): het totaal meet niet één samenhangend geheel, of de groep is klein.")
    uit["let_op"] = let_op
    return uit


def _db(pad):
    return sqlite3.connect(f"file:{pad}?mode=ro", uri=True)


def db_toetsen(pad):
    c = _db(pad)
    rij = c.execute("""select t.toets_id, t.vak, t.datum, count(distinct m.item_id), count(distinct e.leerling_id)
                       from toetsen t left join toetsmatrijs m on m.toets_id = t.toets_id
                       left join evaluatieresultaten e on e.item_id = m.item_id group by t.toets_id order by t.datum desc""").fetchall()
    c.close()
    return [{"toets_id": r[0], "vak": r[1], "datum": r[2], "vragen": r[3], "leerlingen": r[4]} for r in rij]


def uit_db(pad, toets_id):
    c = _db(pad)
    kol = [r[1] for r in c.execute("pragma table_info(toetsmatrijs)")]
    extra = [k for k in ("onderwerp", "leerdoel") if k in kol]
    items, idnr = [], {}
    for r in c.execute(f"select item_id, vraagnummer, max_score, bloom_niveau, tweefasen_label, domein_eindterm{''.join(', ' + k for k in extra)} from toetsmatrijs where toets_id = ? order by vraagnummer", (toets_id,)):
        it = {"nr": int(r[1]), "max": float(r[2] or 0), "bloom": r[3], "label": r[4], "eindterm": r[5]}
        for j, k in enumerate(extra):
            it[k] = r[6 + j]
        items.append(it)
        idnr[r[0]] = int(r[1])
    if not items:
        c.close()
        raise ValueError(f"Toets '{toets_id}' heeft geen vragen in de Toetsevaluatie.")
    scores, lg = {}, {}
    q = f"select leerling_id, item_id, behaalde_score, type_leergat from evaluatieresultaten where item_id in ({','.join('?' * len(idnr))})"
    for l, iid, s, t in c.execute(q, list(idnr)):
        nr = idnr[iid]
        scores.setdefault(l, {})[nr] = float(s or 0)
        if t:
            lg.setdefault(nr, Counter())[t] += 1
    c.close()
    uit = analyse(items, scores, lg)
    uit.update({"bron": "toetsevaluatie", "toets_id": toets_id})
    return uit


def uit_csv(tekst):
    tekst = tekst.lstrip("﻿")
    scheid = ";" if tekst.count(";") >= tekst.count(",") else ","
    rijen = [r for r in csv.reader(io.StringIO(tekst), delimiter=scheid) if any(c.strip() for c in r)]
    if len(rijen) < 3:
        raise ValueError("Het CSV-bestand heeft minstens een kopregel, een regel 'max' en één leerling nodig.")
    getal = lambda s: float(str(s).strip().replace(",", ".") or 0)
    kop, mx = rijen[0], rijen[1]
    if mx[0].strip().lower() != "max":
        raise ValueError("De tweede regel moet beginnen met 'max' en de maximumscore per vraag geven.")
    items = [{"nr": int(float(kop[j].strip())), "max": getal(mx[j])} for j in range(1, len(kop)) if kop[j].strip()]
    scores = {r[0].strip(): {items[j - 1]["nr"]: getal(r[j]) for j in range(1, len(items) + 1) if j < len(r)} for r in rijen[2:]}
    uit = analyse(items, scores)
    uit["bron"] = "csv"
    return uit


if __name__ == "__main__":
    print(json.dumps(uit_db(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=1))
