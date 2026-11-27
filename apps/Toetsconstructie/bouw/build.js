// Bouwmotor: zet één toetsbestand (JSON) om in Word-bestanden in CE-stijl.
// Gebruik: node bouw/build.js <pad/naar/toets_A.json> <uitvoermap>
// Het JSON-schema staat in bouw/SCHEMA.md. Figuren worden gezocht naast het JSON-bestand en daarna in bouw/.
const fs = require("fs");
const path = require("path");
const D = require("docx");
const { Document, Packer, Paragraph, TextRun, Tab, Table, TableRow, TableCell, WidthType, AlignmentType, BorderStyle,
  ShadingType, Header, Footer, PageNumber, ImageRun, TabStopType, PageOrientation, LevelFormat, VerticalAlign, HeightRule } = D;

const BRON = path.resolve(process.argv[2]), OUT = path.resolve(process.argv[3] || path.join(path.dirname(BRON), "uit"));
const C = JSON.parse(fs.readFileSync(BRON, "utf8"));
const M = C.meta;
const SOORT = (M.soortnaam || (/schoolexamen/i.test(M.titel || "") ? "schoolexamen" : String(M.titel || "toets").trim().split(/\s+/)[0])).toLowerCase();
const HET = /^(schoolexamen|proefwerk|tentamen|examen)$/.test(SOORT);
fs.mkdirSync(OUT, { recursive: true });
const zoek = (f) => { for (const d of [path.dirname(BRON), __dirname]) { const p = path.join(d, f); if (fs.existsSync(p)) return p; } throw new Error("Bestand niet gevonden: " + f); };
const beeld = (f) => fs.readFileSync(zoek(f));

const BECO = /bedrijf/i.test(M.vak);
const QS = C.opgaven.flatMap((o) => o.items.filter((i) => i.t === "q"));
M.vragen = QS.length; M.punten = QS.reduce((a, q) => a + q.p, 0); M.minuten = M.minuten || 100;
M.bronnaam = M.bronnaam || (BECO ? "Informatiebron" : "Bron");
const TWEE = !!M.tweefasen;
const HV = TWEE ? C.cv.filter((v) => v.nr && v.label !== "V").reduce((a, v) => a + v.max, 0) : 0;

const FONT = "Arial", W = 9638, COL = 1134, LINE = 276;
const PAGE = { size: { width: 11906, height: 16838 }, margin: { top: 1247, bottom: 1247, left: 1134, right: 1134, header: 340, footer: 567 } };
const stats = { woorden: 0 };
const tel = (s) => { stats.woorden += String(s == null ? "" : s).split(/\s+/).filter((w) => /[A-Za-zÀ-ÿ]/.test(w)).length; };
const net = (t) => String(t == null ? "" : t).replace(/€ /g, "€ ");
function runs(text, o = {}) { // ^{..} wordt superscript
  return net(text).split(/(\^\{[^}]+\})/).filter((s) => s !== "").map((s) => { const m = s.match(/^\^\{([^}]+)\}$/); return new TextRun({ text: m ? m[1] : s, font: FONT, size: 24, superScript: !!m, ...o }); });
}
const run = (text, o = {}) => new TextRun({ text: net(text), font: FONT, size: 24, ...o });
const para = (children, o = {}) => new Paragraph({ ...o, children: Array.isArray(children) ? children : [children], spacing: { line: LINE, after: 0, ...(o.spacing || {}) } });
const img = (f, w, h) => new ImageRun({ type: "png", data: beeld(f), transformation: { width: w, height: h } });

