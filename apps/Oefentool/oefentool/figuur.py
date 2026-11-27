# -*- coding: utf-8 -*-
"""Figuren bij opgaven: marktschets, break-even, Lorenzcurve, kringloop, spelboom of eigen lijnen.

De vaste figuurtypen komen uit de bouwmotor van Toetsconstructie (bouw/figuren.py), zodat ze er
hetzelfde uitzien als in je toetsen: zwart-wit, en bij een rekenvraag een schets zonder getallen."""
from __future__ import annotations

import base64
import json
import importlib.util
import logging
import threading
from pathlib import Path

from .instellingen import BASIS

log = logging.getLogger("oefentool")
FIGUREN = BASIS / "Figuren"
GROOTTE = {"markt": (430, 322), "breakeven": (420, 289), "lorenz": (440, 381), "kringloop": (540, 345),
           "spelboom": (560, 287), "lijnen": (430, 322)}
_slot = threading.Lock()          # matplotlib is niet thread-safe
_mod = None


def _motor():
    global _mod
    if _mod is None:
        pad = BASIS / "bouw" / "figuren.py"
        spec = importlib.util.spec_from_file_location("figuren_bouw", pad)
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
    return _mod


def norm(f: dict | None) -> dict:
    """Zet de figuurgegevens (JSON-tekst in 'gegevens') om naar de vaste velden per soort."""
    f = dict(f or {})
    soort = f.get("soort")
    if soort not in GROOTTE:
        return {"soort": "geen"}
    g = f.get("gegevens")
    if isinstance(g, str):
        try:
            g = json.loads(g) if g.strip() else {}
        except json.JSONDecodeError:
            g = {}
    if isinstance(g, dict) and g:
        if soort in ("markt", "lorenz", "kringloop", "spelboom"):
            f[soort] = g
        if soort in ("breakeven", "lijnen"):
            f["assen"] = {"x": g.get("x", ""), "y": g.get("y", "")}
        if soort == "lijnen":
            f["lijnen"] = g.get("lijnen", [])
            f["getallen_op_assen"] = bool(g.get("getallen", g.get("getallen_op_assen", False)))
    return f


def heeft(inh: dict) -> bool:
    return norm(inh.get("figuur")).get("soort") in GROOTTE


def _lijnen(uit: str, f: dict) -> None:
    import matplotlib.pyplot as plt
    stijlen = ["-", (0, (6, 3)), ":", "-.", (0, (2, 2))]
    fig, ax = plt.subplots(figsize=(6.4, 4.8), dpi=200)
    xmax = ymax = 0.0
    for i, l in enumerate(f.get("lijnen", [])[:5]):
        pts = [p for p in l.get("punten", []) if len(p) == 2]
        if len(pts) < 2:
            continue
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color="black", lw=2, ls=stijlen[i % len(stijlen)])
        ax.text(xs[-1], ys[-1], " " + l.get("naam", ""), fontsize=13, fontweight="bold", va="center")
        xmax, ymax = max(xmax, *xs), max(ymax, *ys)
    if xmax <= 0 or ymax <= 0:
        plt.close(fig)
        raise ValueError("geen bruikbare lijnen")
    ax.set_xlim(0, xmax * 1.18)
    ax.set_ylim(0, ymax * 1.1)
    if not f.get("getallen_op_assen"):
        ax.set_xticks([])
        ax.set_yticks([])
        ax.text(-xmax * 0.03, -ymax * 0.05, "0", fontsize=13, ha="center")
    else:
        ax.grid(color="0.85", lw=0.5)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    assen = f.get("assen") or {}
    ax.set_xlabel(assen.get("x", ""), fontsize=13, loc="right")
    ax.set_ylabel(assen.get("y", ""), fontsize=13, loc="top")
    fig.tight_layout()
    fig.savefig(uit)
    plt.close(fig)


