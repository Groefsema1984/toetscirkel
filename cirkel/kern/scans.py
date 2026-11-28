# -*- coding: utf-8 -*-
"""Fase 3: een scan in de OneDrive-map 'ToetsCirkel Scans' herkennen.

Hier staat alleen de logica die zonder app en zonder Claude te testen is:
- welke bestanden in de map klaar zijn (OneDrive is klaar met synchroniseren);
- wat de bestandsnaam al zegt (AT-nummer, klas, versie);
- welke gebouwde toets uit Toetsconstructie erbij hoort, met welk correctievoorschrift;
- het logboek van verwerkte scans (tabel scans in de centrale database).

Herkennen gaat in deze volgorde, de eerste die zeker is wint:
0. het scanvoorblad bovenop de stapel (portaal /voorblad): de regel 'TC-VOORBLAD: project / versie / klas / datum';
1. een map in de inbox met zelf een correctievoorschrift erin (zoals de oude Toetsevaluatie): de mapnaam is de toets;
2. de bestandsnaam of de tekstlaag van de scan noemt het AT-nummer (en zo nodig de versie);
3. wat Claude op de eerste pagina leest (AT-nummer, versie, leerlingnummers -> klas via de klassenlijsten),
   aangevuld met de afnamedatum: de gebouwde toets van die klas die het dichtst bij de scandatum ligt.
Twijfel (meer dan één toets mogelijk, of geen) betekent: niets raden, de scan gaat naar Twijfel/ met de reden erbij.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import database

VERWERKT, TWIJFEL = "Verwerkt", "Twijfel"
RUST_SECONDEN = 60                   # zo lang ongewijzigd voordat een scan als compleet geldt (OneDrive)
DATUM_MARGE_DAGEN = 21               # een gebouwde toets past bij een scan als de datum hooguit zo ver weg ligt
TWEEFASEN_STANDAARD = {"4V", "5V"}   # zoals het uploadscherm van de Toetsevaluatie

MAANDEN = {m: i for i, m in enumerate(["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
                                       "september", "oktober", "november", "december"], 1)}

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    nr INTEGER PRIMARY KEY AUTOINCREMENT, bestand TEXT NOT NULL, moment TEXT NOT NULL,
    status TEXT NOT NULL, toets_id TEXT, project TEXT, reden TEXT
);
"""


# ------------------------------------------------------------------------------------------ bestanden
def _bestanden(pad: Path) -> list[Path]:
    return [pad] if pad.is_file() else [p for p in pad.rglob("*") if p.is_file()]


def klaar_in_inbox(inbox: Path, nu: float | None = None, rust: int = RUST_SECONDEN) -> list[Path]:
    """PDF's en mappen in de inbox die al even niet zijn veranderd. Verwerkt/, Twijfel/ en verborgen bestanden niet."""
    import time
    nu = nu or time.time()
    uit = []
    if not inbox.is_dir():
        return uit
    for p in sorted(inbox.iterdir()):
        if p.name.startswith((".", "~", "_")) or p.name in (VERWERKT, TWIJFEL):
            continue
        if p.is_file() and p.suffix.lower() != ".pdf":
            continue
        inhoud = _bestanden(p)
        if p.is_dir() and not any(f.suffix.lower() == ".pdf" and not f.name.startswith((".", "~")) for f in inhoud):
            continue
        try:
            jongste = max(f.stat().st_mtime for f in inhoud)
        except (OSError, ValueError):
            continue
        if nu - jongste >= rust:
            uit.append(p)
    return uit


def verplaats(pad: Path, inbox: Path, naar: str, reden: str = "") -> Path:
    """Zet een scan (of map) in Verwerkt/ of Twijfel/, met datum ervoor zodat niets wordt overschreven."""
    doelmap = inbox / naar
    doelmap.mkdir(exist_ok=True)
    doel = doelmap / f"{datetime.now():%Y%m%d_%H%M%S}_{pad.name}"
    pad.rename(doel)
    if reden:
        doel.with_name(doel.name + ".txt" if pad.is_dir() else doel.stem + ".txt").write_text(reden + "\n", encoding="utf-8")
    return doel


