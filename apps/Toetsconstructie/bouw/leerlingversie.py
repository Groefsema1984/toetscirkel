"""Maakt van een toetsbestand een leerlingversie zonder correctievoorschrift, voor de proefleerling.

Gebruik: python3 bouw/leerlingversie.py toetsen/<id>/toets_A.json
Schrijft toetsen/<id>/review/leerling_A.json en meldt de paden van de figuren die de leerling ziet.
"""
import json, sys
from pathlib import Path

bron = Path(sys.argv[1])
toets = json.loads(bron.read_text(encoding="utf-8"))
leerling = {k: toets[k] for k in ("meta", "opgaven", "bijlage", "uitwerkbijlage") if k in toets}
# Labels en interne gegevens horen niet in de leerlingversie.
leerling["meta"] = {k: v for k, v in leerling["meta"].items() if k not in ("tweefasen", "bestandsbasis", "maker")}

figuren = set()
def zoek(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("file"), str): figuren.add(obj["file"])
        for v in obj.values(): zoek(v)
    elif isinstance(obj, list):
        for v in obj: zoek(v)
zoek(leerling)

versie = toets.get("meta", {}).get("versie") or bron.stem.split("_")[-1]
doel = bron.parent / "review" / f"leerling_{versie}.json"
doel.parent.mkdir(exist_ok=True)
doel.write_text(json.dumps(leerling, ensure_ascii=False, indent=1), encoding="utf-8")
print("leerlingversie:", doel)
for f in sorted(figuren):
    p = bron.parent / f
    print("figuur:", p if p.exists() else f"{p} (niet gevonden)")
