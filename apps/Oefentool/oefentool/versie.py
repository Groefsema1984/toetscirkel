"""Welke versie van de code draait er? Zo merkt de app (en het startbestand) dat er nieuwe bestanden staan."""
import hashlib
import time
from pathlib import Path

MAP = Path(__file__).resolve().parent


def code_versie() -> str:
    h = hashlib.md5()
    for p in sorted(MAP.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


GESTART = code_versie()
_cache = {"t": 0.0, "v": GESTART}


def verouderd() -> bool:
    """True als de bestanden op schijf nieuwer zijn dan de code die nu draait (hooguit eens per 20 s nagekeken)."""
    if _cache["t"] < time.time() - 20:
        try:
            _cache.update(t=time.time(), v=code_versie())
        except OSError:
            _cache["t"] = time.time()
    return _cache["v"] != GESTART
