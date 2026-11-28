# -*- coding: utf-8 -*-
"""De startpagina van ToetsCirkel en het vangnet voor adressen die bij een app horen maar op het hoofdadres binnenkomen."""
from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, request

from .koppeling import ONDERDELEN, menubalk

STIJL = """
:root{--inkt:#1d2733;--zacht:#5b6b7d;--lijn:#d9e1ea;--vlak:#fff;--achter:#f4f7fa;--merk:#1f3a5f;--goed:#2f7d4f;--fout:#b3261e}
@media (prefers-color-scheme:dark){:root{--inkt:#e6edf5;--zacht:#9fb0c3;--lijn:#2c3a4a;--vlak:#17212c;--achter:#0f171f;--merk:#8fb4e3;--goed:#6cc08f;--fout:#f08a80}}
*{box-sizing:border-box}body{margin:0;background:var(--achter);color:var(--inkt);font:16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 16px 48px}h1{margin:0 0 4px;font-size:28px}p.sub{margin:0 0 24px;color:var(--zacht)}
.tegels{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
.tegel{display:block;text-decoration:none;color:inherit;background:var(--vlak);border:1px solid var(--lijn);border-radius:12px;padding:18px 18px 16px;transition:border-color .15s}
.tegel:hover{border-color:var(--merk)}.tegel h2{margin:0 0 6px;font-size:20px;color:var(--merk)}.tegel p{margin:0 0 10px;color:var(--zacht);font-size:14px}
.stap{font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--zacht)}
.status{font-size:13px}.status.ok{color:var(--goed)}.status.fout{color:var(--fout)}
.tegel.uit{opacity:.75;cursor:default}pre{white-space:pre-wrap;font-size:12px;color:var(--fout);margin:8px 0 0}
.cirkel{margin:28px 0 0;color:var(--zacht);font-size:14px}
"""


TABEL = """
.filter{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 16px}.filter select,.filter input,.filter button{font:inherit;padding:6px 10px;border:1px solid var(--lijn);border-radius:8px;background:var(--vlak);color:var(--inkt)}
.schuif{overflow-x:auto;background:var(--vlak);border:1px solid var(--lijn);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--lijn);vertical-align:top}th{color:var(--zacht);font-weight:600}
.kern{margin:20px 0 0;padding:14px 16px;background:var(--vlak);border:1px solid var(--lijn);border-radius:12px;font-size:14px;color:var(--zacht)}.kern a{color:var(--merk)}
"""


def _getal(x) -> str:
    return "" if x is None else f"{x:.2f}"


def _kernblok(kern: dict) -> str:
    regels = []
    if kern.get("database_fout"):
        regels.append(f'<div class="status fout">● Databases samenvoegen lukte niet (alles is gebleven zoals het was): '
                      f'{escape(kern["database_fout"])[:300]}</div>')
    ib = kern.get("itembank") or {}
    if ib:
        per = ib.get("itembank", {})
        regels.append(f'<div><a href="/itembank">Itembank</a>: {per.get("construeren", 0)} gebouwde vragen, '
                      f'{per.get("evalueren", 0)} afgenomen toetsvragen, {per.get("oefenen", 0)} oefenopgaven; '
                      f'{ib.get("koppelingen", 0)} toetsen gekoppeld aan hun evaluatie.</div>')
    c = kern.get("codes") or {}
    if c.get("vragen"):
        regels.append(f'<div>Leerdoelcodes uit het nakijken overgenomen: {c["vragen"]} vragen in {c["projecten"]} matrijzen.</div>')
    cc = kern.get("codes_claude") or {}
    if cc.get("bezig"):
        regels.append(f'<div>Claude koppelt bestaande matrijzen aan het leerdoelenregister'
                      f'{": " + escape(cc["nu"]) if cc.get("nu") else ""} … (ververs deze pagina straks)</div>')
    elif cc.get("fout"):
        regels.append(f'<div class="status fout">● Codes koppelen met Claude gestopt: {escape(cc["fout"])}</div>')
    elif cc:
        regels.append(f'<div>Claude vulde {cc.get("vragen", 0)} leerdoelcodes in; '
                      f'<a href="/codes">{cc.get("twijfel", 0)} twijfelgevallen</a> om zelf te kiezen.</div>')
    if kern.get("itembank_fout"):
        regels.append(f'<div class="status fout">● Itembank: {escape(kern["itembank_fout"])[:300]}</div>')
    return f'<div class="kern">{"".join(regels)}</div>' if regels else ""


