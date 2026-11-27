"""Vervangt de plaatshouder LEESVERDER in de voettekst door een Word-veld
dat 'lees verder' toont op elke pagina behalve de laatste."""
import re, shutil, sys, zipfile

src = sys.argv[1]
RPR = '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr>'
def r(inner): return f'<w:r>{RPR}{inner}</w:r>'
def fld(kind): return r(f'<w:fldChar w:fldCharType="{kind}"/>')
def instr(t): return r(f'<w:instrText xml:space="preserve">{t}</w:instrText>')
def simple(name, cached): return fld("begin") + instr(f" {name} ") + fld("separate") + r(f"<w:t>{cached}</w:t>") + fld("end")
FIELD = (fld("begin") + instr(" IF ") + simple("PAGE", "1") + instr(" &lt; ") + simple("NUMPAGES", "2")
         + instr(' "lees verder ►►►" "" ') + fld("separate") + r('<w:t xml:space="preserve">lees verder ►►►</w:t>') + fld("end"))

tmp = src + ".tmp"
n = 0
with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename.startswith("word/footer"):
            xml = data.decode("utf-8")
            xml, k = re.subn(r"<w:r>(?:(?!</w:r>).)*?LEESVERDER.*?</w:r>", lambda m: FIELD, xml, flags=re.S)
            n += k
            data = xml.encode("utf-8")
        zout.writestr(item, data)
shutil.move(tmp, src)
print("velden geplaatst:", n)