function header() { return new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [img(M.logo || "logo.png", 96, 50)] })] }); }
function footer(naam, leesVerder) {
  const kids = [para([run(`${M.datum} | ${M.at} ${naam} | ${M.maker} | ${M.vak} | ${M.kort} | Pagina `, { size: 14 }),
    new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 14 }), run(" van ", { size: 14 }), new TextRun({ children: [PageNumber.TOTAL_PAGES], font: FONT, size: 14 })], { alignment: AlignmentType.CENTER })];
  if (leesVerder) kids.unshift(para([run("LEESVERDER", { size: 20 })], { alignment: AlignmentType.RIGHT, spacing: { after: 60 } }));
  return new Footer({ children: kids });
}
const rand = { style: BorderStyle.SINGLE, size: 6, color: "000000" }, randen = { top: rand, bottom: rand, left: rand, right: rand };
function cel(text, width, o = {}) {
  const uit = o.align === "r" ? AlignmentType.RIGHT : o.align === "c" ? AlignmentType.CENTER : AlignmentType.LEFT;
  return new TableCell({ width: { size: width, type: WidthType.DXA }, borders: randen, verticalAlign: o.top ? VerticalAlign.TOP : VerticalAlign.CENTER,
    shading: o.kop ? { type: ShadingType.CLEAR, fill: "D9D9D9", color: "auto" } : undefined,
    margins: (o.size && o.size <= 18) ? { top: 25, bottom: 25, left: 90, right: 90 } : { top: 70, bottom: 70, left: 110, right: 110 },
    children: String(text == null ? "" : text).split("\n").map((r) => para(runs(r, { bold: !!o.kop || !!o.bold, size: o.size || 24 }), { alignment: uit, spacing: { line: 250 }, keepNext: !!o.keep })) });
}
function tabel(head, rows, widths, align, o = {}) {
  align = align || widths.map(() => "l"); const rijen = [];
  if (head) rijen.push(new TableRow({ tableHeader: true, cantSplit: true, children: head.map((h, i) => cel(h, widths[i], { kop: true, align: align[i], size: o.size, keep: o.keep })) }));
  rows.forEach((r, ri) => rijen.push(new TableRow({ cantSplit: true, height: o.hoogte ? { value: o.hoogte, rule: HeightRule.ATLEAST } : undefined,
    children: r.map((c, i) => cel(c, widths[i], { align: align[i], size: o.size, keep: o.keep && ri < rows.length - 1, top: o.top, kop: o.kopkolom && i === 0 })) })));
  return new Table({ width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA }, columnWidths: widths, indent: o.inspring ? { size: o.inspring, type: WidthType.DXA } : undefined, rows: rijen });
}
const breedtes = (n, totaal) => Array.from({ length: n }, () => Math.floor(totaal / n));