def _scanblok(kern: dict) -> str:
    st = kern.get("scans")
    if not st:
        return ""
    regels = []
    if st.get("actief"):
        regels.append(f'<div>Scans: ToetsCirkel bewaakt <b>{escape(Path(st["inbox"]).name)}</b> in OneDrive'
                      f'{" (laatste ronde " + escape(st["laatste"]) + ")" if st.get("laatste") else ""}'
                      f'{" · nu bezig met " + escape(st["bezig"]) if st.get("bezig") else ""}. <a href="/voorblad">Scanvoorblad printen</a> · <a href="/scans">Logboek</a></div>')
    if st.get("fout"):
        regels.append(f'<div class="status fout">● Scans: {escape(st["fout"])}</div>')
    wacht = kern.get("_scanwacht")
    try:
        open_ = wacht.wacht_op_docent() if wacht else []
    except Exception:  # noqa: BLE001
        open_ = []
    for w in open_:
        tid = escape(w["toets_id"])
        if w["status"] == "fout":
            regels.append(f'<div class="status fout">● {tid}: vastgelopen. <a href="/evalueren/voortgang/{tid}">Bekijk en hervat</a></div>')
        else:
            n = w["open"].get("totaal", 0)
            regels.append(f'<div><b>Wacht op jou:</b> {tid}, {n} punt{"en" if n != 1 else ""} om te beslissen. '
                          f'<a href="/evalueren/review?toets={tid}">Naar valideren</a></div>')
    return f'<div class="kern">{"".join(regels)}</div>' if regels else ""


