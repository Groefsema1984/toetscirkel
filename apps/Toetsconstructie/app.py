"""Toetsconstructie-app: lokale server.
Start met start.command (Mac) of: python3 app.py   ->  http://localhost:5057
De app beheert bronnen, toetsprojecten en de toetsmatrijs, en laat Claude Code (claude -p) op de achtergrond het werk doen."""
import datetime, hashlib, html, io, json, os, re, shutil, subprocess, sys, threading, uuid, zipfile
from pathlib import Path
from flask import Flask, Response, abort, jsonify, render_template, request, send_file

ROOT = Path(__file__).resolve().parent
BRONNEN, KENNIS, TOETSEN, CACHE = ROOT / "bronnen", ROOT / "kennisbank", ROOT / "toetsen", ROOT / "bronnen" / ".tekst"
INSTEL = ROOT / "instellingen.json"
# Eigen Python-omgeving, eventueel lokaal geplaatste Node.js en de gebruikelijke plekken van Claude Code vooraan in het zoekpad,
# zodat ook de opdrachten die Claude op de achtergrond uitvoert (node, python3) de juiste versies vinden.
os.environ["PATH"] = os.pathsep.join([str(ROOT / ".venv" / "bin"), str(ROOT / ".node" / "bin"), str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", os.environ.get("PATH", "")])
def schone_env():
    """Omgeving voor Claude Code zonder API-sleutel: zo loopt het gebruik altijd via het abonnement en nooit via betaald API-tegoed."""
    e = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"): e.pop(k, None)
    return e
for d in (BRONNEN, KENNIS, TOETSEN, CACHE): d.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "bouw"))
import itemanalyse, kwaliteit, leerdoelen, vragenbank as vbank  # rekenmodules in de map bouw
LEERREGELS = KENNIS / "leerregels.json"
app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 400 * 1024 * 1024
LOPEND = {}  # project-id -> subprocess

# ---------------------------------------------------------------- hulpfuncties
def lees_json(p, standaard=None):
    try: return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception: return standaard
def schrijf_json(p, data): Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
def instellingen():
    s = lees_json(INSTEL, {}) or {}
    s.setdefault("externe_mappen", []); s.setdefault("model", "")
    s.setdefault("toetsevaluatie_db", str(Path.home() / "Documents" / "Toetsevaluatie" / "toetsevaluatie.db")); return s
def nu(): return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
def claude_pad():
    for kandidaat in (shutil.which("claude"), str(Path.home() / ".claude/local/claude"), str(Path.home() / ".local/bin/claude"), "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        if kandidaat and Path(kandidaat).exists(): return kandidaat
    return None

def wortels():
    """Alle mappen met bronnen: sleutel -> pad. Externe mappen worden alleen gelezen."""
    w = {"kennisbank": KENNIS, "bronnen": BRONNEN, "voorbeelden": ROOT / "voorbeelden"}
    for i, m in enumerate(instellingen()["externe_mappen"]):
        if Path(m).is_dir(): w[f"extern{i + 1}"] = Path(m)
    return w
def naar_pad(sleutelpad):
    """'bronnen/economie/x.pdf' of 'extern1/x.pdf' -> absoluut pad, alleen binnen een toegestane map."""
    deel = str(sleutelpad).split("/", 1)
    w = wortels()
    if deel[0] not in w and deel[0] != "toetsen": abort(400, "onbekende map")
    basis = TOETSEN if deel[0] == "toetsen" else w[deel[0]]
    p = (basis / (deel[1] if len(deel) > 1 else "")).resolve()
    if basis.resolve() not in p.parents and p != basis.resolve(): abort(400, "pad buiten de map")
    return p

def raad(naam, map_=""):
    """Leidt vak, niveau en soort af uit de bestandsnaam en de map."""
    t = (map_ + "/" + naam).upper().replace("-", "_")
    vak = ("bedrijfseconomie" if re.search(r"BECO|BEDRIJFS|CUMULUS|VENNOOTSCHAP|EENMANSZAAK|BREAK_?EVEN|KOSTPRIJS", t)
           else "economie" if re.search(r"(^|[_/ ])ECO(NOMIE)?([_/ .]|$)|VAK_ECONOMIE|/ECONOMIE|EINDTERMEN|KRINGLOOP|LORENZ|ECONOMISCHE|VERZEKER|MARKT|SPELTHEORIE|WELVAART|CONJUNCTUUR", t) else "algemeen")
    niveau = "vwo" if "VWO" in t else "havo" if "HAVO" in t else "mavo" if re.search(r"MAVO|VMBO", t) else ""
    soort = ("opdracht" if "/OPDRACHTEN/" in t else "richtlijn" if re.search(r"/00_|RICHTLIJN|PTA", t) else "eindtermen" if "EINDTERM" in t or "SYLLABUS" in t else "examen" if re.search(r"EXAMEN|TIJDVAK|_CV_|OPGAVE", t)
             else "rekenregels" if "REKENREGEL" in t else "methode" if re.search(r"CUMULUS|METHODE|HOOFDSTUK", t) else "kader" if "KADER" in t or "DEFINITIE" in t else "didactiek" if "DIDACT" in t else "overig")
    return vak, niveau, soort

def tekst_cache(p: Path):
    """Geeft het pad van de platte tekst van een pdf of Word-bestand (maakt die zo nodig aan)."""
    ext = p.suffix.lower()
    if ext in (".txt", ".md", ".json", ".csv"): return p
    if ext not in (".pdf", ".docx"): return None
    st = p.stat(); doel = CACHE / (hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16] + "_" + re.sub(r"[^A-Za-z0-9_.-]", "_", p.stem)[:60] + ".txt")
    if doel.exists(): return doel
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            r = PdfReader(str(p)); delen = [f"--- pagina {i + 1} ---\n{(pg.extract_text() or '').strip()}" for i, pg in enumerate(r.pages)]
            tekst = "\n\n".join(delen)
        else:
            import docx
            d = docx.Document(str(p)); delen = [par.text for par in d.paragraphs if par.text.strip()]
            for t in d.tables:
                for rij in t.rows: delen.append(" | ".join(c.text.strip() for c in rij.cells))
            tekst = "\n".join(delen)
        doel.write_text(tekst, encoding="utf-8"); return doel
    except Exception as e:
        doel.write_text(f"(tekst kon niet worden uitgelezen: {e})", encoding="utf-8"); return doel