// ---------- voorblad en opgaven ----------
function voorblad() {
  const k = [];
  k.push(para([run(M.titel, { bold: true, size: 36 })], { alignment: AlignmentType.CENTER, spacing: { before: 200, after: 80 } }));
  k.push(para([run(`${M.kort}  –  versie ${M.versie}`, { size: 26 })], { alignment: AlignmentType.CENTER, spacing: { after: 300 } }));
  const w = [3400, 5238];
  const rij = [["Vak", M.vak], ["AT Toetsnummer / Proefwerknummer", `${M.at} (versie ${M.versie})`], ["Klas", M.klas], ["Niveau", M.niveau], ["Onderwerp", M.onderwerp], ["Datum", M.datum],
    ["Tijdsduur", `Je hebt voor deze toets ${M.minuten} minuten de tijd.`], ["Aantal vragen", `Deze toets bestaat uit ${M.vragen} vragen.`], ["Maximaal te behalen punten", `${M.punten} punten`],
    ["Weging", M.wegingtekst || `Deze toets telt mee voor ${M.weging}.`], ["Cijferbepaling", `${TWEE ? "Vwo-cijfer" : "Cijfer"} = 9 × behaalde punten : ${M.punten} + 1,0`],
    ["Toegestane hulpmiddelen", M.hulpmiddelen || "Alleen een gewone rekenmachine is toegestaan, geen grafische rekenmachine."]];
  k.push(new Table({ width: { size: 8638, type: WidthType.DXA }, columnWidths: w, alignment: AlignmentType.CENTER,
    rows: rij.map((r) => new TableRow({ cantSplit: true, children: [cel(r[0], w[0], { kop: true, size: 22 }), cel(r[1], w[1], { size: 22 })] })) }));
  const delen = [C.bijlage ? "een bijlage" : null, C.uitwerkbijlage ? "een uitwerkbijlage" : null].filter(Boolean);
  if (delen.length) k.push(para([run(`Bij ${HET ? "dit" : "deze"} ${SOORT} ${delen.length > 1 ? "horen" : "hoort"} ${delen.join(" en ")}.`, { size: 22 })], { spacing: { before: 240, after: 160 } }));
  k.push(para([run("Let op:", { bold: true, size: 22 })], { spacing: { before: delen.length ? 0 : 240, after: 60 } }));
  const letop = M.letop || [
    ["Rekenopgaven", "vermeld altijd de volledige berekening en formule. Een los eindantwoord levert nul punten op."],
    ["Formules", M.formuletekst || "de formules worden niet gegeven. Je moet ze uit je hoofd kennen."],
    ["Tussenantwoorden", "rond tussenantwoorden op je rekenmachine niet af."],
    ["Uitleg- en redeneervragen", "geef een volledige causale verklaring (oorzaak → tussenstap → gevolg)."],
    ["Aantal antwoorden", "wordt er gevraagd om een specifiek aantal en noem je er meer? Dan worden uitsluitend de eerstgenoemde antwoorden beoordeeld."],
    ["Keuze met uitleg", "een keuze zonder of met een foutieve toelichting levert nul punten op."],
    ["Schrijfstijl", "maak de toets uitsluitend met een zwarte of blauwe pen en schrijf leesbaar. Antwoorden die niet leesbaar zijn, worden fout gerekend."],
    ["Afrondingsregels", M.afronding || (BECO ? "bedragen afronden op hele euro’s, tenzij de vraag anders aangeeft." : "geldbedragen afronden op twee decimalen en percentages op één decimaal, tenzij de vraag anders aangeeft.")]];
  letop.forEach((r) => k.push(new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { line: 260, after: 40 }, children: [run(r[0] + ": ", { bold: true, size: 22 }), run(r[1], { size: 22 })] })));
  k.push(para([run("Voor elk vraagnummer staat hoeveel punten met een goed antwoord behaald kunnen worden.", { size: 22 })], { spacing: { before: 160 } }));
  return k;
}
const tabs = [{ type: TabStopType.LEFT, position: 567 }, { type: TabStopType.LEFT, position: COL }];
function vraag(it) {
  tel(it.text);
  const k = [new Paragraph({ tabStops: tabs, indent: { left: COL, hanging: COL }, keepLines: true, keepNext: !!it.extra, spacing: { line: LINE, after: it.extra ? 0 : 200 },
    children: [run(`${it.p}p`), new TextRun({ children: [new Tab(), String(it.nr)], font: FONT, size: 24, bold: true }), new TextRun({ children: [new Tab(), net(it.text)], font: FONT, size: 24 })] })];
  (it.extra || []).forEach((e, i, a) => { tel(e); const st = /^−/.test(e); k.push(new Paragraph({ indent: st ? { left: COL + 400, hanging: 400 } : { left: COL }, tabStops: [{ type: TabStopType.LEFT, position: COL + 400 }], keepLines: true, keepNext: i < a.length - 1, spacing: { line: LINE, after: i === a.length - 1 ? 200 : 0 }, children: [run(st ? e.replace(/^−\s+/, "−\t") : e)] })); });
  return k;
}
const klein = () => new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: 200, after: 0 }, children: [run("", { size: 16 })] });
function opgavenDoc() {
  const k = voorblad();
  C.opgaven.forEach((o) => {
    k.push(new Paragraph({ pageBreakBefore: !o.doorlopend, keepNext: true, spacing: { line: LINE, before: o.doorlopend ? 480 : 0, after: 240 }, children: [run(o.titel, { bold: true, size: 28 })] }));
    o.items.forEach((it, i) => {
      const v = o.items[i + 1];
      if (it.t === "p") { tel(it.text); k.push(new Paragraph({ indent: { left: COL }, keepLines: true, keepNext: true, spacing: { line: LINE, after: v && v.t === "li" ? 0 : 200 }, children: [run(it.text)] })); }
      if (it.t === "li") { tel(it.text); k.push(new Paragraph({ indent: { left: COL + 400, hanging: 400 }, tabStops: [{ type: TabStopType.LEFT, position: COL + 400 }], keepLines: true, keepNext: true, spacing: { line: LINE, after: v && v.t === "li" ? 0 : 200 }, children: [run(it.text.replace(/^−\s+/, "−\t"))] })); }
      if (it.t === "use") { tel(it.text); k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: LINE, after: 0 }, children: [run(it.text)] })); }
      if (it.t === "q") k.push(...vraag(it));
      if (it.t === "tab") {
        tel(it.titel); (it.head || []).concat(...it.rows).forEach(tel);
        const wd = it.widths || breedtes(it.rows[0].length, W - COL);
        k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: LINE, before: 40, after: 80 }, children: [run(it.titel, { bold: true })] }));
        k.push(tabel(it.head || null, it.rows, wd, it.align, { size: 22, keep: true, kopkolom: it.kopkolom, inspring: COL }));
        (it.legenda || []).forEach((l, li) => { tel(l); k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: 250, before: li === 0 ? 60 : 0, after: 0 }, children: [run(l, { size: 20 })] })); });
        k.push(klein());
      }
      if (it.t === "fig") {
        tel(it.titel);
        k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: LINE, before: 40, after: 60 }, children: [run(it.titel, { bold: true })] }));
        k.push(new Paragraph({ indent: { left: COL }, keepNext: true, children: [img(it.file, it.w, it.h)] }));
        (it.legenda || []).forEach((l) => { tel(l); k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: 250, after: 0 }, children: [run(l, { size: 20 })] })); });
        k.push(klein());
      }
    });
  });
  k.push(para([run("Einde " + SOORT, { bold: true })], { alignment: AlignmentType.CENTER, spacing: { before: 160 } }));
  return k;
}