def maak_portaal(status: dict, kern: dict | None = None) -> Flask:
    """status: voorvoegsel -> {'ok': bool, 'fout': str} per app; kern: uitkomst van database en itembank."""
    kern = kern or {}
    app = Flask("toetscirkel_portaal")

    @app.get("/")
    def start():
        tegels = []
        for nr, (pad, naam, uitleg) in enumerate(ONDERDELEN, 1):
            s = status.get(pad, {"ok": False, "fout": "niet geladen"})
            if s["ok"]:
                tegels.append(f'<a class="tegel" href="{pad}/"><div class="stap">Stap {nr}</div><h2>{naam}</h2>'
                              f'<p>{uitleg}</p><div class="status ok">● Klaar</div></a>')
            else:
                tegels.append(f'<div class="tegel uit"><div class="stap">Stap {nr}</div><h2>{naam}</h2><p>{uitleg}</p>'
                              f'<div class="status fout">● Kon niet starten</div><pre>{escape(s["fout"])[:600]}</pre></div>')
        return (f'<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>ToetsCirkel</title><style>{STIJL}{TABEL}</style></head><body>{menubalk("")}<main>'
                f'<h1>ToetsCirkel</h1><p class="sub">Construeren, evalueren en oefenen in één app.</p>'
                f'<div class="tegels">{"".join(tegels)}</div>'
                f'<p class="cirkel">De cirkel: toets maken → nakijken en analyseren → gericht oefenen → opnieuw toetsen of bijstellen.</p>'
                f'{_scanblok(kern)}{_kernblok(kern)}'
                f'</main></body></html>')

    @app.get("/api/toetscirkel")
    def toetscirkel_status():
        return jsonify(app="ToetsCirkel", onderdelen=status, kern={k: v for k, v in kern.items() if not k.startswith("_")})

    @app.get("/codes")
    def codes_pagina():
        from .kern import codes
        try:
            rijen, fout = codes.twijfelgevallen(), ""
        except Exception as e:  # noqa: BLE001
            rijen, fout = [], str(e)
        regels = "".join(
            f"<tr><td>{escape(str(r['project']))}</td><td>{r['nr']}</td><td>{escape(str(r['leerdoel'] or ''))}</td>"
            f"<td><b>{escape(str(r['voorstel'] or '–'))}</b></td><td>{escape(str(r['zekerheid'] or ''))}</td>"
            f"<td>{escape(str(r['toelichting'] or ''))}</td></tr>" for r in rijen)
        return (f'<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>Twijfelgevallen</title><style>{STIJL}{TABEL}</style></head><body>{menubalk("")}<main>'
                f'<h1>Twijfelgevallen leerdoelcodes</h1><p class="sub">Hier was Claude niet zeker genoeg. Kies de code zelf in '
                f'Construeren › Toetsmatrijs (kolom Code), of laat hem leeg: bij het nakijken koppelt Evalueren alsnog.</p>'
                + (f'<pre>{escape(fout)}</pre>' if fout else "")
                + f'<div class="schuif"><table><tr><th>Toets</th><th>Vraag</th><th>Leerdoel</th><th>Voorstel</th><th>Zekerheid</th>'
                  f'<th>Toelichting</th></tr>{regels or "<tr><td colspan=6>Geen twijfelgevallen.</td></tr>"}</table></div></main></body></html>')

    @app.get("/voorblad")
    def voorblad_pagina():
        """Scanvoorblad om bovenop de stapel uitwerkbijlagen te leggen: daarmee herkent ToetsCirkel de scan zeker."""
        import sys as _sys
        from datetime import date as _date

        from .kern import scans
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import paden
        toetsen = scans.gebouwde_toetsen(paden.TOETSCONSTRUCTIE / "toetsen")
        sleutel = lambda t: f"{t.project}|{t.versie}"  # noqa: E731
        keuze = request.args.get("t", "")
        try:
            datum = _date.fromisoformat(request.args.get("datum") or _date.today().isoformat())
        except ValueError:
            datum = _date.today()
        t = next((x for x in toetsen if sleutel(x) == keuze), None)
        klas = scans.klascode(request.args.get("klas", "")) or (t.klas if t else "")
        if t is None or not klas:
            opties = "".join(f'<option value="{escape(sleutel(x))}"{" selected" if sleutel(x) == keuze else ""}>'
                             f'{escape(x.naam)} · {escape(x.at or "geen AT")} · {escape(x.klas or "?")} · versie {escape(x.versie or "-")}</option>'
                             for x in sorted(toetsen, key=lambda x: (x.naam.lower(), x.versie)))
            return (f'<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    f'<title>Scanvoorblad</title><style>{STIJL}{TABEL}</style></head><body>{menubalk("")}<main>'
                    f'<h1>Scanvoorblad</h1><p class="sub">Print per klas en per versie één voorblad en leg het bovenop de stapel. '
                    f'ToetsCirkel leest het als eerste pagina en weet dan zeker welke toets het is; het voorblad zelf wordt niet nagekeken.</p>'
                    f'<form class="filter"><select name="t">{opties}</select>'
                    f'<input name="klas" placeholder="klas, bijv. 5H" value="{escape(request.args.get("klas", ""))}">'
                    f'<input type="date" name="datum" value="{datum.isoformat()}"><button>Maak voorblad</button></form>'
                    + ("" if t is None or klas else "<p>Van deze toets is de klas onbekend: vul hem in (bijv. 5H, 4V, mavo3).</p>")
                    + '</main></body></html>')
        t.klas = klas
        code = scans.voorblad_code(t, datum)
        rij = lambda k, v: f"<tr><th>{k}</th><td>{v}</td></tr>"  # noqa: E731
        return f"""<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>Scanvoorblad {escape(t.at or t.naam)}</title>
<style>
body{{font:15px/1.45 -apple-system,'Segoe UI',Arial,sans-serif;color:#000;background:#fff;margin:0}}
.blad{{max-width:180mm;margin:0 auto;padding:14mm 0}}h1{{font-size:24px;margin:0 0 4px}}.sub{{margin:0 0 18px;color:#333}}
.code{{border:3px solid #000;padding:14px 16px;font:bold 22px/1.3 'Courier New',monospace;margin:0 0 18px;word-break:break-all}}
table{{border-collapse:collapse;width:100%;margin:0 0 16px}}th,td{{border:1px solid #000;padding:7px 9px;text-align:left;vertical-align:top}}
th{{width:38%;background:#f0f0f0}}.leeg{{height:28px}}ul{{margin:6px 0 0 18px;padding:0}}.knop{{margin:12px 0}}
@media print{{.knop{{display:none}}.blad{{padding:0}}@page{{size:A4;margin:15mm}}}}
</style></head><body><div class="blad">
<div class="knop"><button onclick="print()">Printen</button> <a href="/voorblad">ander voorblad</a></div>
<h1>ToetsCirkel · scanvoorblad</h1>
<p class="sub">Leg dit blad bovenop de stapel van deze klas en scan alles in één keer naar de map <b>ToetsCirkel Scans</b>.</p>
<div class="code">{escape(code)}</div>
<table>
{rij("Toets", escape(t.naam))}{rij("AT-nummer", escape(t.at or "–"))}{rij("Vak", escape(t.vak))}
{rij("Klas (niveau en leerjaar)", escape(t.klas))}{rij("Versie", escape(t.versie or "–"))}
{rij("Afnamedatum", datum.strftime("%d-%m-%Y"))}{rij("Toetsduur", f"{t.minuten} minuten" if t.minuten else "–")}
{rij("Correctievoorschrift", escape(t.cv.name))}
{rij("Lesgroep (bijv. 5H2)", '<div class="leeg"></div>')}
{rij("Aantal leerlingen in deze stapel", '<div class="leeg"></div>')}
{rij("Afwezig (leerlingnummers)", '<div class="leeg"></div><div class="leeg"></div>')}
{rij("Bijzonderheden (extra tijd, inhaal, fraude)", '<div class="leeg"></div><div class="leeg"></div>')}
</table>
<p><b>Voor het scannen</b></p>
<ul><li>Alleen versie {escape(t.versie or "–")} van deze klas in deze stapel; een andere versie of klas krijgt een eigen voorblad.</li>
<li>Het werk van één leerling bij elkaar, met het leerlingnummer op elk blad.</li>
<li>Eerst zelf nakijken op papier; daarna kijkt de AI opnieuw. Bij twijfel beslis jij in Evalueren › Valideren.</li></ul>
<p style="margin-top:18px;font-size:12px;color:#333">Op dit voorblad staan geen leerlinggegevens; het gaat niet mee in het nakijken.</p>
</div></body></html>"""

    @app.get("/scans")
    def scans_pagina():
        from .kern import scans
        try:
            rijen, fout = scans.logboek(100), ""
        except Exception as e:  # noqa: BLE001
            rijen, fout = [], str(e)
        regels = "".join(
            f"<tr><td>{escape(r['moment'].replace('T', ' '))}</td><td>{escape(r['bestand'])}</td><td><b>{escape(r['status'])}</b></td>"
            f"<td>{escape(r['toets_id'] or '')}</td><td>{escape(r['reden'] or '')}</td></tr>" for r in rijen)
        st = kern.get("scans") or {}
        return (f'<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>Scans</title><style>{STIJL}{TABEL}</style></head><body>{menubalk("")}<main>'
                f'<h1>Scans</h1><p class="sub">Zet een klassenscan in <b>{escape(st.get("inbox", "ToetsCirkel Scans"))}</b>. '
                f'Herkend: de analyse start vanzelf en de scan gaat naar Verwerkt. Niet zeker: de scan gaat naar Twijfel, met een '
                f'.txt waarin staat waarom. Het zekerst: leg een <a href="/voorblad">scanvoorblad</a> bovenop de stapel. Anders: pas de bestandsnaam aan (bijv. <i>AT5.21 5H versie A.pdf</i>) en zet hem terug.</p>'
                + (f'<pre>{escape(fout)}</pre>' if fout else "")
                + f'<div class="schuif"><table><tr><th>Moment</th><th>Scan</th><th>Status</th><th>Toets</th><th>Toelichting</th></tr>'
                  f'{regels or "<tr><td colspan=5>Nog geen scans verwerkt.</td></tr>"}</table></div></main></body></html>')

    @app.get("/itembank")
    def itembank_pagina():
        from .kern import itembank
        f = {k: (request.args.get(k) or "").strip() for k in ("vak", "leerdoel_code", "bron", "tekst")}
        try:
            rijen = itembank.zoek(**{k: v or None for k, v in f.items()}, limiet=300)
            fout = ""
        except Exception as e:  # noqa: BLE001
            rijen, fout = [], str(e)
        kop = "".join(f"<th>{k}</th>" for k in ("Bron", "Toets", "Vraag", "Ptn", "Leerdoel", "Bloom", "p", "Tekst"))
        regels = "".join(
            f"<tr><td>{escape(str(r['bron']))}</td><td>{escape(str(r['toets_id'] or r['project'] or ''))}</td>"
            f"<td>{r['vraagnr'] or ''}</td><td>{r['punten'] if r['punten'] is not None else ''}</td>"
            f"<td><b>{escape(str(r['leerdoel_code'] or ''))}</b> {escape(str(r['leerdoel'] or ''))[:90]}</td>"
            f"<td>{escape(str(r['bloom'] or ''))}</td><td>{_getal(r['p_waarde'])}</td>"
            f"<td>{escape(str(r['tekst'] or ''))[:160]}</td></tr>" for r in rijen)
        keuze = lambda naam, opties: (f'<select name="{naam}"><option value="">alle</option>' + "".join(  # noqa: E731
            f'<option{" selected" if f[naam] == o else ""}>{o}</option>' for o in opties) + "</select>")
        return (f'<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<title>Itembank</title><style>{STIJL}{TABEL}</style></head><body>{menubalk("/itembank")}<main>'
                f'<h1>Itembank</h1><p class="sub">Alle vragen uit construeren, evalueren en oefenen. Eerste 300 resultaten.</p>'
                f'<form class="filter">{keuze("bron", ["construeren", "evalueren", "oefenen"])}'
                f'{keuze("vak", ["Economie", "Bedrijfseconomie"])}'
                f'<input name="leerdoel_code" placeholder="leerdoelcode" value="{escape(f["leerdoel_code"])}">'
                f'<input name="tekst" placeholder="zoek in vraag of leerdoel" value="{escape(f["tekst"])}"><button>Zoek</button></form>'
                + (f'<pre>{escape(fout)}</pre>' if fout else "")
                + f'<div class="schuif"><table><tr>{kop}</tr>{regels}</table></div></main></body></html>')

    @app.route("/<path:pad>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def vangnet(pad):
        # Een app vroeg om '/iets' in plaats van '/evalueren/iets': stuur door naar de app waar het verzoek vandaan kwam.
        herkomst = urlsplit(request.headers.get("Referer", "")).path
        for basis, _, _ in ONDERDELEN:
            if herkomst == basis or herkomst.startswith(basis + "/"):
                doel = f"{basis}/{pad}"
                if request.query_string:
                    doel += "?" + request.query_string.decode("latin-1")
                return redirect(doel, code=307)            # 307: methode en inhoud blijven gelijk
        return (f'<!doctype html><meta charset="utf-8"><title>Niet gevonden</title><body>{menubalk("")}'
                f'<main style="font-family:sans-serif;padding:24px">Deze pagina bestaat niet: /{escape(pad)}</main>', 404)

    return app
