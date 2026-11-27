"""Tekent de vaste figuurtypen voor een toets. Gebruik: python3 bouw/figuren.py <figuren.json>
figuren.json is een lijst. Elk item heeft "type", "uit" (bestandsnaam .png, komt naast figuren.json) en de velden van dat type:
  lorenz     : curves [[namen...], [[y20,y40,y60,y80], ...]]  -> {"type":"lorenz","uit":"lorenz_A.png","namen":["curve 1","curve 2"],"curves":[[8,21,38,61],[1,5,17,41]]}
  kringloop  : waarden Y,C,B,O,E,M (tekst of getal); I, S en de twee geldstromen blijven open
  markt      : vraag P = a_v - b_v*Q, aanbod P = a_s + b_s*Q, heffing t, qmax, pmax, product, lijnnaam ("A + accijns"), antwoord (true = met arcering)
  spelboom   : spelers [s1, s2], keuzes [[k1a,k1b],[k2a,k2b],[k3a,k3b]], uitkomsten [niet, na, accepteren, weigeren]
  breakeven  : schets met lijnen A (opbrengst), B (totale kosten), C (constante kosten); xas, yas
De standaardgrootte in Word (w x h in pixels): lorenz 440x381, kringloop 540x345, markt 430x322, spelboom 560x287, breakeven 420x289.
Een schets zonder getallen op de assen voorkomt dat een rekenantwoord kan worden afgelezen."""
import json, os, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Polygon
plt.rcParams["font.family"] = "DejaVu Sans"

def lorenz(uit, curves, namen=None, **_):
    x = [0, 20, 40, 60, 80, 100]; namen = namen or [f"curve {i+1}" for i in range(len(curves))]
    stijl = [dict(marker="o", ms=5), dict(ls=(0, (5, 2)), marker="s", ms=5, mfc="white"), dict(ls=":", marker="^", ms=5)]
    fig, ax = plt.subplots(figsize=(6.0, 5.2), dpi=200)
    ax.plot([0, 100], [0, 100], color="0.45", ls="--", lw=1.3, label="diagonaal (volledig gelijke verdeling)")
    for i, c in enumerate(curves): ax.plot(x, [0] + list(c) + [100], color="black", lw=2, label=namen[i], **stijl[i % 3])
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.set_xticks(range(0, 101, 10)); ax.set_yticks(range(0, 101, 10))
    ax.set_xticks(range(0, 101, 5), minor=True); ax.set_yticks(range(0, 101, 5), minor=True)
    ax.grid(which="major", color="0.6", lw=0.6); ax.grid(which="minor", color="0.85", lw=0.4)
    ax.set_xlabel("cumulatief percentage huishoudens\n(van laagste naar hoogste inkomen)", fontsize=10, labelpad=8)
    ax.set_ylabel("cumulatief percentage van het totale inkomen", fontsize=10, labelpad=8)
    ax.legend(loc="upper left", fontsize=9, framealpha=1); ax.set_aspect("equal"); fig.tight_layout(); fig.savefig(uit); plt.close(fig)