// ---------- bijlage ----------
function kader(lines) {
  const ch = lines.map((l, i) => { const [soort, t] = Array.isArray(l) ? l : ["p", l]; tel(t);
    if (soort === "b") return para([run(t, { bold: true })], { spacing: { line: LINE, before: i ? 120 : 0, after: 40 }, keepNext: true });
    if (soort === "-") return new Paragraph({ numbering: { reference: "boxbullets", level: 0 }, spacing: { line: LINE, after: 0 }, children: [run(t)] });
    return para([run(t, { size: String(t).startsWith("*") ? 20 : 24 })], { spacing: { line: LINE, after: i < lines.length - 1 ? 120 : 0 } }); });
  return new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: [W], rows: [new TableRow({ cantSplit: true, children: [new TableCell({ width: { size: W, type: WidthType.DXA }, borders: randen, margins: { top: 140, bottom: 140, left: 180, right: 180 }, children: ch })] })] });
}
function bijlageDoc() {
  const B = C.bijlage, k = [];
  k.push(para([run(`Bijlage bij het ${M.titel.toLowerCase()}, versie ${M.versie}`, { bold: true, size: 30 })], { spacing: { after: 80 } }));
  k.push(para([run(M.kort, { size: 26 })], { spacing: { after: 160 } }));
  if (B.intro) k.push(para([run(B.intro, { size: 22 })], { spacing: { after: 160 } }));
  (B.bronnen || []).forEach((b) => {
    if (b.kop) { tel(b.kop); k.push(new Paragraph({ pageBreakBefore: !!b.nieuwePagina, keepNext: true, spacing: { line: LINE, before: b.nieuwePagina ? 0 : 240, after: 160 }, children: [run(b.kop, { bold: true, size: 28 })] })); return; }
    tel(b.titel);
    if (BECO) { k.push(new Paragraph({ keepNext: true, pageBreakBefore: !!b.nieuwePagina, spacing: { line: LINE, before: 200, after: 40 }, children: [run(`${M.bronnaam} ${b.nr}`, { bold: true, size: 26 })] }));
      k.push(new Paragraph({ keepNext: true, spacing: { line: LINE, after: 100 }, children: [run(b.titel, { bold: true })] })); }
    else k.push(new Paragraph({ keepNext: true, pageBreakBefore: !!b.nieuwePagina, spacing: { line: LINE, before: 120, after: 100 }, children: [run(`${M.bronnaam} ${b.nr}  `, { bold: true }), run(b.titel, { bold: true })] }));
    if (b.type === "table") { (b.head || []).concat(...b.rows).forEach(tel); k.push(tabel(b.head || null, b.rows, b.widths || breedtes(b.rows[0].length, W), b.align, { keep: true })); }
    if (b.type === "figure") k.push(new Paragraph({ alignment: AlignmentType.CENTER, keepNext: !!(b.note || b.legenda), children: [img(b.file, b.w, b.h)] }));
    if (b.type === "box" || b.type === "article") k.push(kader(b.lines));
    if (b.type === "text") b.lines.forEach((l) => { tel(l); k.push(tabel(null, [[l]], [W], ["l"])); });
    (b.legenda || []).forEach((l, li, a) => { tel(l); k.push(para([run(l, { size: 22 })], { spacing: { line: 250, before: li ? 0 : 80 }, keepNext: li < a.length - 1 })); });
    if (b.note) k.push(para([run(b.note, { size: 16, color: "444444" })], { spacing: { before: 60, line: 220 } }));
    k.push(para([run("")], { spacing: { after: 100 } }));
  });
  if (B.formules) {
    k.push(new Paragraph({ pageBreakBefore: true, keepNext: true, spacing: { line: LINE, after: 120 }, children: [run("Formuleblad", { bold: true, size: 28 })] }));
    B.formules.forEach(([soort, t]) => k.push(soort === "b" ? para([run(t, { bold: true })], { spacing: { line: LINE, before: 200, after: 60 }, keepNext: true }) : para([run(t, { size: soort === "k" ? 22 : 24 })], { indent: { left: soort === "k" ? 400 : 0 }, spacing: { line: LINE, after: soort === "k" ? 0 : 40 } })));
  }
  return k;
}