def lijst_bronnen():
    uit = []
    for sleutel, basis in wortels().items():
        try: paden = sorted(basis.rglob("*"))
        except OSError: continue  # map niet leesbaar (bijvoorbeeld geen toestemming van macOS)
        for p in paden:
            if not p.is_file() or any(d.startswith(".") for d in p.relative_to(basis).parts) or "/uit/" in str(p) or p.name == "leerregels.json": continue
            if p.suffix.lower() not in (".pdf", ".docx", ".txt", ".md", ".json", ".png", ".jpg", ".jpeg", ".html", ".csv"): continue
            if sleutel == "voorbeelden" and p.suffix != ".json": continue
            rel = p.relative_to(basis).as_posix(); vak, niveau, soort = raad(p.name, sleutel + "/" + rel)
            if sleutel == "voorbeelden": soort = "voorbeeld"
            uit.append({"pad": f"{sleutel}/{rel}", "naam": p.name, "map": sleutel, "submap": str(Path(rel).parent) if "/" in rel else "", "vak": vak, "niveau": niveau, "soort": soort,
                        "kb": round(p.stat().st_size / 1024), "gewijzigd": datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")})
    return uit

# ---------------------------------------------------------------- projecten
def project_map(pid):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", pid or ""): abort(400)
    d = TOETSEN / pid
    if not (d / "project.json").exists(): abort(404)
    return d
def bestanden_van(d):
    uit = d / "uit"
    return [{"naam": p.name, "kb": round(p.stat().st_size / 1024), "gewijzigd": datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%H:%M")} for p in sorted(uit.glob("*.docx"))] if uit.exists() else []
def bouwverslagen(d): return [lees_json(p) for p in sorted((d / "uit").glob("bouwverslag_*.json"))] if (d / "uit").exists() else []
OPDRACHT = "toetsopdracht.md"
def open_punten(tekst):
    """De punten onder het kopje 'Open punten' van de toetsopdracht. Leeg betekent: de opdracht is volledig."""
    m = re.search(r"^#{1,3}\s*Open punten.*?$(.*?)(?=^#{1,3}\s|\Z)", tekst or "", re.S | re.M | re.I)
    if not m: return ["De toetsopdracht heeft nog geen kopje 'Open punten'."]
    uit = []
    for r in m.group(1).splitlines():
        k = re.match(r"^\s*(?:[-*\u2022]|\d+[.)])\s+(.*)", r)
        if k and k.group(1).strip() and not re.match(r"^\(?\s*geen\b|^n\.?v\.?t", k.group(1).strip(), re.I): uit.append(k.group(1).strip())
    return uit
def werk_status_bij(d):
    pr = lees_json(d / "project.json"); opdr = (d / OPDRACHT).exists(); mat = (d / "matrijs.json").exists()
    if pr["status"] == "intake" and opdr: pr["status"] = "opdracht"
    elif pr["status"] == "intake" and mat: pr["status"] = "matrijs"  # oudere projecten zonder toetsopdracht
    if pr["status"] == "opdracht_ok" and mat: pr["status"] = "matrijs"
    if pr["status"] in ("goedgekeurd", "matrijs") and bestanden_van(d) and pr.get("goedgekeurd_op"): pr["status"] = "gebouwd"
    if pr["status"] == "gebouwd" and (d / "evaluatie.md").exists(): pr["status"] = "geevalueerd"
    schrijf_json(d / "project.json", pr); return pr
def bewaar_versie(d, wie):
    """Bewaart opdracht en matrijs als ze veranderd zijn, met wie ze veranderde. Zo kan Claude na afloop
    zien wat de docent aan zijn voorstellen heeft aangepast (bron voor kandidaat-leerregels)."""
    v = d / "versies"; v.mkdir(exist_ok=True)
    for naam in (OPDRACHT, "matrijs.json"):
        bron = d / naam
        if not bron.exists(): continue
        oud = sorted(v.glob(f"*_{naam}"))
        inhoud = bron.read_bytes()
        if oud and oud[-1].read_bytes() == inhoud: continue
        if wie == "docent" and oud and "_docent_" in oud[-1].name: oud[-1].unlink()  # alleen de laatste docentversie tussen twee beurten
        (v / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{wie}_{naam}").write_bytes(inhoud)
def kern_van(d):
    if not (d / OPDRACHT).exists(): return None, [], []
    kern, f = kwaliteit.lees_kern((d / OPDRACHT).read_text(encoding="utf-8"))
    f2, w = kwaliteit.controleer_kern(kern) if kern else ([], [])
    return kern, f + f2, w
def gesprek(d):
    p = d / "gesprek.jsonl"
    return [json.loads(r) for r in p.read_text(encoding="utf-8").splitlines() if r.strip()] if p.exists() else []
def log(d, rol, tekst, acties=None):
    with open(d / "gesprek.jsonl", "a", encoding="utf-8") as f: f.write(json.dumps({"rol": rol, "tekst": tekst, "acties": acties or [], "tijd": nu()}, ensure_ascii=False) + "\n")

# ---------------------------------------------------------------- routes: basis
@app.get("/")
def index(): return render_template("index.html")

@app.get("/api/status")
def status():
    c = claude_pad(); versie = ""; ingelogd = False
    if c:
        try: versie = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
        except Exception: versie = "gevonden"
        try:
            uit = subprocess.run([c, "auth", "status", "--json"], capture_output=True, text=True, timeout=20, env=schone_env()).stdout
            ingelogd = bool(json.loads(uit).get("loggedIn"))
        except Exception: ingelogd = True  # oudere versie zonder dit commando: niet blokkeren
    return jsonify({"claude": bool(c), "claude_versie": versie, "ingelogd": ingelogd, "node": bool(shutil.which("node")), "bouwmotor": (ROOT / "bouw" / "node_modules" / "docx").exists(), "map": str(ROOT), "instellingen": instellingen()})

@app.route("/api/instellingen", methods=["GET", "POST"])
def api_instellingen():
    if request.method == "POST":
        s = instellingen(); d = request.get_json(force=True)
        if "model" in d: s["model"] = str(d["model"])
        if "toetsevaluatie_db" in d: s["toetsevaluatie_db"] = str(d["toetsevaluatie_db"]).strip()
        if "externe_mappen" in d: s["externe_mappen"] = [m.strip() for m in d["externe_mappen"] if m.strip()]
        schrijf_json(INSTEL, s)
    s = instellingen(); s["mappen_gevonden"] = [Path(m).is_dir() for m in s["externe_mappen"]]; return jsonify(s)

# ---------------------------------------------------------------- routes: bronnen
@app.get("/api/bronnen")
def api_bronnen(): return jsonify(lijst_bronnen())

@app.post("/api/bronnen/upload")
def upload():
    vak, niveau, doel = request.form.get("vak", "auto"), request.form.get("niveau", "auto"), request.form.get("doel", "bronnen")
    opgeslagen = []
    for f in request.files.getlist("bestanden"):
        naam = re.sub(r"[^\w .()\-+]", "_", Path(f.filename).name).strip() or "bestand"
        gv, gn, _ = raad(naam)
        v = gv if vak == "auto" else vak; n = gn if niveau == "auto" else niveau
        basis = KENNIS if doel == "kennisbank" else BRONNEN
        d = basis / v / n if (n and doel != "kennisbank") else basis / v
        d.mkdir(parents=True, exist_ok=True); p = d / naam; f.save(str(p)); tekst_cache(p)
        opgeslagen.append(f"{'kennisbank' if doel == 'kennisbank' else 'bronnen'}/{p.relative_to(basis).as_posix()}")
    return jsonify({"opgeslagen": opgeslagen})

@app.post("/api/bronnen/verwijder")
def verwijder():
    pad = request.get_json(force=True)["pad"]
    if not pad.startswith(("bronnen/", "kennisbank/")): abort(400, "alleen eigen bronnen kunnen worden verwijderd")
    p = naar_pad(pad); prul = ROOT / ".prullenbak"; prul.mkdir(exist_ok=True)
    if p.is_file(): shutil.move(str(p), str(prul / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{p.name}"))
    return jsonify({"ok": True})

@app.get("/bestand")
def bestand():
    p = naar_pad(request.args["pad"])
    if not p.is_file(): abort(404)
    return send_file(str(p), as_attachment=request.args.get("download") == "1", download_name=p.name)

def docx_html(p):
    import docx
    from docx.table import Table as T
    from docx.text.paragraph import Paragraph as P
    d = docx.Document(str(p)); uit = []
    for el in d.element.body.iterchildren():
        if el.tag.endswith("}p"):
            par = P(el, d); t = html.escape(par.text)
            if not t.strip(): uit.append("<p class='leeg'></p>"); continue
            vet = par.runs and all(r.bold for r in par.runs if r.text.strip())
            uit.append(f"<p class='{'kop' if vet else ''}'>{t.replace(chr(9), ' &nbsp; ')}</p>")
        elif el.tag.endswith("}tbl"):
            t = T(el, d); uit.append("<table>" + "".join("<tr>" + "".join(f"<td>{html.escape(c.text).replace(chr(10), '<br>')}</td>" for c in r.cells) + "</tr>" for r in t.rows) + "</table>")
    return "\n".join(uit)

@app.get("/api/lees")
def lees():
    pad = request.args["pad"]; p = naar_pad(pad)
    if not p.is_file(): abort(404)
    ext = p.suffix.lower()
    if ext == ".pdf": return jsonify({"soort": "pdf", "naam": p.name})
    if ext in (".png", ".jpg", ".jpeg"): return jsonify({"soort": "beeld", "naam": p.name})
    if ext == ".docx":
        try: return jsonify({"soort": "html", "naam": p.name, "inhoud": docx_html(p)})
        except Exception as e: return jsonify({"soort": "tekst", "naam": p.name, "inhoud": f"Dit Word-bestand kon niet worden getoond: {e}"})
    inhoud = p.read_text(encoding="utf-8", errors="replace")
    if ext == ".json":
        try: inhoud = json.dumps(json.loads(inhoud), ensure_ascii=False, indent=2)
        except Exception: pass
    return jsonify({"soort": "tekst", "naam": p.name, "inhoud": inhoud[:600000]})

# ---------------------------------------------------------------- routes: toetsen
@app.get("/api/toetsen")
def toetsen():
    uit = []
    for d in sorted(TOETSEN.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        pr = lees_json(d / "project.json") if d.is_dir() else None
        if pr: uit.append(pr)
    return jsonify(uit)

@app.post("/api/toetsen")
def nieuwe_toets():
    d = request.get_json(force=True); naam = (d.get("naam") or "").strip() or f"{d.get('at', 'Toets')} {d.get('vak', '')} {d.get('klas', '')}".strip()
    pid = re.sub(r"[^A-Za-z0-9]+", "_", naam).strip("_")[:50] + "_" + uuid.uuid4().hex[:4]
    m = TOETSEN / pid; m.mkdir(parents=True)
    vak, niveau = d.get("vak", "Economie"), d.get("niveau", "Havo")
    # standaardselectie: de kennisbank van dit vak en de bronnen van dit vak en niveau
    sel = [b["pad"] for b in lijst_bronnen() if (b["map"] == "kennisbank" and b["vak"] in (vak.lower(), "algemeen")) or
           (b["map"] == "bronnen" and b["vak"] == vak.lower() and b["niveau"] in (niveau.lower(), ""))]
    rl = [b["pad"] for b in lijst_bronnen() if re.search(r"toetsconstructie_richtlijnen_v[\d_]+\.txt$", b["naam"])]
    versie = lambda x: tuple(int(n) for n in re.findall(r"\d+", re.search(r"_v([\d_]+)\.txt$", x).group(1)))
    if rl: nieuwste = max(rl, key=versie); sel = [x for x in sel if x not in rl] + [nieuwste]
    pr = {"id": pid, "naam": naam, "vak": vak, "niveau": niveau, "klas": d.get("klas", ""), "at": d.get("at", ""), "soort": d.get("soort", "schoolexamen"), "status": "intake",
          "selectie": sel, "sessie": None, "aangemaakt": nu()}
    schrijf_json(m / "project.json", pr); return jsonify(pr)

@app.get("/api/toetsen/<pid>")
def toets(pid):
    d = project_map(pid); pr = werk_status_bij(d)
    opdr = (d / OPDRACHT).read_text(encoding="utf-8") if (d / OPDRACHT).exists() else None
    kern, kf, kw = kern_van(d); mat = lees_json(d / "matrijs.json")
    ia = {p.stem.split("_", 1)[1]: lees_json(p) for p in sorted(d.glob("itemanalyse_*.json"))}
    ev = (d / "evaluatie.md").read_text(encoding="utf-8") if (d / "evaluatie.md").exists() else None
    return jsonify({"project": pr, "opdracht": opdr, "opdracht_open": open_punten(opdr) if opdr else [], "kern": kern, "kern_fouten": kf, "kern_waarschuwingen": kw,
                    "matrijs_afwijkingen": kwaliteit.vergelijk_matrijs(kern, mat) if kern and mat else [], "itemanalyses": ia, "evaluatie": ev, "matrijs": mat, "bestanden": bestanden_van(d), "verslagen": bouwverslagen(d), "gesprek": gesprek(d), "bezig": pid in LOPEND})

@app.put("/api/toetsen/<pid>")
def wijzig_toets(pid):
    d = project_map(pid); pr = lees_json(d / "project.json"); nieuw = request.get_json(force=True)
    for k in ("naam", "selectie", "klas", "at", "status"):
        if k in nieuw: pr[k] = nieuw[k]
    schrijf_json(d / "project.json", pr); return jsonify(pr)

@app.delete("/api/toetsen/<pid>")
def weg_toets(pid):
    d = project_map(pid); prul = ROOT / ".prullenbak"; prul.mkdir(exist_ok=True)
    shutil.move(str(d), str(prul / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{pid}")); return jsonify({"ok": True})

@app.put("/api/toetsen/<pid>/opdracht")
def bewaar_opdracht(pid):
    d = project_map(pid); t = request.get_json(force=True).get("tekst", "")
    (d / OPDRACHT).write_text(t, encoding="utf-8"); bewaar_versie(d, "docent"); kern, kf, kw = kern_van(d)
    return jsonify({"ok": True, "open": open_punten(t), "kern": kern, "kern_fouten": kf, "kern_waarschuwingen": kw})

@app.put("/api/toetsen/<pid>/matrijs")
def bewaar_matrijs(pid):
    d = project_map(pid); schrijf_json(d / "matrijs.json", request.get_json(force=True)); bewaar_versie(d, "docent")
    kern, _, _ = kern_van(d); return jsonify({"ok": True, "afwijkingen": kwaliteit.vergelijk_matrijs(kern, lees_json(d / "matrijs.json")) if kern else []})

@app.get("/api/toetsen/<pid>/download")
def download(pid):
    d = project_map(pid); naam = Path(request.args["bestand"]).name; p = d / "uit" / naam
    if not p.is_file(): abort(404)
    return send_file(str(p), as_attachment=True, download_name=naam)

@app.get("/api/toetsen/<pid>/zip")
def alles(pid):
    d = project_map(pid); buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((d / "uit").glob("*.docx")): z.write(p, p.name)
    buf.seek(0); return send_file(buf, as_attachment=True, download_name=f"{pid}.zip", mimetype="application/zip")

# ---------------------------------------------------------------- Claude op de achtergrond
def omschrijf(naam, inv):
    kort = lambda s: str(s).replace(str(ROOT) + "/", "")
    if naam == "Read": return "Leest " + kort(inv.get("file_path", ""))
    if naam in ("Write", "Edit", "MultiEdit", "NotebookEdit"): return "Schrijft " + kort(inv.get("file_path", ""))
    if naam == "Bash": return "Voert uit: " + kort(inv.get("command", ""))[:110]
    if naam == "WebSearch": return "Zoekt op internet: " + str(inv.get("query", ""))
    if naam == "WebFetch": return "Opent " + str(inv.get("url", ""))
    if naam in ("Glob", "Grep"): return "Zoekt in bestanden: " + str(inv.get("pattern", ""))
    return naam

def project_blok(pr, d):
    regels = ["[PROJECT]", f"map: toetsen/{pr['id']}", f"naam: {pr['naam']}", f"vak: {pr['vak']} | niveau: {pr['niveau']} | klas: {pr['klas']} | AT-nummer: {pr['at']} | soort: {pr['soort']}", f"status: {pr['status']}",
              f"matrijs.json aanwezig: {'ja' if (d / 'matrijs.json').exists() else 'nee'}", f"geselecteerde bronnen ({len(pr['selectie'])}):"]
    w = wortels()
    for pad in pr["selectie"]:
        try: p = naar_pad(pad)
        except Exception: continue
        if not p.is_file(): continue
        echt = p if pad.split("/", 1)[0].startswith("extern") else p.relative_to(ROOT)
        c = tekst_cache(p)
        regels.append(f"- {echt}" + (f" | tekst: {c.relative_to(ROOT)}" if c and c != p else ""))
    regels.append("[/PROJECT]"); return "\n".join(regels)

def sse(obj): return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

@app.post("/api/toetsen/<pid>/chat")
def chat(pid):
    d = project_map(pid); pr = lees_json(d / "project.json"); data = request.get_json(force=True)
    bericht = (data.get("bericht") or "").strip(); actie = data.get("actie")
    if actie == "opdracht_ok":
        if not (d / OPDRACHT).exists(): abort(400, "er is nog geen toetsopdracht")
        rest = open_punten((d / OPDRACHT).read_text(encoding="utf-8"))
        if rest: abort(400, f"er staan nog {len(rest)} open punten in de toetsopdracht")
        _, kf, _ = kern_van(d)
        if kf: abort(400, "de rekenbare kern klopt nog niet: " + kf[0])
        pr["status"] = "opdracht_ok"; pr["opdracht_goedgekeurd_op"] = nu(); schrijf_json(d / "project.json", pr)
        bericht = "TOETSOPDRACHT GOEDGEKEURD. Lees toetsopdracht.md opnieuw (de docent kan hem hebben aangepast) en maak de toetsmatrijs volgens stap 3 van de werkinstructie." + (("\n\nOpmerking van de docent: " + bericht) if bericht else "")
    elif actie == "goedkeuren":
        if (d / OPDRACHT).exists() and not pr.get("opdracht_goedgekeurd_op"): abort(400, "keur eerst de toetsopdracht goed")
        pr["status"] = "goedgekeurd"; pr["goedgekeurd_op"] = nu(); schrijf_json(d / "project.json", pr)
        bericht = "MATRIJS GOEDGEKEURD. Lees matrijs.json opnieuw en bouw de toets volgens stap 4 van de werkinstructie." + (("\n\nOpmerking van de docent: " + bericht) if bericht else "")
    elif actie == "evalueren":
        if not list(d.glob("itemanalyse_*.json")): abort(400, "haal eerst de scores op (itemanalyse)")
        bericht = "EVALUEREN. Evalueer de afname volgens stap 6 van de werkinstructie." + (("\n\nOpmerking van de docent: " + bericht) if bericht else "")
    elif actie == "afronden":
        try: vbank.toevoegen(d)
        except Exception: pass
        bericht = "AFRONDEN. Werk de kennisbank bij volgens stap 5 van de werkinstructie." + (("\n\nOpmerking van de docent: " + bericht) if bericht else "")
    if not bericht: abort(400, "leeg bericht")
    if pid in LOPEND: abort(409, "Claude is nog bezig met dit project")
    c = claude_pad()

    def stroom():
        if not c:
            yield sse({"type": "fout", "tekst": "Claude Code is niet gevonden op deze computer. Installeer Claude Code en log in met uw abonnement (zie LEESMIJ.txt)."}); return
        s = instellingen()
        cmd = [c, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages", "--permission-mode", "acceptEdits",
               "--allowedTools", "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch,Agent,Task"]
        if pr.get("sessie"): cmd += ["--resume", pr["sessie"]]
        if s["model"]: cmd += ["--model", s["model"]]
        for m in s["externe_mappen"]:
            if Path(m).is_dir(): cmd += ["--add-dir", m]
        log(d, "docent", data.get("toon") or bericht)
        try: proc = subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=schone_env())
        except Exception as e:
            yield sse({"type": "fout", "tekst": f"Claude Code kon niet worden gestart: {e}"}); return
        LOPEND[pid] = proc
        fouten = []
        threading.Thread(target=lambda: fouten.extend(proc.stderr.readlines()), daemon=True).start()
        try:
            proc.stdin.write(project_blok(pr, d) + "\n\n" + bericht); proc.stdin.close()
            tekst, acties, gestreamd, na_actie, sessie, eind = "", [], False, False, None, None
            for regel in proc.stdout:
                regel = regel.strip()
                if not regel.startswith("{"): continue
                try: ev = json.loads(regel)
                except Exception: continue
                t = ev.get("type")
                if t == "system" and ev.get("subtype") == "init": sessie = ev.get("session_id")
                elif t == "stream_event" and not ev.get("parent_tool_use_id"):
                    e = ev.get("event", {}); dl = e.get("delta", {})
                    if e.get("type") == "content_block_delta" and dl.get("type") == "text_delta":
                        stuk = dl.get("text", "")
                        if na_actie and tekst and not tekst.endswith("\n"): stuk = "\n\n" + stuk
                        na_actie = False; gestreamd = True; tekst += stuk; yield sse({"type": "tekst", "delta": stuk})
                elif t == "assistant" and not ev.get("parent_tool_use_id"):
                    for blok in ev.get("message", {}).get("content", []):
                        if blok.get("type") == "tool_use":
                            a = omschrijf(blok.get("name", ""), blok.get("input", {})); acties.append(a); na_actie = True; yield sse({"type": "actie", "tekst": a})
                        elif blok.get("type") == "text":
                            if not gestreamd:  # oudere versie zonder deelberichten: toon de tekst nu
                                stuk = (("\n\n" if na_actie and tekst else "") + blok.get("text", "")); na_actie = False
                                tekst += stuk; yield sse({"type": "tekst", "delta": stuk})
                            gestreamd = False
                elif t == "rate_limit_event":
                    info = ev.get("rate_limit_info", {}); vijf = (info.get("unifiedWindows") or {}).get("five_hour") or {}
                    yield sse({"type": "verbruik", "status": info.get("status"), "vijf_uur": vijf.get("utilization"), "reset": vijf.get("resetsAt") or info.get("resetsAt")})
                elif t == "result":
                    eind = ev; sessie = ev.get("session_id") or sessie
                    if not tekst and ev.get("result"): tekst = str(ev["result"]); yield sse({"type": "tekst", "delta": tekst})
            proc.wait()
            if sessie:
                p2 = lees_json(d / "project.json"); p2["sessie"] = sessie; schrijf_json(d / "project.json", p2)
            if proc.returncode not in (0, None) and not tekst:
                tekst = "Claude Code gaf een fout: " + ("".join(fouten).strip()[-600:] or f"code {proc.returncode}"); yield sse({"type": "fout", "tekst": tekst})
            log(d, "claude", tekst, acties)
            try: bewaar_versie(d, "claude")
            except Exception: pass
            nieuw = werk_status_bij(d)
            yield sse({"type": "klaar", "status": nieuw["status"], "fout": bool(eind and eind.get("is_error")), "kosten": (eind or {}).get("total_cost_usd"), "duur": (eind or {}).get("duration_ms")})
        finally:
            LOPEND.pop(pid, None)
            if proc.poll() is None: proc.terminate()
    return Response(stroom(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/api/toetsen/<pid>/stop")
def stop(pid):
    proc = LOPEND.get(pid)
    if proc and proc.poll() is None: proc.terminate()
    return jsonify({"ok": True})

# ---------------------------------------------------------------- routes: evaluatie, leerregels, vragenbank
@app.get("/api/leerdoelenregister")
def leerdoelenregister():
    """Vaste leerdoelcodes uit de centrale database, voor de kolom Code in de matrijs."""
    try: return jsonify({"leerdoelen": leerdoelen.register(request.args.get("vak"), request.args.get("niveau"), pad=Path(instellingen()["toetsevaluatie_db"]).expanduser())})
    except Exception as e: return jsonify({"leerdoelen": [], "fout": str(e)})

@app.get("/api/evaluatie/toetsen")
def evaluatie_toetsen():
    db = Path(instellingen()["toetsevaluatie_db"]).expanduser()
    if not db.exists(): return jsonify({"gevonden": False, "pad": str(db), "toetsen": []})
    try: return jsonify({"gevonden": True, "pad": str(db), "toetsen": itemanalyse.db_toetsen(db)})
    except Exception as e: return jsonify({"gevonden": False, "pad": str(db), "fout": str(e), "toetsen": []})

@app.post("/api/toetsen/<pid>/itemanalyse")
def itemanalyse_ophalen(pid):
    d = project_map(pid)
    if request.files.get("bestand"):
        versie = re.sub(r"[^A-Za-z0-9]", "", request.form.get("versie", "A"))[:3] or "A"
        try: ia = itemanalyse.uit_csv(request.files["bestand"].read().decode("utf-8", errors="replace"))
        except Exception as e: abort(400, f"CSV niet te lezen: {e}")
    else:
        g = request.get_json(force=True); versie = re.sub(r"[^A-Za-z0-9]", "", g.get("versie", "A"))[:3] or "A"
        try: ia = itemanalyse.uit_db(Path(instellingen()["toetsevaluatie_db"]).expanduser(), g["toets_id"])
        except Exception as e: abort(400, f"Scores niet op te halen: {e}")
    ia["versie"] = versie; schrijf_json(d / f"itemanalyse_{versie}.json", ia)
    try: vbank.toevoegen(d); vbank.statistiek(d); vbank.kalibratie()
    except Exception as e: ia["let_op"] = ia.get("let_op", []) + [f"Vragenbank niet bijgewerkt: {e}"]
    return jsonify(ia)

def _leerregels():
    rr = lees_json(LEERREGELS, []) or []
    for i, r in enumerate(rr): r.setdefault("status", "bevestigd"); r["_i"] = i
    return rr
@app.get("/api/leerregels")
def leerregels(): return jsonify(_leerregels())
@app.post("/api/leerregels/<int:i>")
def leerregel_wijzig(i):
    rr = lees_json(LEERREGELS, []) or []; g = request.get_json(force=True)
    if not 0 <= i < len(rr): abort(404)
    if g.get("status") in ("kandidaat", "bevestigd", "vervallen"): rr[i]["status"] = g["status"]; rr[i]["status_datum"] = nu()
    if isinstance(g.get("regel"), str) and g["regel"].strip(): rr[i]["regel"] = g["regel"].strip()
    shutil.copy(LEERREGELS, ROOT / "_weg" / f"leerregels_{datetime.datetime.now():%Y%m%d_%H%M%S}.json") if (ROOT / "_weg").is_dir() else None
    schrijf_json(LEERREGELS, rr); return jsonify(_leerregels())

@app.get("/api/vragenbank")
def vragenbank_lijst(): return jsonify({"vragen": vbank.laad(), "kalibratie": lees_json(vbank.KALIBRATIE)})

@app.post("/api/toetsen/<pid>/kopie")
def kopie(pid):
    """Nieuwe toets op basis van een bestaande: voor een herkansing of een nieuw schooljaar."""
    d = project_map(pid); bron = lees_json(d / "project.json"); g = request.get_json(force=True)
    doel = g.get("doel", "herkansing"); naam = (g.get("naam") or f"{bron['naam']} ({doel})").strip()
    nid = re.sub(r"[^A-Za-z0-9]+", "_", naam).strip("_")[:50] + "_" + uuid.uuid4().hex[:4]
    m = TOETSEN / nid; m.mkdir(parents=True)
    pr = {k: bron.get(k) for k in ("vak", "niveau", "klas", "at", "soort", "selectie")}
    pr.update({"id": nid, "naam": naam, "status": "intake", "sessie": None, "aangemaakt": nu(), "bron_project": pid, "doel": doel})
    if doel == "herkansing": pr["soort"] = "herkansing"
    schrijf_json(m / "project.json", pr)
    if (d / OPDRACHT).exists():
        t = (d / OPDRACHT).read_text(encoding="utf-8")
        t = re.sub(r"^Gebaseerd op:.*$", f"Gebaseerd op: {bron['naam']} (map toetsen/{pid}), als {doel}", t, count=1, flags=re.M)
        if "Gebaseerd op:" not in t: t = f"Gebaseerd op: {bron['naam']} (map toetsen/{pid}), als {doel}\n\n" + t
        punt = (f"Dit is een kopie van '{bron['naam']}' voor een {doel}. Pas datum, versies, thema's en contexten aan (geen contexten van de bron-toets), "
                f"en verwerk de evaluatie van de bron-toets in kopje 14. Voorstel: Claude doet dit en legt de wijzigingen voor.")
        t = re.sub(r"(^#{1,3}\s*Open punten.*?$)", lambda x: x.group(1) + "\n1. " + punt, t, count=1, flags=re.M | re.I) if re.search(r"^#{1,3}\s*Open punten", t, re.M | re.I) else t + "\n\n## Open punten\n1. " + punt + "\n"
        (m / OPDRACHT).write_text(t, encoding="utf-8")
    log(m, "docent", f"Nieuwe toets op basis van '{bron['naam']}' ({doel}).")
    return jsonify(pr)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("POORT", 5057)), debug=False, threaded=True)