def kringloop(uit, Y, C, B, O, E, M, **_):
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=200); w, h = 1.8, 0.7
    pos = {"gezinnen": (1, 3), "banken": (5, 3), "bedrijven": (9, 3), "buitenland": (5, 5.4), "overheid": (5, 0.6)}
    for k, (x, y) in pos.items():
        ax.add_patch(Rectangle((x - w / 2, y - h / 2), w, h, fc="white", ec="black", lw=1.2)); ax.text(x, y, k, ha="center", va="center", fontsize=13)
    def pad(pts, dashed=False, arrow=True):
        xs, ys = zip(*pts); ax.plot(xs[:-1] if arrow else xs, ys[:-1] if arrow else ys, color="black", lw=1.1, ls="--" if dashed else "-")
        if arrow: ax.annotate("", xy=pts[-1], xytext=pts[-2], arrowprops=dict(arrowstyle="-|>", color="black", lw=1.1, shrinkA=0, shrinkB=0))
    t = lambda x, y, s: ax.text(x, y, s, ha="center", va="center", fontsize=12.5, bbox=dict(fc="white", ec="none", pad=1.5))
    pad([(1, 3.35), (1, 4.1), (8.4, 4.1), (8.4, 3.35)]); t(3.2, 4.1, f"C = {C}")
    pad([(8.6, 2.65), (8.6, 1.75), (1.4, 1.75), (1.4, 2.65)]); t(3.2, 1.75, f"Y = {Y}")
    pad([(5.9, 5.6), (9.6, 5.6), (9.6, 3.35)]); t(7.7, 5.6, f"E = {E}")
    pad([(9.0, 3.35), (9.0, 5.2), (5.9, 5.2)]); t(7.5, 5.2, f"M = {M}")
    pad([(0.6, 2.65), (0.6, 0.6), (4.1, 0.6)]); t(2.4, 0.6, f"B = {B}")
    pad([(5.9, 0.6), (9.4, 0.6), (9.4, 2.65)]); t(7.6, 0.6, f"O = {O}")
    pad([(1.9, 3), (4.1, 3)]); t(3.0, 3.22, "S = …"); pad([(5.9, 3), (8.1, 3)]); t(7.0, 3.22, "I = …")
    pad([(5, 2.65), (5, 0.95)], dashed=True, arrow=False); t(6.15, 1.3, "geldstroom 1")
    pad([(5, 3.35), (5, 5.05)], dashed=True, arrow=False); t(6.15, 4.6, "geldstroom 2")
    ax.set_xlim(-0.2, 10.2); ax.set_ylim(0, 6); ax.axis("off"); fig.tight_layout(pad=0.2); fig.savefig(uit); plt.close(fig)

def markt(uit, a_v, b_v, a_s, b_s, t, qmax, pmax, product="product", lijnnaam="A + heffing", antwoord=False, labels=("opbrengst heffing", "welvaartsverlies"), **_):
    fig, ax = plt.subplots(figsize=(6.4, 4.8), dpi=200)
    q0 = (a_v - a_s) / (b_v + b_s); p0 = a_v - b_v * q0; q1 = (a_v - a_s - t) / (b_v + b_s); p1 = a_v - b_v * q1; pp = a_s + b_s * q1
    if antwoord:
        ax.add_patch(Polygon([(0, pp), (q1, pp), (q1, p1), (0, p1)], closed=True, fc="none", ec="black", hatch="///", lw=0.0))
        ax.add_patch(Polygon([(q1, pp), (q1, p1), (q0, p0)], closed=True, fc="0.35", ec="black", lw=0.8))
        for a, b in (([0, q1], [p1, p1]), ([0, q1], [pp, pp]), ([q1, q1], [0, p1])): ax.plot(a, b, color="black", lw=0.8, ls=":")
        ax.text(q1 * 0.45, (p1 + pp) / 2, labels[0], ha="center", va="center", fontsize=13, bbox=dict(fc="white", ec="none", pad=2))
        ax.annotate(labels[1], xy=(q1 + (q0 - q1) * 0.35, (p1 + pp) / 2), xytext=(q0 + qmax * 0.12, p0 - pmax * 0.02), fontsize=13, va="center", arrowprops=dict(arrowstyle="-", lw=0.8))
    qv = min(qmax, a_v / b_v); ax.plot([0, qv], [a_v, a_v - b_v * qv], color="black", lw=2)
    qa = min(qmax, (pmax * 0.93 - a_s - t) / b_s); s0 = max(0, -a_s / b_s) if a_s < 0 else 0; s1 = max(0, -(a_s + t) / b_s) if a_s + t < 0 else 0
    ax.plot([s0, qa], [a_s + b_s * s0, a_s + b_s * qa], color="black", lw=2); ax.plot([s1, qa], [a_s + t + b_s * s1, a_s + t + b_s * qa], color="black", lw=2, ls=(0, (6, 3)))
    ax.text(qv * 0.97, a_v - b_v * qv * 0.97 + pmax * 0.035, "V", fontsize=15, fontweight="bold")
    ax.text(qa + qmax * 0.015, a_s + b_s * qa - pmax * 0.02, "A", fontsize=15, fontweight="bold"); ax.text(qa + qmax * 0.015, a_s + t + b_s * qa + pmax * 0.01, lijnnaam, fontsize=14, fontweight="bold")
    ax.set_xlim(0, qmax * 1.3); ax.set_ylim(0, pmax); ax.set_xticks([]); ax.set_yticks([])
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax.set_xlabel(f"Q (hoeveelheid {product})", fontsize=13, loc="right"); ax.set_ylabel("P (prijs in euro’s)", fontsize=13, loc="top"); ax.text(-qmax * 0.03, -pmax * 0.04, "0", fontsize=13, ha="center")
    fig.tight_layout(); fig.savefig(uit); plt.close(fig)

