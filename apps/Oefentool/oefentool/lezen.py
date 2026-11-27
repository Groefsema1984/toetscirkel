# -*- coding: utf-8 -*-
"""Tekst uit lesmateriaal halen: PDF, Word, PowerPoint, tekst en HTML. Paginanummers blijven staan
als [p. 3] (PDF) of [dia 3] (PowerPoint), zodat een fragment naar de juiste plek kan verwijzen."""
from __future__ import annotations

import html
import re
import threading
from pathlib import Path

LEESBAAR = {".pdf", ".docx", ".pptx", ".txt", ".md", ".html", ".htm"}
OVERSLAAN = {".gdoc": "Google Docs-bestand: in Drive voor desktop staat alleen een snelkoppeling. "
                      "Download het als .docx of PDF (Bestand > Downloaden) en zet het in dezelfde map.",
             ".gslides": "Google Presentaties-bestand: download het als .pptx of PDF.",
             ".gsheet": "Google Spreadsheet: niet nodig voor de bibliotheek."}
PDF_SLOT = threading.Lock()          # PyMuPDF niet in meerdere threads tegelijk


class NietLeesbaar(Exception):
    pass


def _pdf(pad: Path) -> str:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    delen = []
    with PDF_SLOT:
        with fitz.open(str(pad)) as doc:
            for i, pagina in enumerate(doc, 1):
                tekst = pagina.get_text("text").strip()
                if tekst:
                    delen.append(f"[p. {i}]\n{tekst}")
    return "\n\n".join(delen)


def _docx(pad: Path) -> str:
    from docx import Document
    from docx.table import Table
    doc = Document(str(pad))
    delen = []
    for blok in doc.iter_inner_content():
        if isinstance(blok, Table):
            for rij in blok.rows:
                try:
                    cellen = [c.text.strip().replace("\n", " / ") for c in rij.cells]
                except Exception:  # noqa: BLE001
                    continue
                if any(cellen):
                    delen.append(" | ".join(cellen))
        elif blok.text.strip():
            delen.append(blok.text.strip())
    return "\n".join(delen)


def _pptx(pad: Path) -> str:
    from pptx import Presentation
    prs = Presentation(str(pad))
    delen = []
    for i, dia in enumerate(prs.slides, 1):
        regels = []
        for vorm in dia.shapes:
            if getattr(vorm, "has_text_frame", False) and vorm.has_text_frame:
                regels += [p.text for p in vorm.text_frame.paragraphs if p.text.strip()]
            if getattr(vorm, "has_table", False) and vorm.has_table:
                for rij in vorm.table.rows:
                    regels.append(" | ".join(c.text.strip() for c in rij.cells))
        if dia.has_notes_slide and dia.notes_slide.notes_text_frame is not None:
            notitie = dia.notes_slide.notes_text_frame.text.strip()
            if notitie:
                regels.append(f"(notitie) {notitie}")
        if regels:
            delen.append(f"[dia {i}]\n" + "\n".join(regels))
    return "\n\n".join(delen)


def _html(pad: Path) -> str:
    t = pad.read_text(encoding="utf-8", errors="replace")
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", t)).strip()


def lees(pad: Path) -> str:
    ext = pad.suffix.lower()
    if ext in OVERSLAAN:
        raise NietLeesbaar(OVERSLAAN[ext])
    try:
        if ext == ".pdf":
            tekst = _pdf(pad)
        elif ext == ".docx":
            tekst = _docx(pad)
        elif ext == ".pptx":
            tekst = _pptx(pad)
        elif ext in (".html", ".htm"):
            tekst = _html(pad)
        elif ext in (".txt", ".md"):
            tekst = pad.read_text(encoding="utf-8", errors="replace")
        else:
            raise NietLeesbaar(f"Bestandstype {ext} wordt niet gelezen.")
    except NietLeesbaar:
        raise
    except Exception as fout:  # noqa: BLE001
        raise NietLeesbaar(f"Kon het bestand niet openen: {type(fout).__name__}: {fout}") from fout
    tekst = tekst.replace("\x00", "")
    if len(re.sub(r"\W", "", tekst)) < 200:
        raise NietLeesbaar("Er staat (bijna) geen tekst in. Is het een scan of een afbeelding? "
                           "Exporteer het origineel als PDF met tekst.")
    return tekst