// ---------- uitwerkbijlage ----------
function uitwerkDoc() {
  const k = [];
  k.push(para([run(`Uitwerkbijlage ${M.titel.toLowerCase()}, versie ${M.versie}`, { bold: true, size: 28 })], { spacing: { after: 240 } }));
  k.push(tabel(null, [["Naam:", "", "Klas:", ""]], [1300, 5138, 1100, 2100], null, { hoogte: 620 }));
  C.uitwerkbijlage.forEach((U, ui) => {
    k.push(new Paragraph({ pageBreakBefore: ui > 0, keepNext: true, spacing: { line: LINE, before: ui > 0 ? 0 : 420, after: 80 }, children: [run(`Vraag ${U.vraag}`, { bold: true, size: 28 })] }));
    k.push(para([run(U.titel, { bold: true })], { spacing: { after: 140 }, keepNext: true }));
    if (U.file) k.push(new Paragraph({ alignment: AlignmentType.CENTER, children: [img(U.file, U.w, U.h)] }));
    if (U.rows) k.push(tabel(U.head || null, U.rows, U.widths || breedtes(U.rows[0].length, W), null, { hoogte: U.hoogte || 1200, top: true, size: 22 }));
    if (U.legenda) { k.push(para([run("Legenda", { bold: true })], { spacing: { before: 240, after: 120 } })); U.legenda.forEach((l) => k.push(tabel(null, [["", l]], [1200, 6000], null, { hoogte: 620 }), para([run("")], { spacing: { after: 60 } }))); }
    if (U.noot) k.push(para([run(U.noot, { size: 20 })], { spacing: { before: 100 } }));
  });
  k.push(para([run("VERGEET NIET DEZE UITWERKBIJLAGE IN TE LEVEREN", { bold: true })], { alignment: AlignmentType.CENTER, spacing: { before: 500 } }));
  return k;
}