def spelboom(uit, spelers, keuzes, uitkomsten, **_):
    fig, ax = plt.subplots(figsize=(8.2, 4.2), dpi=200)
    knoop = lambda x, y, t: ax.text(x, y, t, ha="center", va="center", fontsize=14, fontweight="bold", bbox=dict(boxstyle="round,pad=0.35", fc="white", ec="black", lw=1.1))
    def tak(a, b, t, boven=True):
        ax.plot([a[0], b[0]], [a[1], b[1]], color="black", lw=1.2, zorder=0); mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        ax.text(mx - 0.3, my + (0.42 if boven else -0.5), t, ha="right", va="center", fontsize=13)
    eind = lambda x, y, t: ax.text(x, y, t, ha="left", va="center", fontsize=14)
    A, B, Cn = (1.2, 2.7), (4.6, 4.0), (9.0, 2.7); k1, k2, k3 = keuzes
    tak(A, (4.6, 1.3), k1[1], False); eind(4.8, 1.3, uitkomsten[0]); tak(A, B, k1[0])
    tak(B, (9.0, 5.1), k2[0]); eind(9.2, 5.1, uitkomsten[1]); tak(B, Cn, k2[1], False)
    tak(Cn, (12.2, 3.8), k3[0]); eind(12.4, 3.8, uitkomsten[2]); tak(Cn, (12.2, 1.6), k3[1], False); eind(12.4, 1.6, uitkomsten[3])
    knoop(*A, spelers[0]); knoop(*B, spelers[1]); knoop(*Cn, spelers[0])
    ax.set_xlim(0, 14.4); ax.set_ylim(0.8, 5.6); ax.axis("off"); fig.tight_layout(pad=0.2); fig.savefig(uit); plt.close(fig)

def breakeven(uit, xas="omzet in euro’s", yas="euro’s", **_):
    fig, ax = plt.subplots(figsize=(6.4, 4.4), dpi=200)
    for (ys, ls, n) in (([0, 9.2], "-", "A"), ([3.2, 8.0], (0, (6, 3)), "B"), ([3.2, 3.2], ":", "C")):
        ax.plot([0, 10], ys, color="black", lw=2, ls=ls); ax.text(10.15, ys[1] - (0.1 if n == "B" else 0), n, fontsize=15, fontweight="bold", va="center")
    ax.set_xlim(0, 11); ax.set_ylim(0, 10); ax.set_xticks([]); ax.set_yticks([])
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    ax.set_xlabel(xas, fontsize=13, loc="right"); ax.set_ylabel(yas, fontsize=13, loc="top"); ax.text(-0.25, -0.4, "0", fontsize=13, ha="center")
    fig.tight_layout(); fig.savefig(uit); plt.close(fig)

TYPEN = {"lorenz": lorenz, "kringloop": kringloop, "markt": markt, "spelboom": spelboom, "breakeven": breakeven}
if __name__ == "__main__":
    pad = sys.argv[1]; map_ = os.path.dirname(os.path.abspath(pad))
    for f in json.load(open(pad, encoding="utf-8")):
        f = dict(f); soort = f.pop("type"); f["uit"] = os.path.join(map_, f["uit"])
        if soort not in TYPEN: sys.exit(f"Onbekend figuurtype: {soort}. Kies uit {', '.join(TYPEN)} of teken zelf met matplotlib.")
        TYPEN[soort](**f); print("getekend:", os.path.basename(f["uit"]))
