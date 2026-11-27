# -*- coding: utf-8 -*-
"""Een bestaande Flask-app onder een eigen voorvoegsel laten draaien (bijv. /evalueren) zonder zijn code te wijzigen.

De drie apps gebruiken op veel plekken vaste adressen die met '/' beginnen ('/api/status', '/static/app.js').
Onder één adres zouden die bij de verkeerde app uitkomen. Dit bestand lost dat op drie niveaus op:
1. In elke HTML-pagina die de app teruggeeft, krijgen href/src/action die met '/' beginnen het voorvoegsel.
2. Bovenaan elke pagina komt een klein script dat fetch() en XMLHttpRequest hetzelfde laat doen, ook voor
   links en afbeeldingen die JavaScript later toevoegt.
3. Wat dan nog naar het hoofdadres gaat (bijv. location.href = '/review'), stuurt de startpagina door naar de
   app waar het verzoek vandaan kwam (zie portaal.py).
Daarnaast komt bovenaan elke pagina de ToetsCirkel-menubalk.
"""
from __future__ import annotations

import re

from flask import Flask, request

ONDERDELEN = [  # (voorvoegsel, naam in het menu, korte omschrijving)
    ("/construeren", "Construeren", "Toetsen maken: matrijs, opgaven, correctiemodel"),
    ("/evalueren", "Evalueren", "Scans nakijken, itemanalyse, leerprofielen"),
    ("/oefenen", "Oefenen", "Oefenmateriaal per leerling of klas"),
]

_SHIM = """<script>/* ToetsCirkel: adressen binnen deze app houden */(function(){var B=%(basis)r;
var E=/^\\/(construeren|evalueren|oefenen)(\\/|\\?|$)/;
function fix(u){return (typeof u==="string"&&u.charAt(0)==="/"&&u.charAt(1)!=="/"&&!E.test(u))?B+u:u}
window.TOETSCIRKEL_BASIS=B;window.toetscirkelAdres=fix;
var f=window.fetch;if(f){window.fetch=function(i,o){return f.call(this,typeof i==="string"?fix(i):i,o)}}
var x=XMLHttpRequest.prototype.open;XMLHttpRequest.prototype.open=function(m,u){var a=Array.prototype.slice.call(arguments);a[1]=fix(u);return x.apply(this,a)};
var A={A:"href",LINK:"href",IMG:"src",SCRIPT:"src",FORM:"action",IFRAME:"src",SOURCE:"src",EMBED:"src",OBJECT:"data"};
function el(e){var k=A[e.tagName];if(!k||(e.closest&&e.closest("[data-toetscirkel]")))return;var v=e.getAttribute(k);if(v){var n=fix(v);if(n!==v)e.setAttribute(k,n)}}
function boom(r){if(r.nodeType!==1)return;el(r);if(r.querySelectorAll){r.querySelectorAll("a,link,img,script,form,iframe,source,embed,object").forEach(el)}}
new MutationObserver(function(ms){ms.forEach(function(m){if(m.type==="attributes"){el(m.target)}else{m.addedNodes.forEach(boom)}})})
.observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:["href","src","action","data"]});})();</script>"""

_MENU_STIJL = ("<style>#toetscirkel-menu{all:initial;display:flex;align-items:center;gap:4px;padding:6px 14px;"
               "background:#1f3a5f;font:500 14px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
               "position:relative;z-index:2147483000}"
               "#toetscirkel-menu a{all:unset;cursor:pointer;color:#dbe6f3;padding:4px 10px;border-radius:6px}"
               "#toetscirkel-menu a:hover{background:#2d4f7c;color:#fff}"
               "#toetscirkel-menu a.tc-hier{background:#fff;color:#1f3a5f}"
               "#toetscirkel-menu .tc-merk{color:#fff;font-weight:700;margin-right:10px}"
               "@media print{#toetscirkel-menu{display:none}}</style>")


def menubalk(actief: str = "") -> str:
    """HTML van de menubalk; actief is het voorvoegsel van de app die nu open is ('' = startpagina)."""
    links = [f'<a href="/" class="tc-merk{" tc-hier" if not actief else ""}" title="Startpagina">◯ ToetsCirkel</a>']
    for pad, naam, uitleg in ONDERDELEN:
        klasse = ' class="tc-hier"' if pad == actief else ""
        links.append(f'<a href="{pad}/"{klasse} title="{uitleg}">{naam}</a>')
    links.append(f'<a href="/itembank"{' class="tc-hier"' if actief == "/itembank" else ""} title="Alle vragen op één plek">Itembank</a>')
    return _MENU_STIJL + '<nav id="toetscirkel-menu" data-toetscirkel="1">' + "".join(links) + "</nav>"


def _herschrijf_attributen(html: str, basis: str) -> str:
    namen = "|".join(re.escape(p.lstrip("/")) for p, _, _ in ONDERDELEN)
    patroon = re.compile(r'(\b(?:href|src|action|data)\s*=\s*["\'])/(?!/|(?:' + namen + r')(?:[/?"\']))', re.I)
    return patroon.sub(lambda m: m.group(1) + basis + "/", html)


OUDE_ADRESSEN = re.compile(r"https?://(?:127\.0\.0\.1|localhost):(5055|5056|5057)")
_POORT_NAAR = {"5057": "/construeren", "5055": "/evalueren", "5056": "/oefenen"}


def _voeg_in(html: str, basis: str) -> str:
    # Links naar de losse apps op hun oude poort wijzen nu naar het onderdeel binnen ToetsCirkel.
    html = OUDE_ADRESSEN.sub(lambda m: _POORT_NAAR[m.group(1)], html)
    html = _herschrijf_attributen(html, basis)
    shim = _SHIM % {"basis": basis}
    m = re.search(r"<head[^>]*>", html, re.I)
    html = html[:m.end()] + shim + html[m.end():] if m else shim + html
    m = re.search(r"<body[^>]*>", html, re.I)
    if m:
        html = html[:m.end()] + menubalk(basis) + html[m.end():]
    return html


def koppel(app: Flask, basis: str, cookienaam: str) -> None:
    """Laat app correct werken onder voorvoegsel basis (bijv. '/evalueren')."""
    # Twee apps met sessies op hetzelfde adres: elk een eigen cookie, anders overschrijven ze elkaar.
    app.config["SESSION_COOKIE_NAME"] = cookienaam

    @app.after_request
    def _toetscirkel(antwoord):
        # Omleidingen naar een vast adres ('/review') binnen de app houden.
        plek = antwoord.headers.get("Location", "")
        if plek.startswith("/") and not plek.startswith("//") and not (plek == basis or plek.startswith(basis + "/")
                                                                       or plek.startswith(basis + "?")):
            antwoord.headers["Location"] = basis + plek
        if (antwoord.mimetype == "text/html" and not antwoord.is_streamed and not antwoord.direct_passthrough
                and request.headers.get("X-ToetsCirkel-Ruw") != "1"):
            tekst = antwoord.get_data(as_text=True)
            if "<html" in tekst[:2000].lower() or "<body" in tekst.lower():
                antwoord.set_data(_voeg_in(tekst, basis))
        return antwoord
