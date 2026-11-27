# -*- coding: utf-8 -*-
"""Bibliotheek: lesmateriaal, examens en correctievoorschriften lezen, in fragmenten knippen en per
leerdoel ordenen. De originele bestanden blijven waar ze staan; de geordende bibliotheek (per vak,
thema en leerdoel) wordt als Markdown in Google Drive gezet."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import claude, lezen, opslag, te
from .instellingen import BASIS, WERKERS, laad_instellingen

log = logging.getLogger("oefentool")

# Bestanden die bij een examen horen maar niet los gelezen worden.
NIET_LOS = [
    (r"OMZETTINGSTABEL|NORMERING", "Omzettingstabel: niet nodig voor oefenmateriaal."),
    (r"AANVULLING|ERRATUM", "Aanvulling of erratum bij een examen: niet nodig."),
    (r"UITWERKBIJLAGE", "Uitwerkbijlage bij een examen: de vragen staan in het opgavenboekje."),
    (r"(^|_)BIJLAGE", "Bijlage bij een examen: de vragen staan in het opgavenboekje."),
    (r"(^|_)PTA(_|\b)|PTO", "PTA of PTO: niet nodig voor oefenmateriaal."),
]
CV = r"CORRECTIE_?VOORSCHRIFT|CORRECTIEFVOORSCHRIFT|(^|_)CVS?(_|\.|$)|ANTWOORDMODEL|ANTWOORDEN"
OPGAVEN = r"(^|_)OPGAVEN?(_|\.|$)"

JOB = {"bezig": False, "gedaan": 0, "totaal": 0, "tekst": "", "fouten": [], "klaar": ""}
_job_slot = threading.Lock()


# ---------------------------------------------------------------------------- herkennen
def herken(pad: Path, root: Path) -> dict:
    rel = str(pad.relative_to(root)) if pad.is_relative_to(root) else pad.name
    hoofd = rel.upper().replace(" ", "_")
    naam = pad.name.upper().replace(" ", "_")
    vak = ("Bedrijfseconomie" if re.search(r"BEDRIJFSECONOMIE|(^|[_/])BECO([_/]|$)", hoofd)
           else "Economie" if "ECONOMIE" in hoofd or "_ECO_" in hoofd else "")
    niveau = ""
    for n in ("MAVO", "HAVO", "VWO"):
        if re.search(rf"(^|[_/\d]){n}([_/\d]|$)", hoofd):
            niveau = n.lower()
            break
    if re.search(r"EINDTERM|SYLLABUS", naam):
        soort = "eindtermen"
    elif re.search(CV, naam):
        soort = "correctievoorschrift"
    elif re.search(r"EXAMEN|EXAMENCONTEXT", naam) and re.search(OPGAVEN, naam):
        soort = "examen"
    else:
        soort = "lesmateriaal"
    return {"vak": vak, "niveau": niveau, "sector": ("mavo" if niveau == "mavo" else "havo-vwo") if niveau else "",
            "soort": soort, "map": str(Path(rel).parent)}


def _sleutel(naam: str) -> str:
    n = Path(naam).stem.upper()
    n = re.sub(CV + "|" + OPGAVEN, "_", n)
    return re.sub(r"_+", "_", n).strip("_")


def scan() -> dict:
    """Zoekt alle bestanden in de bronmappen en werkt de lijst bij. Leest nog niets met Claude."""
    inst = laad_instellingen()
    gevonden = {}
    for root_s in inst["bronmappen"]:
        root = Path(root_s).expanduser()
        if not root.is_dir():
            continue
        for map_, mappen, bestanden in os.walk(root):
            mappen[:] = [m for m in mappen if not m.startswith((".", "_")) and m != "Oefentool_Bibliotheek"]
            for b in bestanden:
                p = Path(map_) / b
                ext = p.suffix.lower()
                if b.startswith((".", "~$")) or (ext not in lezen.LEESBAAR and ext not in lezen.OVERSLAAN):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                gevonden[str(p)] = (p, root, st)
    nieuw = gewijzigd = 0
    with opslag.db() as con:
        bekend = {r["pad"]: dict(r) for r in con.execute("SELECT * FROM bronnen")}
        for pad, (p, root, st) in gevonden.items():
            h = herken(p, root)
            oud = bekend.get(pad)
            if oud is None:
                con.execute("INSERT INTO bronnen (pad, naam, map, vak, sector, niveau, soort, grootte, mtime) "
                            "VALUES (?,?,?,?,?,?,?,?,?)",
                            (pad, p.name, h["map"], h["vak"], h["sector"], h["niveau"], h["soort"], st.st_size, st.st_mtime))
                nieuw += 1
            elif abs((oud["mtime"] or 0) - st.st_mtime) > 1:
                con.execute("UPDATE bronnen SET mtime = ?, grootte = ?, status = CASE WHEN status = 'geindexeerd' "
                            "THEN 'gewijzigd' ELSE status END WHERE pad = ?", (st.st_mtime, st.st_size, pad))
                gewijzigd += 1
        weg = [p for p in bekend if p not in gevonden]
        for p in weg:
            con.execute("DELETE FROM bronnen WHERE pad = ?", (p,))
        _koppel_examens(con)
    return {"gevonden": len(gevonden), "nieuw": nieuw, "gewijzigd": gewijzigd, "verdwenen": len(weg)}


def _koppel_examens(con) -> None:
    """Zet een correctievoorschrift achter het opgavenboekje van hetzelfde examen; los overbodige delen."""
    rijen = [dict(r) for r in con.execute("SELECT bron_id, pad, naam, soort, status FROM bronnen")]
    per_map = defaultdict(list)
    for r in rijen:
        per_map[str(Path(r["pad"]).parent)].append(r)
    for groep in per_map.values():
        cvs = {_sleutel(r["naam"]): r for r in groep if r["soort"] == "correctievoorschrift"
               and not re.search(r"AANVULLING|ERRATUM", r["naam"].upper())}
        for r in groep:
            naam = r["naam"].upper().replace(" ", "_")
            reden = next((t for pat, t in NIET_LOS if re.search(pat, naam)), None)
            if reden and r["status"] in ("nieuw", "overgeslagen"):
                con.execute("UPDATE bronnen SET status = 'overgeslagen', melding = ? WHERE bron_id = ?", (reden, r["bron_id"]))
                continue
            if re.search(OPGAVEN, naam):
                cv = cvs.get(_sleutel(r["naam"]))
                if cv:
                    con.execute("UPDATE bronnen SET partner = ?, soort = 'examen' WHERE bron_id = ?", (cv["pad"], r["bron_id"]))
                    if cv["status"] in ("nieuw", "overgeslagen"):
                        con.execute("UPDATE bronnen SET status = 'overgeslagen', melding = ? WHERE bron_id = ?",
                                    (f"Wordt samen met {r['naam']} gelezen.", cv["bron_id"]))
        for r in groep:
            if Path(r["pad"]).suffix.lower() in lezen.OVERSLAAN and r["status"] == "nieuw":
                con.execute("UPDATE bronnen SET status = 'overgeslagen', melding = ? WHERE bron_id = ?",
                            (lezen.OVERSLAAN[Path(r["pad"]).suffix.lower()], r["bron_id"]))


# ---------------------------------------------------------------------------- overzicht
def overzicht() -> dict:
    with opslag.db() as con:
        bronnen = [dict(r) for r in con.execute(
            "SELECT b.*, (SELECT COUNT(*) FROM fragmenten f WHERE f.bron_id = b.bron_id) AS fragmenten "
            "FROM bronnen b ORDER BY b.pad")]
    inst = laad_instellingen()
    roots = [Path(r).expanduser() for r in inst["bronmappen"]]
    mappen = defaultdict(lambda: {"bestanden": [], "tel": defaultdict(int), "tekens": 0})
    for b in bronnen:
        root = next((r for r in roots if b["pad"].startswith(str(r))), None)
        naam = (root.name + "/" + b["map"]) if root else b["map"]
        m = mappen[naam.rstrip("/.")]
        m["bestanden"].append(b)
        m["tel"][b["status"]] += 1
        if b["status"] in ("nieuw", "gewijzigd"):
            m["tekens"] += int((b["grootte"] or 0) * (0.08 if b["naam"].lower().endswith(".pdf") else 0.5))
    tel = defaultdict(int)
    for b in bronnen:
        tel[b["status"]] += 1
    return {"mappen": dict(sorted(mappen.items())), "tel": dict(tel), "aantal": len(bronnen)}


def dekking(vak: str, sector: str) -> list[dict]:
    """Per leerdoel: hoeveel lesfragmenten, examenvragen en goedgekeurde opgaven er zijn."""
    reg = te.register_lijst(vak, sector)
    tel = defaultdict(lambda: defaultdict(int))
    with opslag.db() as con:
        for r in con.execute("SELECT f.soort, f.leerdoel_codes FROM fragmenten f JOIN bronnen b ON b.bron_id = f.bron_id "
                             "WHERE b.vak = ? OR b.vak = ''", (vak,)):
            for c in json.loads(r["leerdoel_codes"] or "[]"):
                tel[c]["examen" if r["soort"] == "examenvraag" else "les"] += 1
        for r in con.execute("SELECT leerdoel_code, COUNT(*) AS n FROM items WHERE status = 'goedgekeurd' "
                             "AND vak = ? GROUP BY leerdoel_code", (vak,)):
            tel[r["leerdoel_code"]]["items"] = r["n"]
    return [dict(d, les=tel[d["code"]]["les"], examen=tel[d["code"]]["examen"], items=tel[d["code"]]["items"])
            for d in reg]


# ---------------------------------------------------------------------------- indexeren
def _registertekst(vak: str) -> tuple[str, set[str]]:
    regels, codes = [], set()
    for sec in ("havo-vwo", "mavo"):
        try:
            lijst = te.register_lijst(vak, sec) if vak else []
        except Exception:  # noqa: BLE001
            lijst = []
        if lijst:
            regels.append(f"[{vak} {sec}]\n" + claude.leerdoel_regels(lijst))
            codes |= {d["code"] for d in lijst}
    return "\n".join(regels) or "(register nog leeg)", codes


def _knip(tekst: str, max_tekens: int, stuk: int = 60_000) -> list[str]:
    tekst = tekst[:max_tekens]
    if len(tekst) <= stuk:
        return [tekst]
    delen, begin = [], 0
    while begin < len(tekst):
        eind = min(len(tekst), begin + stuk)
        knip = tekst.rfind("\n[p.", begin + stuk // 2, eind) if eind < len(tekst) else -1
        eind = knip if knip > 0 else eind
        delen.append(tekst[begin:eind])
        begin = eind
    return delen


ROL_INDEX = ("Je ordent de bibliotheek van de docent. Je knipt bestanden in fragmenten en koppelt ze aan "
             "leerdoelen uit zijn leerdoelenregister. Je vat samen in eigen woorden.")


def _voorbereid(bron: dict) -> tuple[list[dict], list[str], int]:
    """(systeemprompt, opdracht per deel, lengte van de tekst). Gooit lezen.NietLeesbaar."""
    pad = Path(bron["pad"])
    tekst = lezen.lees(pad)
    partner = ""
    if bron.get("partner") and Path(bron["partner"]).exists():
        try:
            partner = lezen.lees(Path(bron["partner"]))[:60_000]
        except lezen.NietLeesbaar:
            partner = ""
    vak = bron["vak"] or "Economie"
    regtekst, _ = _registertekst(vak)
    sys = claude.systeem_index(vak)
    delen = [claude.index_tekst(pad.name, bron["map"], deel, regtekst, partner if i == 0 else "")
             for i, deel in enumerate(_knip(tekst, laad_instellingen()["max_tekens_per_bron"]))]
    return sys, delen, len(tekst)


def _opslaan(bron: dict, uitkomsten: list[dict], tekst_lengte: int) -> int:
    _, codes = _registertekst(bron["vak"] or "Economie")
    niveau = bron["niveau"] or "havo"
    meta = uitkomsten[0] if uitkomsten else {}
    fragmenten = [f for u in uitkomsten for f in u["fragmenten"]]
    with opslag.db() as con:
        con.execute("DELETE FROM fragmenten WHERE bron_id = ?", (bron["bron_id"],))
        for f in fragmenten:
            geldig = [c for c in f["leerdoel_codes"] if c in codes]
            con.execute(
                "INSERT INTO fragmenten (bron_id, soort, titel, kern, vraag, antwoord, paginas, examen, vraagnr, punten,"
                " leerdoel_codes, eindtermen, bloom, niveau) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (bron["bron_id"], f["soort"], f["titel"], f["kern"], f["vraag"], f["antwoord"], f["paginas"],
                 f["examen"], f["vraagnr"], f["punten"], json.dumps(geldig), f["eindtermen"], f["bloom"],
                 meta.get("niveau") or niveau))
        con.execute(
            "UPDATE bronnen SET status = 'geindexeerd', melding = ?, tekens = ?, geindexeerd = ?,"
            " vak = COALESCE(NULLIF(vak, ''), ?), niveau = COALESCE(NULLIF(niveau, ''), ?) WHERE bron_id = ?",
            (meta.get("samenvatting", "")[:400], tekst_lengte, opslag.nu(),
             meta.get("vak") if meta.get("vak") != "onbekend" else "",
             meta.get("niveau") if meta.get("niveau") in ("mavo", "havo", "vwo") else "", bron["bron_id"]))
    return len(fragmenten)


def indexeer_bron(client, bron: dict) -> int:
    sys, delen, lengte = _voorbereid(bron)
    uitkomsten = [claude.vraag_json(client, sys, d, claude.SCHEMA_INDEX, max_tokens=24_000, model=claude.model_voor("index"),
                                    soort="index") for d in delen]
    return _opslaan(bron, uitkomsten, lengte)


# ---------------------------------------------------------------------------- batch (helft van de kosten)
BATCHES = BASIS / "batches.json"
_batch_slot = threading.Lock()
_poller: dict = {}


def batches() -> list[dict]:
    try:
        return json.loads(BATCHES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _bewaar_batches(lijst: list[dict]) -> None:
    BATCHES.write_text(json.dumps(lijst, ensure_ascii=False, indent=1), encoding="utf-8")


def start_batch(bron_ids: list[int]) -> dict:
    """Stuurt alle bestanden in één batch naar Anthropic. Klaar binnen enkele uren, voor de helft van de prijs."""
    client = claude.maak_client()
    if client is None:
        raise claude.ApiFout("Lezen via batch gebruikt API-tegoed en daarvoor is een API-sleutel nodig.")
    with opslag.db() as con:
        vt = ",".join("?" * len(bron_ids)) or "''"
        bronnen = [dict(r) for r in con.execute(f"SELECT * FROM bronnen WHERE bron_id IN ({vt})", bron_ids)]
    verzoeken, delen, overgeslagen = [], {}, 0
    for b in bronnen:
        try:
            sys, opdrachten, lengte = _voorbereid(b)
        except lezen.NietLeesbaar as fout:
            with opslag.db() as con:
                con.execute("UPDATE bronnen SET status = 'overgeslagen', melding = ? WHERE bron_id = ?", (str(fout), b["bron_id"]))
            overgeslagen += 1
            continue
        delen[str(b["bron_id"])] = {"n": len(opdrachten), "lengte": lengte}
        for i, tekst in enumerate(opdrachten):
            verzoeken.append({"custom_id": f"b{b['bron_id']}d{i}", "params": {
                "model": claude.model_voor("index"), "max_tokens": 24_000, "system": sys,
                "messages": [{"role": "user", "content": [{"type": "text", "text": tekst}]}],
                "output_config": {"format": {"type": "json_schema", "schema": claude.SCHEMA_INDEX}}}})
    if not verzoeken:
        return {"id": None, "bestanden": 0, "overgeslagen": overgeslagen}
    batch = client.messages.batches.create(requests=verzoeken)
    with _batch_slot:
        lijst = batches()
        lijst.append({"id": batch.id, "gemaakt": opslag.nu(), "delen": delen, "status": "bezig", "verzoeken": len(verzoeken)})
        _bewaar_batches(lijst)
    with opslag.db() as con:
        con.executemany("UPDATE bronnen SET status = 'batch', melding = 'In de wachtrij bij Anthropic (batch)' WHERE bron_id = ?",
                        [(int(k),) for k in delen])
    start_poller()
    return {"id": batch.id, "bestanden": len(delen), "overgeslagen": overgeslagen}


def start_poller(interval: int = 60) -> None:
    t = _poller.get("t")
    if t and t.is_alive():
        return
    if not any(b["status"] == "bezig" for b in batches()):
        return
    t = threading.Thread(target=_poll, args=(interval,), daemon=True, name="batch-poller")
    _poller["t"] = t
    t.start()


def _poll(interval: int) -> None:
    import time
    while True:
        open_ = [b for b in batches() if b["status"] == "bezig"]
        if not open_:
            return
        try:
            client = claude.maak_client()
            for b in open_:
                r = client.messages.batches.retrieve(b["id"])
                if getattr(r, "processing_status", "") == "ended":
                    _verwerk_batch(client, b)
        except Exception:  # noqa: BLE001
            log.exception("Batch controleren mislukt")
        time.sleep(interval)


def _verwerk_batch(client, b: dict) -> None:
    uit = {}
    for e in client.messages.batches.results(b["id"]):
        uit[e.custom_id] = e.result
    goed = fout = 0
    for bid, info in b["delen"].items():
        with opslag.db() as con:
            rij = con.execute("SELECT * FROM bronnen WHERE bron_id = ?", (int(bid),)).fetchone()
        if not rij:
            continue
        uitkomsten, melding = [], ""
        for i in range(info["n"]):
            res = uit.get(f"b{bid}d{i}")
            if res is None or res.type != "succeeded":
                melding = f"Batch: {getattr(res, 'type', 'geen antwoord')}"
                break
            msg = res.message
            if msg.stop_reason == "max_tokens":
                melding = "Batch: antwoord afgekapt"
                break
            g = msg.usage
            claude.log_usage("index (batch)", dict(rij)["naam"], getattr(msg, "model", "") or claude.model_voor("index"), g, batch=True)
            try:
                uitkomsten.append(json.loads("".join(c.text for c in msg.content if c.type == "text")))
            except json.JSONDecodeError:
                melding = "Batch: geen geldige JSON"
                break
        if melding:
            fout += 1
            with opslag.db() as con:
                con.execute("UPDATE bronnen SET status = 'fout', melding = ? WHERE bron_id = ?", (melding, int(bid)))
        else:
            _opslaan(dict(rij), uitkomsten, info["lengte"])
            goed += 1
    with _batch_slot:
        lijst = batches()
        for x in lijst:
            if x["id"] == b["id"]:
                x.update(status="klaar", klaar=opslag.nu(), goed=goed, fout=fout)
        _bewaar_batches(lijst)
    try:
        schrijf_drive()
    except Exception as e:  # noqa: BLE001
        log.warning("Drive bijwerken na batch mislukt: %s", e)


def start_indexeren(bron_ids: list[int]) -> bool:
    with _job_slot:
        if JOB["bezig"]:
            return False
        JOB.update(bezig=True, gedaan=0, totaal=len(bron_ids), tekst="Bezig met lezen", fouten=[], klaar="")
    claude.abonnement_weer_proberen()
    threading.Thread(target=_indexeer_alles, args=(bron_ids,), daemon=True).start()
    return True


def _indexeer_alles(bron_ids: list[int]) -> None:
    try:
        client = claude.maak_client()
        with opslag.db() as con:
            vt = ",".join("?" * len(bron_ids)) or "''"
            bronnen = [dict(r) for r in con.execute(f"SELECT * FROM bronnen WHERE bron_id IN ({vt})", bron_ids)]

        gestopt: list[str] = []

        def een(b):
            if gestopt:                      # abonnement op: de rest blijft 'nieuw' en kan later
                return
            try:
                n = indexeer_bron(client, b)
                JOB["tekst"] = f"{b['naam']}: {n} fragmenten"
            except claude.ToestemmingNodig as fout:
                gestopt.append(str(fout))
                return
            except lezen.NietLeesbaar as fout:
                with opslag.db() as con:
                    con.execute("UPDATE bronnen SET status = 'overgeslagen', melding = ? WHERE bron_id = ?",
                                (str(fout), b["bron_id"]))
            except Exception as fout:  # noqa: BLE001
                log.exception("Indexeren mislukt: %s", b["naam"])
                JOB["fouten"].append(f"{b['naam']}: {fout}")
                with opslag.db() as con:
                    con.execute("UPDATE bronnen SET status = 'fout', melding = ? WHERE bron_id = ?",
                                (str(fout)[:300], b["bron_id"]))
            JOB["gedaan"] += 1

        with ThreadPoolExecutor(WERKERS) as pool:
            list(pool.map(een, bronnen))
        try:
            n = schrijf_drive()
            JOB["klaar"] = f"Klaar. De bibliotheek in Google Drive is bijgewerkt ({n} leerdoelen)."
        except Exception as fout:  # noqa: BLE001
            JOB["klaar"] = f"Klaar, maar de bibliotheek in Google Drive kon niet worden bijgewerkt: {fout}"
        if gestopt:
            JOB["klaar"] = ("Gestopt: je Claude-abonnement kan nu niet verder (limiet bereikt of niet ingelogd). "
                            f"{JOB['gedaan']} van de {JOB['totaal']} bestanden zijn gelezen; de rest staat nog op 'nieuw'. "
                            "Start het lezen later opnieuw, of vink 'Via batch met API-tegoed' aan als je daarvoor wilt betalen. "
                            "Er is geen API-tegoed gebruikt.")
    finally:
        JOB["bezig"] = False


# ---------------------------------------------------------------------------- gebruiken
def fragmenten_voor(code: str, niveau: str, thema: str = "", aantal: int = 10) -> list[dict]:
    with opslag.db() as con:
        rijen = [dict(r) for r in con.execute(
            "SELECT f.*, b.naam AS bron, b.niveau AS bron_niveau FROM fragmenten f JOIN bronnen b ON b.bron_id = f.bron_id "
            "WHERE f.leerdoel_codes LIKE ?", (f'%"{code}"%',))]
        if len(rijen) < 3 and thema:
            woorden = [w for w in re.findall(r"\w{5,}", thema.lower())][:3]
            for w in woorden:
                rijen += [dict(r) for r in con.execute(
                    "SELECT f.*, b.naam AS bron, b.niveau AS bron_niveau FROM fragmenten f JOIN bronnen b "
                    "ON b.bron_id = f.bron_id WHERE lower(f.titel) LIKE ? LIMIT 6", (f"%{w}%",))]
    gezien, uit = set(), []
    # eigen niveau eerst, examenvragen van het eigen niveau het meest waardevol als stijlvoorbeeld
    for r in sorted(rijen, key=lambda r: (r["bron_niveau"] != niveau and r["niveau"] != niveau, r["soort"] != "uitleg")):
        if r["fragment_id"] in gezien:
            continue
        gezien.add(r["fragment_id"])
        uit.append(r)
    les = [r for r in uit if r["soort"] != "examenvraag"][:aantal]
    ex = [r for r in uit if r["soort"] == "examenvraag"][:max(4, aantal // 2)]
    return les + ex


def beschikbaar(codes: list[str], niveau: str) -> dict[str, dict]:
    uit = {}
    for c in codes:
        fr = fragmenten_voor(c, niveau)
        bew = opslag.bewezen_items(c, niveau, 5)
        uit[c] = {"les": sum(1 for f in fr if f["soort"] != "examenvraag"),
                  "examen": sum(1 for f in fr if f["soort"] == "examenvraag"),
                  "bewezen": len(bew), "uids": [b["uid"] for b in bew]}
    return uit


# ---------------------------------------------------------------------------- Google Drive
def _veilig(t: str, n: int = 60) -> str:
    t = re.sub(r"[\\/:*?\"<>|]+", "-", t).strip(" .")
    return t[:n].rstrip(" .") or "Overig"


def schrijf_drive() -> int:
    """Schrijft per vak > thema > leerdoel een Markdown-bestand met lesmateriaal, examenvragen en
    goedgekeurde oefenopgaven. Alleen lesmateriaal: geen leerlingnummers of resultaten."""
    doel = laad_instellingen().get("bibliotheek_drive")
    if not doel:
        raise RuntimeError("Google Drive is niet gevonden. Vul de map in bij Instellingen.")
    doel = Path(doel)
    codes = te.alle_codes()
    with opslag.db() as con:
        fr = [dict(r) for r in con.execute(
            "SELECT f.*, b.naam AS bron, b.pad AS bronpad FROM fragmenten f JOIN bronnen b ON b.bron_id = f.bron_id")]
        items = [opslag.item_uit_rij(r) for r in con.execute("SELECT * FROM items WHERE status = 'goedgekeurd'")]
    per_code = defaultdict(lambda: {"les": [], "examen": [], "items": []})
    for f in fr:
        for c in json.loads(f["leerdoel_codes"] or "[]"):
            per_code[c]["examen" if f["soort"] == "examenvraag" else "les"].append(f)
    for it in items:
        per_code[it["leerdoel_code"]]["items"].append(it)
    drive = Path(laad_instellingen()["bibliotheek_drive"]).parent
    geschreven = 0
    per_vak = defaultdict(lambda: defaultdict(list))
    for code, d in sorted(codes.items()):
        inhoud = per_code.get(code)
        vakmap = doel / _veilig(f"{d['vak']} {d['sector']}")
        thema = _veilig(d.get("thema") or "Overig")
        per_vak[vakmap][thema].append(d)
        if not inhoud:
            continue
        (vakmap / thema).mkdir(parents=True, exist_ok=True)
        regels = [f"# {code}  {d['omschrijving']}", "", f"*{d.get('leerling_zin', '')}*", "",
                  f"Thema: **{d.get('thema', '')}**  ", f"Bijgewerkt: {datetime.now():%d-%m-%Y %H:%M} door de oefentool", ""]
        eindt = sorted({f["eindtermen"] for f in inhoud["les"] + inhoud["examen"] if f.get("eindtermen")})
        if eindt:
            regels += ["## Eindtermen", *[f"- {e}" for e in eindt], ""]
        if inhoud["les"]:
            regels += ["## Lesmateriaal", ""]
            for f in inhoud["les"]:
                rel = os.path.relpath(f["bronpad"], drive) if f["bronpad"].startswith(str(drive)) else f["bronpad"]
                regels.append(f"- **{f['titel']}** ({f['soort']}) — {f['bron']}{(', p. ' + f['paginas']) if f['paginas'] else ''}  ")
                regels.append(f"  {f['kern']}  ")
                regels.append(f"  <small>`{rel}`</small>")
            regels.append("")
        if inhoud["examen"]:
            regels += ["## Examenvragen", ""]
            for f in sorted(inhoud["examen"], key=lambda f: f.get("examen") or ""):
                regels.append(f"- **{f.get('examen', '')} vraag {f.get('vraagnr', '')}** ({f.get('punten') or '?'}p): {f.get('vraag', '')}  ")
                if f.get("antwoord"):
                    regels.append(f"  *Antwoord:* {f['antwoord']}")
            regels.append("")
        if inhoud["items"]:
            regels += ["## Goedgekeurde oefenopgaven", ""]
            for it in inhoud["items"]:
                regels.append(f"### {it['uid']}  {it['inhoud'].get('titel', '')} (stap {it['stap']}, {it['inhoud'].get('punten')}p)")
                regels.append(claude._item_als_tekst(it["inhoud"]).split("\n", 1)[-1])
                if it.get("p_waarde") is not None:
                    regels.append(f"\n*Resultaat bij leerlingen: P = {it['p_waarde']:.2f}"
                                  + (f", Rit = {it['rit']:.2f}" if it.get("rit") is not None else "") + "*")
                regels.append("")
        bestand = vakmap / thema / f"{_veilig(code + ' ' + d['omschrijving'], 90)}.md"
        bestand.write_text("\n".join(regels), encoding="utf-8")
        geschreven += 1
    for vakmap, themas in per_vak.items():
        vakmap.mkdir(parents=True, exist_ok=True)
        regels = [f"# Bibliotheek {vakmap.name}", "", "Per thema (concept) de leerdoelen; daaronder per eindterm.", ""]
        per_eindterm = defaultdict(list)
        for thema, doelen in sorted(themas.items()):
            regels.append(f"## {thema}")
            for d in doelen:
                inh = per_code.get(d["code"], {"les": [], "examen": [], "items": []})
                regels.append(f"- {d['code']} {d['omschrijving']} — {len(inh['les'])} lesfragmenten, "
                              f"{len(inh['examen'])} examenvragen, {len(inh['items'])} oefenopgaven")
                for f in inh["les"] + inh["examen"]:
                    for e in re.split(r"[;,]\s*", f.get("eindtermen") or ""):
                        if e.strip():
                            per_eindterm[e.strip()].append(d["code"])
            regels.append("")
        if per_eindterm:
            regels += ["## Per eindterm", ""]
            for e, cs in sorted(per_eindterm.items()):
                regels.append(f"- **{e}**: {', '.join(sorted(set(cs)))}")
        (vakmap / "INDEX.md").write_text("\n".join(regels), encoding="utf-8")
    return geschreven