def teken(inh: dict, pad: Path) -> bool:
    f = norm(inh.get("figuur"))
    soort = f.get("soort")
    if soort not in GROOTTE:
        return False
    pad.parent.mkdir(parents=True, exist_ok=True)
    uit = str(pad)
    assen = f.get("assen") or {}
    try:
        with _slot:
            import matplotlib
            matplotlib.use("Agg")
            m = _motor()
            if soort == "markt":
                g = {k: f.get("markt", {}).get(k, 0) for k in ("a_v", "b_v", "a_s", "b_s", "heffing", "qmax", "pmax")}
                g.update({k: f.get("markt", {}).get(k, "") for k in ("product", "lijnnaam")})
                if g["b_v"] <= 0 or g["b_s"] <= 0 or g["qmax"] <= 0 or g["pmax"] <= 0 or g["a_v"] <= g["a_s"]:
                    raise ValueError("vraag- en aanbodlijn snijden niet")
                m.markt(uit, a_v=g["a_v"], b_v=g["b_v"], a_s=g["a_s"], b_s=g["b_s"], t=g.get("heffing", 0),
                        qmax=g["qmax"], pmax=g["pmax"], product=g.get("product") or "product",
                        lijnnaam=g.get("lijnnaam") or "A + heffing")
            elif soort == "breakeven":
                m.breakeven(uit, xas=assen.get("x") or "afzet in stuks", yas=assen.get("y") or "euro’s")
            elif soort == "lorenz":
                g = f.get("lorenz", {})
                curves = [c for c in g.get("curves", []) if len(c) == 4]
                if not curves:
                    raise ValueError("Lorenzcurve heeft vier punten per curve nodig")
                m.lorenz(uit, curves=curves, namen=g.get("namen") or None)
            elif soort == "kringloop":
                g = f.get("kringloop", {})
                m.kringloop(uit, **{k: g.get(k, "…") for k in ("Y", "C", "B", "O", "E", "M")})
            elif soort == "spelboom":
                g = f.get("spelboom", {})
                if len(g.get("spelers", [])) < 2 or len(g.get("keuzes", [])) < 3 or len(g.get("uitkomsten", [])) < 4:
                    raise ValueError("spelboom onvolledig")
                m.spelboom(uit, spelers=g["spelers"][:2], keuzes=[k[:2] for k in g["keuzes"][:3]], uitkomsten=g["uitkomsten"][:4])
            else:
                _lijnen(uit, f)
        return True
    except Exception as fout:  # noqa: BLE001
        log.warning("Figuur tekenen mislukt (%s): %s", soort, fout)
        if pad.exists():
            pad.unlink()
        return False


def pad_voor(uid: str, inh: dict) -> Path | None:
    """Pad naar de figuur van een opgave; tekent hem als hij nog niet bestaat."""
    if not heeft(inh):
        return None
    p = FIGUREN / f"{uid}.png"
    if not p.exists() and not teken(inh, p):
        return None
    return p


def data_uri(uid: str, inh: dict) -> str:
    p = pad_voor(uid, inh)
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode() if p else ""


def beschrijving(inh: dict) -> str:
    f = norm(inh.get("figuur"))
    soort = f.get("soort")
    if soort not in GROOTTE:
        return ""
    if soort == "markt":
        g = {k: f.get("markt", {}).get(k, "?") for k in ("a_v", "b_v", "a_s", "b_s", "heffing")}
        return (f"Figuur (markt, schets zonder getallen): vraag P = {g['a_v']} − {g['b_v']}Q, aanbod P = {g['a_s']} + {g['b_s']}Q"
                + (f", heffing {g['heffing']} per stuk" if g.get("heffing") else ""))
    if soort == "lorenz":
        return f"Figuur (Lorenzcurve): {f.get('lorenz')}"
    if soort == "kringloop":
        return f"Figuur (kringloop): {f.get('kringloop')}"
    if soort == "spelboom":
        return f"Figuur (spelboom): {f.get('spelboom')}"
    if soort == "breakeven":
        return "Figuur (break-evenschets met lijnen A opbrengst, B totale kosten, C constante kosten)"
    return "Figuur (lijnen): " + "; ".join(f"{l.get('naam', '')}: {l.get('punten')}" for l in f.get("lijnen", []))
