# -*- coding: utf-8 -*-
"""De startpagina van ToetsCirkel en het vangnet voor adressen die bij een app horen maar op het hoofdadres binnenkomen."""
from __future__ import annotations

from html import escape
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
    if kern.get("itembank_fout"):
        regels.append(f'<div class="status fout">● Itembank: {escape(kern["itembank_fout"])[:300]}</div>')
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
                f'{_kernblok(kern)}'
                f'</main></body></html>')

    @app.get("/api/toetscirkel")
    def toetscirkel_status():
        return jsonify(app="ToetsCirkel", onderdelen=status, kern=kern)

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