# ------------------------------------------------------------------------------------------ hints uit tekst
def klascode(tekst: str, niveau: str = "") -> str:
    """'5 havo', '5H2', 'havo5', '5h.beco' -> '5H'; 'mavo4', '4M' -> 'mavo4'. Leeg als het niet te zien is."""
    t = (tekst or "").lower()
    m = re.search(r"(?<![a-z0-9])(\d)\s*(?:h|havo)(?![a-z])|(?<![a-z])(?:havo)\s*(\d)", t)
    if m:
        return f"{m.group(1) or m.group(2)}H"
    m = re.search(r"(?<![a-z0-9])(\d)\s*(?:v|vwo|ath|gym)(?![a-z])|(?<![a-z])(?:vwo)\s*(\d)", t)
    if m:
        return f"{m.group(1) or m.group(2)}V"
    m = re.search(r"(?<![a-z0-9])(\d)\s*(?:m|mavo|tl|vmbo)(?![a-z])|(?<![a-z])(?:mavo|vmbo)\s*(\d)", t)
    if m:
        return f"mavo{m.group(1) or m.group(2)}"
    m = re.search(r"(?<![a-z0-9.])(\d)(?![0-9.])", t)
    if m and niveau:
        return klascode(f"{m.group(1)} {niveau}")
    return ""


def at_code(tekst: str) -> str:
    m = re.search(r"(?<![a-z])at\s*[-_]?\s*(\d{1,2})\s*[.,_]\s*(\d{1,2})(?!\d)", (tekst or "").lower())
    return f"AT{m.group(1)}.{m.group(2)}" if m else ""


def versie(tekst: str) -> str:
    t = tekst or ""
    m = re.search(r"versie\s*[-_]?\s*([A-D])\b", t, re.I) or re.search(r"(?:^|[_\s-])([AB])(?:\.pdf)?$", t.strip())
    return m.group(1).upper() if m else ""


def vak(tekst: str) -> str:
    t = (tekst or "").lower()
    if "bedrijfseconomie" in t or re.search(r"(^|[^a-z])(beco|be)([^a-z]|$)", t):
        return "Bedrijfseconomie"
    if "economie" in t or re.search(r"(^|[^a-z])(eco|ec)([^a-z]|$)", t):
        return "Economie"
    return ""


def hints(tekst: str) -> dict:
    return {"at": at_code(tekst), "klas": klascode(tekst), "versie": versie(tekst), "vak": vak(tekst),
            "tweefasen": None if "tweefasen" not in (tekst or "").lower() else not re.search(r"geen[\s_-]*tweefasen", tekst, re.I),
            "oe": bool(re.search(r"(^|_)OE(_|$)", tekst or ""))}


VOORBLAD_RE = re.compile(r"TC[\s-]*VOORBLAD\s*[:\-]?\s*([A-Za-z0-9_.\-]+)\s*/\s*([A-D-])\s*/\s*([A-Za-z0-9]+)\s*/\s*(\d{4}-\d{2}-\d{2})", re.I)


def voorblad_code(t: "Toets", datum: date) -> str:
    """De regel die op het scanvoorblad staat en die ToetsCirkel terugleest."""
    return f"TC-VOORBLAD: {t.project} / {t.versie or '-'} / {t.klas} / {datum.isoformat()}"


def lees_voorblad(tekst: str) -> dict | None:
    m = VOORBLAD_RE.search(tekst or "")
    if not m:
        return None
    try:
        datum = date.fromisoformat(m.group(4))
    except ValueError:
        return None
    return {"project": m.group(1), "versie": m.group(2).upper().replace("-", ""), "klas": klascode(m.group(3)) or m.group(3),
            "datum": datum}