// ---------- correctievoorschrift ----------
const REGELS = C.regels || (BECO ? [
  ["Rekenfouten", "voor rekenfouten worden geen scorepunten afgetrokken. Heeft de leerling kennelijk niet door schatting gecontroleerd of het antwoord kan kloppen, dan wordt 1 scorepunt afgetrokken, tot ten hoogste 2 scorepunten per opgave."],
  ["Afronding", "stelt de vraag geen eis aan de afronding, dan is elke juiste afronding goed. Stelt de vraag wel een eis, dan kost een verkeerde afronding ten hoogste 1 scorepunt."],
  ["Doorwerken", "een uitkomst van een eerdere vraag mag worden gebruikt, ook als die fout is. Een fout wordt niet twee keer aangerekend."],
  ["Berekening in stappen", "bij ‘Per gemaakte fout 1 scorepunt in mindering brengen’ kan de score niet lager worden dan 0."],
] : [
  ["Doorwerkfout", "een fout uit een eerdere vraag wordt bij een latere vraag niet opnieuw aangerekend, als de methode juist is."],
  ["Rekenfout bij een juiste methode", "maximaal 1 scorepunt aftrek per vraag."],
  ["Afrondingsfout", "maximaal 1 scorepunt aftrek per vraag. Als de leerling met niet-afgeronde tussenantwoorden rekent, kan de maximumscore worden behaald."],
]).concat([
  ["Schrijfwijze", "een berekening bestaat uit de formule of het uitgangspunt, de ingevulde berekening en het antwoord met eenheid. Alleen een eindantwoord levert geen scorepunten op."],
  ["Gelijke strekking", "een antwoord dat inhoudelijk overeenkomt met het voorbeeldantwoord wordt goed gerekend. Een vakinhoudelijk juist antwoord dat niet in dit voorschrift staat, wordt beoordeeld naar de geest van het voorschrift."],
  ["Te veel antwoorden", "als meer antwoorden worden gegeven dan gevraagd, worden alleen de eerstgenoemde antwoorden beoordeeld."],
]).concat(TWEE ? [["Twee cijfers", `vwo-cijfer = 9 × alle behaalde punten : ${M.punten} + 1,0. Havo-cijfer = 9 × behaalde punten op de vragen met het label ‘Havo en vwo’ : ${HV} + 1,0. De labels staan niet in de toets.`]] : []);
const rTab = [{ type: TabStopType.RIGHT, position: W }];
const labelTekst = (v) => (v.label === "V" ? "Alleen vwo" : "Havo en vwo");
function cvVraag(v) {
  const k = [];
  k.push(new Paragraph({ tabStops: [{ type: TabStopType.LEFT, position: COL }], keepNext: true, spacing: { line: LINE, before: 200, after: 40 },
    children: [run(String(v.nr), { bold: true }), new TextRun({ children: [new Tab(), `maximumscore ${v.max}`], font: FONT, size: 24, bold: true })] }));
  if (v.intro) k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: LINE, after: 0 }, children: [run(v.intro)] }));
  (v.plain || []).forEach((l) => k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: LINE, after: 0 }, children: runs(l) })));
  (v.list || []).forEach((l) => k.push(new Paragraph({ numbering: { reference: "cvdash", level: 0 }, keepLines: true, keepNext: true, spacing: { line: LINE, after: 0 }, indent: { left: COL + 360, hanging: 360, right: 700 }, children: [run(l)] })));
  if ((v.plain || v.list) && v.bullets) k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: 120, after: 0 }, children: [run("", { size: 12 })] }));
  const kaal = !!(v.plain || v.list);
  (v.bullets || []).forEach(([t, s]) => k.push(new Paragraph({ numbering: kaal ? undefined : { reference: "cvbullets", level: 0 }, tabStops: rTab, keepLines: true, keepNext: true, spacing: { line: LINE, after: 0 },
    indent: kaal ? { left: COL, right: 700 } : { left: COL + 360, hanging: 360, right: 700 }, children: [...runs(t), new TextRun({ children: [new Tab(), String(s)], font: FONT, size: 24 })] })));
  if (v.tabel) { k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: 120, after: 0 }, children: [run("", { size: 12 })] }));
    k.push(tabel(v.tabel.head, v.tabel.rows, v.tabel.widths || breedtes(v.tabel.rows[0].length, W - COL), v.tabel.align, { size: 20, keep: true, inspring: COL })); }
  if (v.fig) k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { before: 80 }, children: [img(v.fig.file, v.fig.w, v.fig.h)] }));
  if (v.opm) { k.push(new Paragraph({ indent: { left: COL }, keepNext: true, spacing: { line: LINE, before: 100, after: 0 }, children: [run("Opmerking", { italics: true })] }));
    k.push(new Paragraph({ indent: { left: COL }, keepLines: true, keepNext: true, spacing: { line: LINE, after: 0 }, children: [run(v.opm, { italics: true })] })); }
  if (v.fout) k.push(new Paragraph({ indent: { left: COL }, keepLines: true, keepNext: true, spacing: { line: LINE, before: 100, after: 0 }, children: [run("Veelgemaakte fout: ", { bold: true, size: 20 }), run(v.fout, { size: 20 })] }));
  k.push(new Paragraph({ indent: { left: COL }, spacing: { line: LINE, before: v.fout ? 0 : 100, after: 0 }, children: [...(TWEE ? [run("Label: ", { bold: true, size: 20 }), run(labelTekst(v) + "   ", { size: 20 })] : []), run("Bloom-niveau: ", { bold: true, size: 20 }), run(v.bloom, { size: 20 })] }));
  return k;
}
function cvDoc() {
  const k = [];
  k.push(para([run(`Correctievoorschrift ${M.titel.toLowerCase()}, versie ${M.versie}`, { bold: true, size: 30 })], { spacing: { after: 80 } }));
  k.push(para([run(`${M.at}  –  ${M.onderwerp}`, { size: 24 })], { spacing: { after: 240 } }));
  k.push(para([run(`${HET ? "Het" : "De"} ${SOORT} bestaat uit ${M.vragen} vragen. Er zijn maximaal ${M.punten} scorepunten te behalen${TWEE ? `, waarvan ${HV} op vragen die ook voor het havo-cijfer tellen` : ""}. ${TWEE ? "Vwo-cijfer" : "Cijfer"} = 9 × behaalde punten : ${M.punten} + 1,0.`)], { spacing: { after: 240 } }));
  k.push(para([run("Deel A  Correctievoorschrift (voor de docent)", { bold: true, size: 28 })], { spacing: { after: 160 } }));
  k.push(para([run("Algemene regels", { bold: true })], { spacing: { after: 80 } }));
  REGELS.forEach(([a, b]) => k.push(new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { line: LINE, after: 40 }, children: [run(a + ": ", { bold: true }), run(b)] })));
  C.cv.forEach((v) => { if (v.opgave) k.push(new Paragraph({ keepNext: true, pageBreakBefore: true, spacing: { line: LINE, after: 80 }, children: [run(v.opgave, { bold: true, size: 26 })] })); else k.push(...cvVraag(v)); });
  k.push(new Paragraph({ pageBreakBefore: true, spacing: { line: LINE, after: 120 }, children: [run("Deel B  Nabespreekversie (voor de leerling)", { bold: true, size: 28 })] }));
  k.push(para([run("Per vraag staat een voorbeeldantwoord in leerlingtaal dat de volle score oplevert, met een korte uitleg.")], { spacing: { after: 160 } }));
  C.cv.forEach((v) => {
    if (v.opgave) { k.push(new Paragraph({ keepNext: true, spacing: { line: LINE, before: 240, after: 80 }, children: [run(v.opgave, { bold: true, size: 26 })] })); return; }
    k.push(new Paragraph({ keepNext: true, spacing: { line: LINE, before: 160, after: 20 }, children: [run(`Vraag ${v.nr}`, { bold: true }), run(`  (${v.max}p)`)] }));
    k.push(new Paragraph({ keepLines: true, keepNext: true, indent: { left: 400 }, spacing: { line: LINE, after: 40 }, children: [run("Voorbeeldantwoord: ", { bold: true }), ...runs(v.voorbeeld)] }));
    k.push(new Paragraph({ keepLines: true, indent: { left: 400 }, spacing: { line: LINE, after: 0 }, children: [run("Uitleg: ", { bold: true }), ...runs(v.uitleg)] }));
  });
  return k;
}
function matrijsSectie() {
  const R = C.matrijs.rijen, G = C.matrijs.gevraagd || {}, pp = M.minuten / M.punten;
  const k = [para([run(`Toetsmatrijs ${M.at} ${M.vak.toLowerCase()} ${M.klas}, versie ${M.versie}`, { bold: true, size: 26 })], { spacing: { after: 100 } })];
  const kol = [["Nr", 400, "c", (r) => r.nr], ["Leerdoel", 3400, "l", (r) => r.leerdoel], ["Eindterm", 1050, "l", (r) => r.eindterm]]
    .concat(TWEE ? [["Label", 600, "c", (r) => (r.label === "V" ? "V" : "H+V")]] : [])
    .concat([["Werkwoord en Bloom", 2100, "l", (r) => `${r.werkwoord} – ${r.bloom}`], ["Koppeling eindexamen", TWEE ? 2250 : 2650, "l", (r) => r.koppeling], ["Thema / context", TWEE ? 1750 : 1950, "l", (r) => r.thema], ["Bron", 1000, "l", (r) => r.bron || "–"],
      ["P", 400, "c", (r) => r.punten], ["Aandeel", 800, "r", (r) => (r.punten / M.punten * 100).toFixed(1).replace(".", ",") + "%"], ["Tijd (min)", 720, "r", (r) => (r.punten * pp).toFixed(1).replace(".", ",")]]);
  const rows = R.map((r) => kol.map((c) => c[3](r)));
  const tot = kol.map((c) => ""); tot[1] = "Totaal"; tot[kol.length - 3] = String(R.reduce((a, r) => a + r.punten, 0)); tot[kol.length - 2] = "100,0%"; tot[kol.length - 1] = String(M.minuten);
  rows.push(tot);
  k.push(tabel(kol.map((c) => c[0]), rows, kol.map((c) => c[1]), kol.map((c) => c[2]), { size: 14 }));
  k.push(para([run("")]));
  const som = (f) => R.filter(f).reduce((a, r) => a + r.punten, 0), pct = (n) => (n / M.punten * 100).toFixed(0) + "%", gv = (o, n) => (o && o[n] != null ? o[n] + "%" : "–");
  const onderdelen = [...new Set(R.map((r) => r.onderdeel).filter(Boolean))];
  const t2 = onderdelen.map((o) => [o, som((r) => r.onderdeel === o), gv(G.onderdelen, o)])
    .concat(TWEE ? [["Label H+V (telt in havo- en vwo-cijfer)", som((r) => r.label !== "V"), "–"], ["Label V (telt alleen in het vwo-cijfer)", som((r) => r.label === "V"), "–"]] : [])
    .concat(["Onthouden", "Begrijpen", "Toepassen", "Analyseren", "Evalueren", "Creëren"].filter((b) => som((r) => r.bloom === b) || (G.bloom && G.bloom[b] != null)).map((b) => [b, som((r) => r.bloom === b), gv(G.bloom, b)]))
    .map(([a, n, g]) => [a, String(n), pct(n), g]);
  k.push(tabel(["Verdeling", "Punten", "Aandeel", "Gevraagd"], t2, [5600, 1200, 1200, 1200], ["l", "r", "r", "r"], { size: 18, keep: true }));
  return { k, t2 };
}