def lees_datum(tekst: str) -> date | None:
    """'22 oktober 2026' of '2026-10-22' -> date."""
    t = (tekst or "").strip().lower()
    try:
        return date.fromisoformat(t[:10])
    except ValueError:
        pass
    m = re.search(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", t)
    if m and m.group(2) in MAANDEN:
        try:
            return date(int(m.group(3)), MAANDEN[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------------------------------ gebouwde toetsen
@dataclass
class Toets:
    project: str
    naam: str
    vak: str
    niveau: str
    klas: str                      # klascode zoals de Toetsevaluatie die kent: 5H, 4V, mavo3
    at: str
    versie: str
    cv: Path
    datum: date | None = None
    minuten: int | None = None
    oe: bool = False
    versies_in_project: int = 1
    extra: dict = field(default_factory=dict)

    def toets_id(self, tweefasen: bool | None = None) -> str:
        code = re.sub(r"[^A-Za-z0-9.\-]", "", self.at or self.project)[:30] or "Toets"
        tf = (self.klas in TWEEFASEN_STANDAARD) if tweefasen is None else tweefasen
        uit = f"{code}_{'OE' if self.oe else 'SE'}_{self.klas}_{self.vak}"
        if self.versies_in_project > 1 and self.versie:
            uit += f"_v{self.versie}"
        return uit + ("_tweefasen" if tf else "")


def gebouwde_toetsen(toetsen_map: Path) -> list[Toets]:
    """Elke versie van elke gebouwde toets in Toetsconstructie die een correctievoorschrift heeft."""
    uit = []
    for pj in sorted(toetsen_map.glob("*/project.json")) if toetsen_map.is_dir() else []:
        d = pj.parent
        try:
            pr = json.loads(pj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cvs = sorted((d / "uit").glob("*_correctievoorschrift.docx")) if (d / "uit").is_dir() else []
        if not cvs:
            continue
        per_versie = []
        for cv in cvs:
            meta = {}
            v = versie(cv.name.replace("_correctievoorschrift.docx", ""))
            tj = d / f"toets_{v or 'A'}.json"
            if tj.exists():
                try:
                    meta = json.loads(tj.read_text(encoding="utf-8")).get("meta", {})
                except (OSError, ValueError):
                    meta = {}
            niveau = meta.get("niveau") or pr.get("niveau") or ""
            klas = (klascode(meta.get("klas", ""), niveau) or klascode(pr.get("klas", ""), niveau)
                    or klascode(cv.name, niveau) or klascode(at_code(meta.get("at") or pr.get("at") or "")[2:3], niveau))
            per_versie.append(Toets(
                project=pr.get("id") or d.name, naam=pr.get("naam") or d.name, vak=meta.get("vak") or pr.get("vak") or vak(cv.name),
                niveau=niveau, klas=klas, at=at_code(meta.get("at") or pr.get("at") or cv.name) or (meta.get("at") or pr.get("at") or ""),
                versie=meta.get("versie") or v, cv=cv, datum=lees_datum(meta.get("datum", "")),
                minuten=int(meta["minuten"]) if str(meta.get("minuten", "")).isdigit() else None))
        for t in per_versie:
            t.versies_in_project = len(per_versie)
        uit += per_versie
    return uit


# ------------------------------------------------------------------------------------------ herkennen
@dataclass
class Herkenning:
    ok: bool
    reden: str = ""
    toets: Toets | None = None
    toets_id: str = ""
    datum: date | None = None
    scan: Path | None = None           # de PDF met het leerlingwerk
    cv: Path | None = None
    klassenlijst: Path | None = None
    kandidaten: list[str] = field(default_factory=list)
    voorblad: bool = False             # pagina 1 is het scanvoorblad: niet nakijken


def _kies(kandidaten: list[Toets], h: dict, scandatum: date) -> tuple[list[Toets], str]:
    """Filter op wat we weten. Geeft (overgebleven, waarom twijfel als er niet precies één over is)."""
    for sleutel, veld in (("vak", "vak"), ("klas", "klas"), ("at", "at")):
        if h.get(sleutel):
            nauw = [t for t in kandidaten if (getattr(t, veld) or "").lower() == h[sleutel].lower()]
            if nauw:
                kandidaten = nauw
            elif sleutel == "at":
                return [], f"Er is geen gebouwde toets met {h['at']} gevonden."
    projecten = {t.project for t in kandidaten}
    if len(projecten) > 1:
        dichtbij = [t for t in kandidaten if t.datum and abs((t.datum - scandatum).days) <= DATUM_MARGE_DAGEN]
        if dichtbij and len({t.project for t in dichtbij}) < len(projecten):
            kandidaten = dichtbij
    if len({t.project for t in kandidaten}) > 1:
        return kandidaten, "Meer dan één gebouwde toets past bij deze scan."
    if len(kandidaten) > 1:
        if h.get("versie"):
            kandidaten = [t for t in kandidaten if t.versie == h["versie"]] or kandidaten
        if len(kandidaten) > 1:
            return kandidaten, ("Deze toets heeft versie " + " en ".join(sorted(t.versie for t in kandidaten))
                                + ". Zet 'versie A' of 'versie B' in de bestandsnaam, of scan de versies apart.")
    if not kandidaten:
        return [], "Geen gebouwde toets gevonden die bij deze scan past."
    return kandidaten, ""


def herken(pad: Path, toetsen: list[Toets], *, tekstlaag: str = "", lees_kop=None, klas_van=None,
           lees_toetsmap=None) -> Herkenning:
    """pad: een PDF of map in de inbox.
    lees_kop(pdf) -> {'voorblad', 'at_code', 'versie', 'titel', 'leerlingnummers'} (Claude leest de eerste pagina), optioneel.
    klas_van(leerlingnummer) -> klasnaam uit de klassenlijsten, optioneel.
    lees_toetsmap(naam) -> geldig of ValueError (de regels van de Toetsevaluatie voor een toetsnaam), optioneel."""
    scandatum = date.fromtimestamp(max(f.stat().st_mtime for f in _bestanden(pad)))
    if pad.is_dir():
        pdfs = sorted(f for f in pad.iterdir() if f.suffix.lower() == ".pdf" and not f.name.startswith((".", "~"))
                      and not re.search(r"correctie|opgaven|bijlage", f.name, re.I))
        cvs = sorted(f for f in pad.iterdir() if re.search(r"correctie", f.name, re.I) and f.suffix.lower() in (".docx", ".pdf"))
        lijst = next((f for f in sorted(pad.iterdir()) if f.suffix.lower() in (".csv", ".xlsx")
                      and not f.name.startswith(("~$", "."))), None)
        if len(pdfs) != 1:
            return Herkenning(False, f"In de map {pad.name} staan {len(pdfs)} scans; zet er precies één in.", datum=scandatum)
        if cvs:
            try:
                if lees_toetsmap:
                    lees_toetsmap(pad.name)
            except ValueError as fout:
                return Herkenning(False, f"De mapnaam is geen geldige toetsnaam: {fout}", datum=scandatum)
            return Herkenning(True, toets_id=pad.name, datum=scandatum, scan=pdfs[0], cv=cvs[0], klassenlijst=lijst)
        scan, naam = pdfs[0], f"{pad.name} {pdfs[0].name}"
    else:
        scan, naam, lijst = pad, pad.name, None

    # Het scanvoorblad (bovenop de stapel) is zeker: dat wint van alles.
    kop = None
    vb = lees_voorblad(tekstlaag)
    if vb is None and lees_kop is not None:
        kop = lees_kop(scan) or {}
        vb = lees_voorblad(str(kop.get("voorblad") or ""))
    if vb is not None:
        passend = [t for t in toetsen if t.project == vb["project"] and (t.versie or "") == vb["versie"]]
        if len(passend) != 1:
            return Herkenning(False, f"Het scanvoorblad noemt toets {vb['project']} versie {vb['versie'] or '-'}, maar die staat "
                              "niet (meer) bij de gebouwde toetsen in Construeren. Bouw de toets opnieuw of print een nieuw voorblad.",
                              datum=vb["datum"], scan=scan)
        t = passend[0]
        t.klas = vb["klas"] or t.klas
        return Herkenning(True, toets=t, toets_id=t.toets_id(), datum=vb["datum"], scan=scan, cv=t.cv,
                          klassenlijst=lijst, voorblad=True)

    h = hints(naam)
    for sleutel, waarde in hints(tekstlaag).items():
        if not h.get(sleutel) and waarde:
            h[sleutel] = waarde

    bewijs = lambda: any(h.get(k) for k in ("at", "klas", "vak"))  # noqa: E731  (zonder enig bewijs nooit kiezen)
    kandidaten, reden = _kies(list(toetsen), h, scandatum)
    if (reden or not kandidaten or not bewijs()) and lees_kop is not None:
        kop = kop if kop is not None else (lees_kop(scan) or {})
        gelezen = hints(" ".join(str(kop.get(k) or "") for k in ("at_code", "titel")) + " versie " + str(kop.get("versie") or ""))
        for sleutel in ("at", "versie", "vak", "klas"):
            if not h.get(sleutel) and gelezen.get(sleutel):
                h[sleutel] = gelezen[sleutel]
        if not h.get("klas") and klas_van:
            klassen = [klascode(klas_van(str(n)) or "") for n in kop.get("leerlingnummers") or []]
            klassen = [k for k in klassen if k]
            if klassen:
                h["klas"] = max(set(klassen), key=klassen.count)
        kandidaten, reden = _kies(list(toetsen), h, scandatum)

    if not reden and not bewijs():
        reden = "Op de scan en in de bestandsnaam staat niets waaraan de toets of de klas te herkennen is."
    if reden or len(kandidaten) != 1:
        namen = sorted({f"{t.naam} ({t.at or 'geen AT'}, {t.klas}, versie {t.versie or '-'})" for t in kandidaten})
        uitleg = reden + (" Mogelijk: " + "; ".join(namen[:6]) + "." if namen else "")
        uitleg += " Zet het AT-nummer (en de versie) in de bestandsnaam, bijvoorbeeld 'AT5.21 5H versie A.pdf', en zet de scan terug in de map."
        return Herkenning(False, uitleg.strip(), datum=scandatum, scan=scan, kandidaten=namen)
    t = kandidaten[0]
    t.klas = t.klas or h.get("klas", "")
    if not t.klas:
        return Herkenning(False, f"Van {t.naam} is de klas (bijvoorbeeld 5H) onbekend. Zet die in de bestandsnaam.",
                          datum=scandatum, scan=scan)
    t.oe = t.oe or h.get("oe", False)
    return Herkenning(True, toets=t, toets_id=t.toets_id(h.get("tweefasen")), datum=scandatum, scan=scan, cv=t.cv,
                      klassenlijst=lijst)


# ------------------------------------------------------------------------------------------ logboek
def log_scan(bestand: str, status: str, toets_id: str = "", project: str = "", reden: str = "", pad: Path | None = None) -> None:
    con = database.verbind(pad)
    try:
        with con:
            con.executescript(SCHEMA)
            con.execute("INSERT INTO scans (bestand, moment, status, toets_id, project, reden) VALUES (?, ?, ?, ?, ?, ?)",
                        (bestand, datetime.now().isoformat(timespec="seconds"), status, toets_id, project, reden))
    finally:
        con.close()


def logboek(limiet: int = 50, pad: Path | None = None) -> list[dict]:
    con = database.verbind(pad)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'scans'").fetchone():
            return []
        return [dict(r) for r in con.execute("SELECT * FROM scans ORDER BY nr DESC LIMIT ?", (limiet,))]
    finally:
        con.close()