const bul = (ref, text, left) => ({ reference: ref, levels: [{ level: 0, format: LevelFormat.BULLET, text, alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left, hanging: text === "•" && left === 400 ? 300 : 360 } } } }] });
const numbering = { config: [bul("bullets", "•", 400), bul("boxbullets", "•", 360), bul("cvbullets", "•", COL + 360), bul("cvdash", "−", COL + 360)] };
const styles = { default: { document: { run: { font: FONT, size: 24 } } } };
async function schrijf(naam, sections) {
  const doc = new Document({ creator: M.maker, title: naam, styles, numbering, sections });
  fs.writeFileSync(path.join(OUT, naam + ".docx"), await Packer.toBuffer(doc)); return naam + ".docx";
}
(async () => {
  const basis = M.bestandsbasis || `${M.at}_SE_${M.niveau.toLowerCase()}${String(M.klas).replace(/\D/g, "")}_${M.vak.toLowerCase()}_versie_${M.versie}`;
  const A4 = { properties: { page: PAGE }, headers: { default: header() } }, uit = [];
  uit.push(await schrijf(basis + "_opgaven", [{ ...A4, footers: { default: footer("versie " + M.versie, true) }, children: opgavenDoc() }]));
  if (C.bijlage) uit.push(await schrijf(basis + "_bijlage", [{ ...A4, footers: { default: footer("bijlage " + M.versie, false) }, children: bijlageDoc() }]));
  if (C.uitwerkbijlage) uit.push(await schrijf(basis + "_uitwerkbijlage", [{ ...A4, footers: { default: footer("uitwerkbijlage " + M.versie, false) }, children: uitwerkDoc() }]));
  const m = matrijsSectie();
  uit.push(await schrijf(basis + "_correctievoorschrift", [{ ...A4, footers: { default: footer("correctievoorschrift " + M.versie, false) }, children: cvDoc() },
    { properties: { page: { size: { width: 11906, height: 16838, orientation: PageOrientation.LANDSCAPE }, margin: { top: 1000, bottom: 900, left: 1134, right: 1134, header: 340, footer: 500 } } },
      headers: { default: header() }, footers: { default: footer("toetsmatrijs " + M.versie, false) }, children: m.k }]));
  fs.writeFileSync(path.join(OUT, `bouwverslag_${M.versie}.json`), JSON.stringify({ versie: M.versie, bestanden: uit, punten: M.punten, vragen: M.vragen, woorden: stats.woorden, minuten_per_punt: +(M.minuten / M.punten).toFixed(2), verdeling: m.t2, opgaven_bestand: basis + "_opgaven.docx" }, null, 1));
  console.log("GEBOUWD", uit.join(", "));
})().catch((e) => { console.error("BOUWFOUT:", e.message); process.exit(1); });
