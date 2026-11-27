// Toetsconstructie-app: scherm
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (url, opt = {}) => { const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opt, body: opt.body ? JSON.stringify(opt.body) : undefined }); if (!r.ok) throw new Error((await r.text()).replace(/<[^>]+>/g, " ").trim().slice(0, 300)); return r.json(); };
const S = { bronnen: [], toetsen: [], pid: null, data: null, tab: "selectie", bezig: false, filter: { bieb: { q: "", map: "", vak: "", niveau: "", soort: "" }, sel: { q: "", map: "", vak: "", niveau: "", soort: "", alleen: false } } };
const BLOOM = ["Onthouden", "Begrijpen", "Toepassen", "Analyseren", "Evalueren", "Creëren"];
const STAPPEN = [["intake", "1 Intake"], ["opdracht", "2 Toetsopdracht"], ["matrijs", "3 Toetsmatrijs"], ["goedgekeurd", "4 Bouwen"], ["gebouwd", "5 Gebouwd"], ["geevalueerd", "6 Geëvalueerd"]];

// ---------- start ----------
async function start() {
  const st = await api("/api/status");
  const cOk = st.claude && st.ingelogd;
  $("pilClaude").textContent = cOk ? "Claude Code ✓" : (st.claude ? "Claude Code: niet ingelogd" : "Claude Code ontbreekt"); $("pilClaude").className = "pil " + (cOk ? "goed" : "fout"); $("pilClaude").title = cOk ? st.claude_versie : "Sluit de app en dubbelklik opnieuw op 'Start Toetsconstructie.command'. Dat bestand installeert Claude Code en laat u inloggen met uw abonnement.";
  const b = st.node && st.bouwmotor; $("pilBouw").textContent = b ? "Bouwmotor ✓" : (st.node ? "Bouwmotor niet geïnstalleerd" : "Node.js ontbreekt"); $("pilBouw").className = "pil " + (b ? "goed" : "fout");
  await Promise.all([laadBronnen(), laadToetsen()]); toonBieb();
}
async function laadBronnen() { S.bronnen = await api("/api/bronnen"); }
async function laadToetsen() { S.toetsen = await api("/api/toetsen"); tekenToetsen(); }
function tekenToetsen() {
  $("toetslijst").innerHTML = S.toetsen.length ? S.toetsen.map((t) => `<div class="toets ${t.id === S.pid ? "actief" : ""}" onclick="openToets('${t.id}')"><div class="n">${esc(t.naam)}</div><span class="badge ${t.status}">${esc(statusNaam(t.status))}</span></div>`).join("") : `<div class="sub">Nog geen toetsen.</div>`;
}
const statusNaam = (s) => ({ intake: "intake", matrijs: "matrijs ter goedkeuring", goedgekeurd: "wordt gebouwd", gebouwd: "klaar" }[s] || s);

// ---------- bronnenlijst (bibliotheek en selectie) ----------
function filterBalk(doel, f, opnieuw) {
  const uniek = (k) => [...new Set(S.bronnen.map((b) => b[k]).filter(Boolean))].sort();
  const sel = (k, label) => `<select onchange="S.filter.${doel}.${k}=this.value;${opnieuw}()"><option value="">${label}</option>${uniek(k).map((v) => `<option ${f[k] === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
  return `<input type="text" placeholder="Zoek op naam" value="${esc(f.q)}" oninput="S.filter.${doel}.q=this.value;${opnieuw}(true)">${sel("map", "alle mappen")}${sel("vak", "alle vakken")}${sel("niveau", "alle niveaus")}${sel("soort", "alle soorten")}`;
}
const past = (b, f) => (!f.q || b.naam.toLowerCase().includes(f.q.toLowerCase())) && (!f.map || b.map === f.map) && (!f.vak || b.vak === f.vak) && (!f.niveau || b.niveau === f.niveau) && (!f.soort || b.soort === f.soort);
function rijen(lijst, metVink) {
  if (!lijst.length) return `<div class="leeg">Geen bestanden gevonden.</div>`;
  const sel = new Set(metVink ? S.data.project.selectie : []); let uit = "", groep = null;
  lijst.forEach((b) => {
    const g = b.map + (b.submap ? " / " + b.submap : "");
    if (g !== groep) { groep = g; uit += `<tr class="groepkop"><td colspan="${metVink ? 6 : 6}">${esc(g)}</td></tr>`; }
    uit += `<tr class="klik">${metVink ? `<td style="width:26px"><input type="checkbox" ${sel.has(b.pad) ? "checked" : ""} onchange="vink('${encodeURIComponent(b.pad)}',this.checked)"></td>` : ""}<td class="naam" onclick="lees('${encodeURIComponent(b.pad)}')">${esc(b.naam)}</td><td>${esc(b.vak)}</td><td>${esc(b.niveau)}</td><td>${esc(b.soort)}</td><td class="n">${b.kb} kB</td>${metVink ? "" : `<td class="n">${b.map === "bronnen" || b.map === "kennisbank" ? `<span class="link" onclick="weg('${encodeURIComponent(b.pad)}')">verwijder</span>` : ""}</td>`}</tr>`;
  });
  return `<table><tr>${metVink ? "<th></th>" : ""}<th>Bestand</th><th>Vak</th><th>Niveau</th><th>Soort</th><th class="n">Grootte</th>${metVink ? "" : "<th></th>"}</tr>${uit}</table>`;
}
function toonBieb() {
  S.pid = null; tekenToetsen(); $("bieb").hidden = false; $("project").hidden = true; $("kwaliteit").hidden = true; $("navBieb").classList.add("actief"); $("navKwal").classList.remove("actief");
  $("tekst").disabled = true; $("stuur").disabled = true; $("spreek").disabled = true; stopSpreek(); $("snel").innerHTML = ""; $("chatStatus").textContent = "";
  $("berichten").innerHTML = `<div class="leegchat">Kies of maak links een toets. Vertel daarna hier wat u wilt toetsen.</div>`; tekenBieb();
}
function tekenBieb(alleenLijst) {
  if (!alleenLijst) $("biebFilter").innerHTML = filterBalk("bieb", S.filter.bieb, "tekenBieb");
  $("biebLijst").innerHTML = rijen(S.bronnen.filter((b) => past(b, S.filter.bieb)), false);
}
async function weg(pad) { pad = decodeURIComponent(pad); if (!confirm("Dit bestand naar de prullenbak van de app verplaatsen?\n" + pad)) return; await api("/api/bronnen/verwijder", { method: "POST", body: { pad } }); await laadBronnen(); tekenBieb(); }

async function upload(files) {
  if (!files.length) return; const fd = new FormData();
  [...files].forEach((f) => fd.append("bestanden", f)); fd.append("vak", $("upVak").value); fd.append("niveau", $("upNiveau").value); fd.append("doel", $("upDoel").value);
  $("upMeld").textContent = `Bezig met ${files.length} bestand(en)…`;
  const r = await fetch("/api/bronnen/upload", { method: "POST", body: fd }); const j = await r.json();
  $("upMeld").textContent = r.ok ? `${j.opgeslagen.length} bestand(en) opgeslagen: ${j.opgeslagen.slice(0, 3).join(", ")}${j.opgeslagen.length > 3 ? " …" : ""}` : "Opslaan is mislukt.";
  await laadBronnen(); tekenBieb();
}
const drop = $("drop");
["dragenter", "dragover"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((e) => drop.addEventListener(e, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (ev) => upload(ev.dataTransfer.files)); $("kies").addEventListener("change", (ev) => upload(ev.target.files));

// ---------- leesvenster ----------
async function lees(pad, projectBestand) {
  pad = decodeURIComponent(pad); const url = "/bestand?pad=" + encodeURIComponent(pad);
  const j = await api("/api/lees?pad=" + encodeURIComponent(pad)); $("lezerNaam").textContent = j.naam; $("lezerDl").href = url + "&download=1";
  const el = $("lezerInhoud");
  if (j.soort === "pdf") el.innerHTML = `<iframe src="${url}"></iframe>`;
  else if (j.soort === "beeld") el.innerHTML = `<img src="${url}" alt="">`;
  else if (j.soort === "html") el.innerHTML = `<div class="docx">${j.inhoud}</div>`;
  else el.innerHTML = `<pre>${esc(j.inhoud)}</pre>`;
  $("lezer").hidden = false;
}
function sluitLezer() { $("lezer").hidden = true; $("lezerInhoud").innerHTML = ""; }

// ---------- toets openen ----------
function openNieuw() {
  $("venster").innerHTML = `<h2>Nieuwe toets</h2>
    <div class="veld"><label>Vak</label><select id="nVak"><option>Economie</option><option>Bedrijfseconomie</option></select></div>
    <div class="veld"><label>Niveau</label><select id="nNiveau"><option>Havo</option><option>Vwo</option><option>Mavo</option></select></div>
    <div class="veld"><label>Klas</label><input type="text" id="nKlas" placeholder="5 havo"></div>
    <div class="veld"><label>AT-nummer</label><input type="text" id="nAt" placeholder="AT5.12"></div>
    <div class="veld"><label>Soort</label><select id="nSoort"><option>schoolexamen</option><option>toets</option><option>herkansing</option><option>oefentoets</option></select></div>
    <div class="veld"><label>Naam</label><input type="text" id="nNaam" placeholder="wordt anders: AT-nummer vak klas"></div>
    <div class="knoppen"><button class="licht" onclick="sluitModaal()">Annuleren</button><button onclick="maakToets()">Aanmaken</button></div>`;
  $("modaal").hidden = false;
}
function sluitModaal() { $("modaal").hidden = true; }
async function maakToets() {
  const p = await api("/api/toetsen", { method: "POST", body: { vak: $("nVak").value, niveau: $("nNiveau").value, klas: $("nKlas").value, at: $("nAt").value, soort: $("nSoort").value, naam: $("nNaam").value } });
  sluitModaal(); await laadToetsen(); openToets(p.id);
}
async function openToets(pid, behoudTab) {
  S.pid = pid; S.data = await api("/api/toetsen/" + pid); const p = S.data.project;
  $("bieb").hidden = true; $("kwaliteit").hidden = true; $("project").hidden = false; $("navBieb").classList.remove("actief"); $("navKwal").classList.remove("actief"); tekenToetsen();
  $("pNaam").textContent = p.naam; $("pSub").innerHTML = `${esc(p.vak)} · ${esc(p.klas || p.niveau)} · ${esc(p.soort)} · aangemaakt ${esc(p.aangemaakt)}${p.bron_project ? ` · ${esc(p.doel || "kopie")} van een eerdere toets` : ""} · <span class="link" onclick="maakKopie()">kopie maken</span> · <span class="link" onclick="verwijderToets()">verwijderen</span>`;
  const stN = p.status === "opdracht_ok" ? "matrijs" : p.status, i = STAPPEN.findIndex((s) => s[0] === stN); $("stappen").innerHTML = STAPPEN.map((s, k) => `<span class="stap ${k === i ? "nu" : k < i ? "klaar" : ""}">${s[1]}</span>`).join("");
  if (!behoudTab) { S.bewerk = false; S.tab = standaardTab(p.status); }
  tekenTab(); tekenGesprek(); S.bezig = S.data.bezig; zetInvoer();
}
async function verwijderToets() { if (!confirm("Deze toets naar de prullenbak van de app verplaatsen?")) return; await api("/api/toetsen/" + S.pid, { method: "DELETE" }); await laadToetsen(); toonBieb(); }
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => { S.tab = b.dataset.tab; tekenTab(); }));
function tekenTab() {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("actief", b.dataset.tab === S.tab));
  ["selectie", "opdracht", "matrijs", "bestanden"].forEach((t) => ($("tab-" + t).hidden = t !== S.tab));
  if (S.tab === "opdracht") tekenOpdracht(); if (S.tab === "selectie") tekenSelectie(); if (S.tab === "matrijs") tekenMatrijs(); if (S.tab === "bestanden") tekenBestanden();
}

// ---------- selectie ----------
function tekenSelectie(alleenLijst) {
  const f = S.filter.sel, sel = new Set(S.data.project.selectie);
  if (!alleenLijst) $("selFilter").innerHTML = filterBalk("sel", f, "tekenSelectie") + `<label><input type="checkbox" ${f.alleen ? "checked" : ""} onchange="S.filter.sel.alleen=this.checked;tekenSelectie()"> alleen gekozen</label><span class="sub" id="selTel"></span><button class="licht" onclick="kiesAlles(true)">Kies alles in deze lijst</button><button class="licht" onclick="kiesAlles(false)">Wis deze lijst</button>`;
  const lijst = S.bronnen.filter((b) => past(b, f) && (!f.alleen || sel.has(b.pad)));
  $("selLijst").innerHTML = rijen(lijst, true); const kb = S.bronnen.filter((b) => sel.has(b.pad)).reduce((a, b) => a + b.kb, 0);
  $("selTel").textContent = `${sel.size} gekozen (${Math.round(kb / 102.4) / 10} MB)`;
}
async function bewaarSelectie(sel) { S.data.project.selectie = [...sel]; await api("/api/toetsen/" + S.pid, { method: "PUT", body: { selectie: S.data.project.selectie } }); tekenSelectie(true); }
function vink(pad, aan) { pad = decodeURIComponent(pad); const s = new Set(S.data.project.selectie); aan ? s.add(pad) : s.delete(pad); bewaarSelectie(s); }
function kiesAlles(aan) { const f = S.filter.sel, s = new Set(S.data.project.selectie); S.bronnen.filter((b) => past(b, f)).forEach((b) => (aan ? s.add(b.pad) : s.delete(b.pad))); bewaarSelectie(s); }

// ---------- toetsmatrijs ----------
let bewaarTimer = null;
function matrijsGewijzigd() { clearTimeout(bewaarTimer); bewaarTimer = setTimeout(async () => { const r = await api(`/api/toetsen/${S.pid}/matrijs`, { method: "PUT", body: S.data.matrijs }); S.data.matrijs_afwijkingen = r.afwijkingen || []; $("mBewaard").textContent = "wijzigingen opgeslagen"; tekenAfwijkingen(); }, 500); tekenVerdeling(); }
function tekenAfwijkingen() { const el = $("mAfwijking"); if (!el) return; const a = S.data.matrijs_afwijkingen || []; el.innerHTML = a.length ? `<div class="kaart openpunten"><h3>Wijkt af van de toetsopdracht</h3><ul class="lijstje">${a.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""; }
function cel(i, k, el) { let v = el.innerText.trim(); if (k === "punten" || k === "opgave") v = parseInt(v, 10) || 0; if (k === "p_verwacht") { v = parseFloat(v.replace(",", ".")); v = isNaN(v) ? null : v > 1 ? v / 100 : v; } S.data.matrijs.rijen[i][k] = v; matrijsGewijzigd(); }
function rijWeg(i) { S.data.matrijs.rijen.splice(i, 1); S.data.matrijs.rijen.forEach((r, k) => (r.nr = k + 1)); tekenMatrijs(); matrijsGewijzigd(); }
function rijErbij() { const R = S.data.matrijs.rijen, l = R[R.length - 1] || {}; R.push({ nr: R.length + 1, onderdeel: l.onderdeel || "", opgave: l.opgave || 1, leerdoel: "", eindterm: "", werkwoord: "Bereken", bloom: "Toepassen", koppeling: "", vraagmodel: "", p_verwacht: null, thema: l.thema || "", bron: "–", punten: 2, label: l.label }); tekenMatrijs(); matrijsGewijzigd(); }
function verdeling() {
  const m = S.data.matrijs, R = m.rijen, tot = R.reduce((a, r) => a + (+r.punten || 0), 0), g = m.gevraagd || {}, min = (m.meta && m.meta.minuten) || 100;
  const groep = (k, namen, gv) => namen.map((n) => { const p = R.filter((r) => r[k] === n).reduce((a, r) => a + (+r.punten || 0), 0); return { n, p, pct: tot ? Math.round((p / tot) * 100) : 0, gv: gv && gv[n] }; });
  return { tot, min, vragen: R.length, ond: groep("onderdeel", [...new Set(R.map((r) => r.onderdeel).filter(Boolean))], g.onderdelen), bloom: groep("bloom", BLOOM.filter((b) => R.some((r) => r.bloom === b) || (g.bloom && g.bloom[b] != null)), g.bloom),
    label: (m.meta && m.meta.tweefasen) ? [["H+V", R.filter((r) => r.label !== "V")], ["alleen V", R.filter((r) => r.label === "V")]].map(([n, l]) => ({ n, p: l.reduce((a, r) => a + (+r.punten || 0), 0) })) : null };
}
function tekenVerdeling() {
  const v = verdeling(), tempo = v.tot ? v.min / v.tot : 0;
  const balk = (x) => `<div class="balkrij"><div>${esc(x.n)}<div class="balk"><i style="width:${Math.min(100, x.pct)}%"></i></div></div><div class="n ${x.gv != null && Math.abs(x.pct - x.gv) > 5 ? "afwijking" : ""}">${x.pct}%</div><div class="n sub">${x.gv != null ? x.gv + "%" : ""}</div></div>`;
  $("mVerdeling").innerHTML = `<div class="kaart"><h3>Omvang</h3><div class="groot">${v.tot} punten</div><div>${v.vragen} vragen · ${v.min} minuten</div><div class="${tempo && (tempo < 2.4 || tempo > 3.6) ? "afwijking" : "sub"}">${tempo.toFixed(1).replace(".", ",")} minuut per punt (examen: 3,1 à 3,2)</div>${v.label ? `<div class="sub">${v.label.map((l) => `${l.n}: ${l.p} punten`).join(" · ")}</div>` : ""}</div>
    <div class="kaart"><h3>Onderdelen <span class="sub" style="float:right;text-transform:none">nu · gevraagd</span></h3>${v.ond.map(balk).join("") || `<span class="sub">geen onderdelen</span>`}</div>
    <div class="kaart"><h3>Bloom <span class="sub" style="float:right;text-transform:none">nu · gevraagd</span></h3>${v.bloom.map(balk).join("")}</div>`;
}
function tekenMatrijs() {
  const m = S.data.matrijs, el = $("tab-matrijs"), p = S.data.project;
  if (!m) { el.innerHTML = `<div class="leeg">Er is nog geen toetsmatrijs. Vertel in het gesprek wat u wilt toetsen en beantwoord de intakevragen. Claude zet de matrijs daarna hier neer.</div>`; return; }
  const twee = m.meta && m.meta.tweefasen, vast = p.status === "goedgekeurd" || p.status === "gebouwd", ed = vast ? "" : 'contenteditable="true"';
  const td = (i, k, v, kl = "") => `<td class="${kl}" ${ed} onblur="cel(${i},'${k}',this)">${esc(v)}</td>`;
  const lijst = (t, a) => (a && a.length ? `<div class="kaart"><h3>${t}</h3><ul class="lijstje">${a.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : "");
  el.innerHTML = `<div id="mAfwijking"></div><div class="kaarten" id="mVerdeling"></div>
    <table class="matrijs"><tr><th>Nr</th><th>Opg.</th><th>Onderdeel</th><th>Leerdoel</th><th>Eindterm</th>${twee ? "<th>Label</th>" : ""}<th>Werkwoord</th><th>Bloom</th><th>Ankervraag (eindexamen)</th><th>Model</th><th class="n" title="Verwachte p-waarde: aandeel van de punten dat de klas naar verwachting haalt">p verw.</th><th>Thema</th><th class="n">P</th><th></th></tr>
    ${m.rijen.map((r, i) => `<tr><td>${r.nr}</td>${td(i, "opgave", r.opgave)}${td(i, "onderdeel", r.onderdeel)}${td(i, "leerdoel", r.leerdoel)}${td(i, "eindterm", r.eindterm)}
      ${twee ? `<td><select ${vast ? "disabled" : ""} onchange="S.data.matrijs.rijen[${i}].label=this.value;matrijsGewijzigd()"><option value="HV" ${r.label !== "V" ? "selected" : ""}>H+V</option><option value="V" ${r.label === "V" ? "selected" : ""}>V</option></select></td>` : ""}
      ${td(i, "werkwoord", r.werkwoord)}<td><select ${vast ? "disabled" : ""} onchange="S.data.matrijs.rijen[${i}].bloom=this.value;matrijsGewijzigd()">${BLOOM.map((b) => `<option ${r.bloom === b ? "selected" : ""}>${b}</option>`).join("")}</select></td>
      ${td(i, "koppeling", r.koppeling)}${td(i, "vraagmodel", r.vraagmodel || "")}${td(i, "p_verwacht", r.p_verwacht == null ? "" : String(r.p_verwacht).replace(".", ","), "n")}${td(i, "thema", r.thema)}${td(i, "punten", r.punten, "n")}<td>${vast ? "" : `<span class="link" title="Rij verwijderen" onclick="rijWeg(${i})">×</span>`}</td></tr>`).join("")}</table>
    <div class="actiebalk">${vast ? `<span class="sub">De matrijs is goedgekeurd op ${esc(p.goedgekeurd_op || "")}.</span> <button class="licht" onclick="heropen()">Matrijs heropenen</button>` : `<button class="licht" onclick="rijErbij()">+ Rij</button><button onclick="keurGoed()">Matrijs goedkeuren en toets bouwen</button><span class="sub" id="mBewaard">Klik in een cel om te wijzigen. Grotere wijzigingen vraagt u in het gesprek.</span>`}</div>
    <div class="kaarten">${lijst("Vaste onderdelen", m.vaste_onderdelen)}${lijst("Keuzes van Claude", m.keuzes)}${lijst("Nog open", m.open_vragen)}</div>`;
  tekenVerdeling(); tekenAfwijkingen();
}
async function keurGoed() {
  if (S.bezig) return alert("Claude is nog bezig.");
  if (S.data.opdracht && !S.data.project.opdracht_goedgekeurd_op) return alert("Keur eerst de toetsopdracht goed (tabblad Toetsopdracht)."); const open = (S.data.matrijs.open_vragen || []).length;
  const afw = (S.data.matrijs_afwijkingen || []).length;
  if (!confirm(`De matrijs goedkeuren en de toets laten bouwen?${open ? `\n\nLet op: er staan nog ${open} open vragen.` : ""}${afw ? `\n\nLet op: de matrijs wijkt op ${afw} punten af van de toetsopdracht.` : ""}\n\nHet bouwen duurt enkele minuten en telt mee in het verbruik van uw abonnement.`)) return;
  clearTimeout(bewaarTimer); await api(`/api/toetsen/${S.pid}/matrijs`, { method: "PUT", body: S.data.matrijs });
  verstuur($("tekst").value.trim(), "goedkeuren", "Matrijs goedgekeurd. Bouw de toets."); $("tekst").value = "";
}
async function heropen() { await api("/api/toetsen/" + S.pid, { method: "PUT", body: { status: "matrijs" } }); openToets(S.pid, true); }

// ---------- toetsopdracht ----------
function standaardTab(st) { return st === "intake" ? "selectie" : st === "opdracht" ? "opdracht" : ["gebouwd", "geevalueerd"].includes(st) ? "bestanden" : "matrijs"; }
function tekenOpdracht() {
  const el = $("tab-opdracht"), d = S.data, p = d.project, t = d.opdracht;
  if (!t) { el.innerHTML = `<div class="leeg">Er is nog geen toetsopdracht. Start de intake in het gesprek, of plak daar een toetsprompt die u al hebt. Claude maakt er een volledige toetsopdracht van en zet hier wat nog open is. Pas als niets meer open staat, kunt u de opdracht goedkeuren en wordt de matrijs gemaakt.</div>`; return; }
  const open = d.opdracht_open || [], kf = d.kern_fouten || [], kw = d.kern_waarschuwingen || [], vast = !["intake", "opdracht"].includes(p.status);
  const k = d.kern, kernKaart = kf.length
    ? `<div class="kaart openpunten"><h3>Rekenbare kern klopt niet</h3><ul class="lijstje">${kf.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><div class="sub">Vraag Claude dit te herstellen, of pas het blok kern zelf aan.</div></div>`
    : k ? `<div class="kaart"><h3>Rekenbare kern</h3><div class="groot">${k.totaal_punten} punten</div><div>${k.minuten} minuten · ${String(Math.round((k.minuten / k.totaal_punten) * 100) / 100).replace(".", ",")} min per punt${k.vragen ? ` · ${k.vragen} vragen` : ""}</div><div class="sub">${Object.entries(k.onderdelen || {}).map(([a, b]) => `${esc(a)} ${b}`).join(" · ")}</div>${kw.length ? `<ul class="lijstje sub">${kw.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>` : "";
  const kaart = open.length
    ? `<div class="kaart openpunten"><h3>Nog ${open.length} open ${open.length === 1 ? "punt" : "punten"}</h3><ul class="lijstje">${open.map((x) => `<li>${md(x).replace(/^<p>|<\/p>$/g, "")}</li>`).join("")}</ul><div class="sub">Beantwoord ze in het gesprek, of pas de opdracht zelf aan. Daarna kunt u goedkeuren.</div></div>`
    : `<div class="kaart"><h3>Geen open punten</h3><div class="sub">${vast ? `Goedgekeurd op ${esc(p.opdracht_goedgekeurd_op || "")}.` : "De opdracht is volledig. Lees hem na en keur hem goed."}</div></div>`;
  const knoppen = S.bewerk ? `<button onclick="bewaarOpdracht()">Opslaan</button><button class="licht" onclick="S.bewerk=false;tekenOpdracht()">Annuleren</button>`
    : vast ? `<button class="licht" onclick="heropenOpdracht()">Opdracht heropenen</button><span class="sub">De opdracht is de basis van de matrijs en de toets.</span>`
    : `<button class="licht" onclick="S.bewerk=true;tekenOpdracht()">Zelf bewerken</button><button ${open.length || kf.length ? "disabled title=\"Eerst alle open punten oplossen en de kern laten kloppen\"" : ""} onclick="keurOpdrachtGoed()">Opdracht goedkeuren en matrijs laten maken</button>`;
  el.innerHTML = `<div class="kaarten">${kaart}${kernKaart}</div><div class="actiebalk">${knoppen}</div>` + (S.bewerk ? `<textarea id="opdrTekst" class="opdrtekst">${esc(t)}</textarea>` : `<div class="opdracht">${md(t)}</div>`);
}
async function bewaarOpdracht() { const t = $("opdrTekst").value; const r = await api(`/api/toetsen/${S.pid}/opdracht`, { method: "PUT", body: { tekst: t } }); S.data.opdracht = t; S.data.opdracht_open = r.open; S.data.kern = r.kern; S.data.kern_fouten = r.kern_fouten; S.data.kern_waarschuwingen = r.kern_waarschuwingen; S.bewerk = false; tekenOpdracht(); }
function keurOpdrachtGoed() {
  if (S.bezig) return alert("Claude is nog bezig.");
  if ((S.data.opdracht_open || []).length) return alert("Er staan nog open punten in de toetsopdracht.");
  if ((S.data.kern_fouten || []).length) return alert("De rekenbare kern klopt nog niet.");
  if (!confirm("De toetsopdracht goedkeuren? Claude maakt daarna de toetsmatrijs op basis van deze opdracht.")) return;
  verstuur($("tekst").value.trim(), "opdracht_ok", "Toetsopdracht goedgekeurd. Maak de toetsmatrijs."); $("tekst").value = "";
}
async function heropenOpdracht() { if (!confirm("De toetsopdracht heropenen? Daarna moeten de opdracht en de matrijs opnieuw worden goedgekeurd.")) return; await api("/api/toetsen/" + S.pid, { method: "PUT", body: { status: "opdracht" } }); S.tab = "opdracht"; openToets(S.pid, true); }
function vulIn(t) { const v = $("tekst"); v.value = t; v.focus(); v.setSelectionRange(t.length, t.length); }

// ---------- toetsbestanden ----------
function tekenBestanden() {
  const d = S.data, el = $("tab-bestanden");
  if (!d.bestanden.length) { el.innerHTML = `<div class="leeg">Er zijn nog geen toetsbestanden. Ze verschijnen hier nadat u de toetsmatrijs hebt goedgekeurd.</div>`; return; }
  const v = d.verslagen.map((x) => `<div class="kaart"><h3>Versie ${esc(x.versie)}</h3><div class="groot">${x.punten} punten</div><div>${x.vragen} vragen · ${x.woorden} woorden leeslast</div><div class="sub">${String(x.minuten_per_punt).replace(".", ",")} minuut per punt</div></div>`).join("");
  el.innerHTML = `<div class="kaarten">${v}</div><table><tr><th>Bestand</th><th class="n">Grootte</th><th class="n">Gemaakt</th><th></th></tr>${d.bestanden.map((b) => `<tr class="klik"><td class="naam" onclick="lees('${encodeURIComponent("toetsen/" + S.pid + "/uit/" + b.naam)}')">${esc(b.naam)}</td><td class="n">${b.kb} kB</td><td class="n">${b.gewijzigd}</td><td class="n"><a class="link" href="/api/toetsen/${S.pid}/download?bestand=${encodeURIComponent(b.naam)}">download</a></td></tr>`).join("")}</table>
    <div class="actiebalk"><a href="/api/toetsen/${S.pid}/zip"><button>Alles downloaden (zip)</button></a><button class="licht" onclick="rondAf()">Afronden: kennisbank bijwerken</button><button class="licht" onclick="maakKopie()">Kopie voor herkansing of nieuw jaar</button><span class="sub">Klik op een bestand om het te lezen. De opmaak ziet u pas goed in Word.</span></div>
    <h2 class="sectie">Na de afname</h2><div id="naAfname"></div>`;
  tekenNaAfname();
}
function rondAf() { if (S.bezig) return alert("Claude is nog bezig."); if (confirm("De toets is goedgekeurd en de kennisbank mag worden bijgewerkt?")) verstuur($("tekst").value.trim(), "afronden", "Afronden: werk de kennisbank bij."); }

// ---------- na de afname ----------
async function tekenNaAfname() {
  const el = $("naAfname"); if (!el) return; const d = S.data, ia = d.itemanalyses || {};
  let lijst = { toetsen: [], gevonden: false };
  try { lijst = await api("/api/evaluatie/toetsen"); } catch (e) {}
  const opties = lijst.toetsen.map((t) => `<option value="${esc(t.toets_id)}">${esc(t.toets_id)} · ${esc(t.datum || "")} · ${t.leerlingen} lln</option>`).join("");
  const versies = (d.matrijs && d.matrijs.meta && d.matrijs.meta.versies) || ["A", "B"];
  const vSel = `<select id="iaVersie">${versies.map((v) => `<option>${esc(v)}</option>`).join("")}</select>`;
  const tabel = Object.entries(ia).map(([v, a]) => `<div class="kaart breed"><h3>Versie ${esc(v)} · ${esc(a.toets_id || a.bron)} · ${a.n} leerlingen</h3>
      <div>Gemiddeld ${String(a.gemiddelde).replace(".", ",")} van ${a.max_totaal} · p toets ${String(a.p_toets).replace(".", ",")} · alfa ${a.alfa == null ? "–" : String(a.alfa).replace(".", ",")}</div>
      ${(a.let_op || []).map((x) => `<div class="sub">${esc(x)}</div>`).join("")}
      <table class="ia"><tr><th>Vraag</th><th class="n">Max</th><th class="n">p</th><th class="n">rit</th><th class="n">rir</th><th>Leergaten</th><th>Signalen</th></tr>
      ${a.vragen.map((q) => `<tr class="${q.signalen.length ? "signaal" : ""}"><td>${q.nr}</td><td class="n">${q.max}</td><td class="n">${q.p == null ? "–" : String(q.p).replace(".", ",")}</td><td class="n">${q.rit == null ? "–" : String(q.rit).replace(".", ",")}</td><td class="n">${q.rir == null ? "–" : String(q.rir).replace(".", ",")}</td><td>${Object.entries(q.leergaten || {}).map(([k, n]) => `${k} ${n}`).join(", ")}</td><td>${esc(q.signalen.join("; "))}</td></tr>`).join("")}</table></div>`).join("");
  el.innerHTML = `<p class="uitleg">Haal na het nakijken de scores per vraag op. De app berekent p-waarde, rit, rir en alfa, koppelt ze aan de vragenbank en stelt de kalibratie bij. Daarna evalueert Claude de vragen en stelt leerregels voor.</p>
    <div class="actiebalk">${lijst.gevonden ? `<select id="iaToets"><option value="">Kies de toets in de Toetsevaluatie…</option>${opties}</select> voor versie ${vSel} <button class="licht" onclick="haalScores()">Scores ophalen</button>` : `<span class="sub">De database van de Toetsevaluatie is niet gevonden (${esc(lijst.pad || "")}). Pas het pad aan bij Instellingen, of lees een CSV in.</span> ${vSel}`}
      <label class="link">of CSV inlezen<input type="file" accept=".csv,.txt" hidden onchange="leesCsv(this)"></label></div>
    <div class="kaarten">${tabel}</div>
    ${Object.keys(ia).length ? `<div class="actiebalk"><button onclick="evalueer()">Evalueren door Claude</button><span class="sub">Claude beoordeelt de gemarkeerde vragen, schrijft de evaluatie en stelt kandidaat-leerregels voor.</span></div>` : ""}
    ${d.evaluatie ? `<div class="opdracht">${md(d.evaluatie)}</div>` : ""}`;
}
async function haalScores() {
  const t = $("iaToets").value; if (!t) return alert("Kies eerst de toets.");
  try { await api(`/api/toetsen/${S.pid}/itemanalyse`, { method: "POST", body: { toets_id: t, versie: $("iaVersie").value } }); } catch (e) { return alert(e.message); }
  await openToets(S.pid, true);
}
async function leesCsv(inp) {
  const f = inp.files[0]; if (!f) return; const fd = new FormData(); fd.append("bestand", f); fd.append("versie", $("iaVersie").value);
  const r = await fetch(`/api/toetsen/${S.pid}/itemanalyse`, { method: "POST", body: fd }); if (!r.ok) return alert((await r.text()).replace(/<[^>]+>/g, " ").trim().slice(0, 300));
  await openToets(S.pid, true);
}
function evalueer() { if (S.bezig) return alert("Claude is nog bezig."); verstuur($("tekst").value.trim(), "evalueren", "Evalueer de afname."); $("tekst").value = ""; }
async function maakKopie() {
  const doel = prompt("Waarvoor is de kopie? Typ 'herkansing' of 'nieuw schooljaar'.", "herkansing"); if (!doel) return;
  const naam = prompt("Naam van de nieuwe toets:", `${S.data.project.naam} (${doel})`); if (!naam) return;
  const pr = await api(`/api/toetsen/${S.pid}/kopie`, { method: "POST", body: { doel, naam } }); await laadToetsen(); openToets(pr.id);
}

// ---------- kwaliteit en leren ----------
async function toonKwaliteit() {
  S.pid = null; tekenToetsen(); $("bieb").hidden = true; $("project").hidden = true; $("kwaliteit").hidden = false; $("navBieb").classList.remove("actief"); $("navKwal").classList.add("actief");
  $("tekst").disabled = true; $("stuur").disabled = true; $("spreek").disabled = true; $("snel").innerHTML = "";
  const [rr, vb] = await Promise.all([api("/api/leerregels"), api("/api/vragenbank")]); S.kw = { rr, vb }; tekenKwaliteit();
}
function tekenKwaliteit() {
  const { rr, vb } = S.kw, f = S.kwFilter || "kandidaat", el = $("kwaliteitInhoud");
  const telt = (st) => rr.filter((r) => r.status === st).length;
  const regels = rr.filter((r) => f === "alle" || r.status === f).sort((a, b) => String(b.datum).localeCompare(String(a.datum)));
  const kal = vb.kalibratie, vr = vb.vragen || [];
  const zwak = vr.filter((v) => { const a = (v.afnames || []).slice(-1)[0]; return a && a.signalen && a.signalen.length; });
  el.innerHTML = `<h2 class="sectie">Leerregels</h2>
    <p class="uitleg">Bevestigde leerregels zijn bindend bij elke nieuwe toets. Kandidaten komen uit uw aanpassingen en uit de evaluaties; Claude gebruikt ze pas als u ze bevestigt.</p>
    <div class="filter">${[["kandidaat", "Kandidaten"], ["bevestigd", "Bevestigd"], ["vervallen", "Vervallen"], ["alle", "Alle"]].map(([k, t]) => `<button class="licht ${f === k ? "actief" : ""}" onclick="S.kwFilter='${k}';tekenKwaliteit()">${t} (${k === "alle" ? rr.length : telt(k)})</button>`).join("")}</div>
    <table><tr><th>Datum</th><th>Vak · niveau</th><th>Regel</th><th>Aanleiding</th><th></th></tr>${regels.map((r) => `<tr><td>${esc(r.datum || "")}</td><td>${esc(r.vak || "")} ${esc(r.niveau || "")}${r.onderwerp ? `<div class="sub">${esc(r.onderwerp)}</div>` : ""}</td><td>${esc(r.regel)}${r.bron ? `<div class="sub">bron: ${esc(r.bron)}</div>` : ""}</td><td class="sub">${esc(r.aanleiding || "")}${r.bewijs ? `<div>${esc(Array.isArray(r.bewijs) ? r.bewijs.join("; ") : r.bewijs)}</div>` : ""}</td>
      <td class="knoppenrij">${r.status !== "bevestigd" ? `<button class="licht" onclick="zetRegel(${r._i},'bevestigd')">Bevestig</button>` : ""}${r.status !== "vervallen" ? `<button class="licht" onclick="zetRegel(${r._i},'vervallen')">Vervalt</button>` : ""}<button class="licht" onclick="bewerkRegel(${r._i})">Bewerk</button></td></tr>`).join("") || `<tr><td colspan="5" class="sub">Geen leerregels in deze lijst.</td></tr>`}</table>
    <h2 class="sectie">Kalibratie van de moeilijkheid</h2>
    ${kal && kal.n ? `<p>Op ${kal.n} vragen was de werkelijke p gemiddeld <b>${String(kal.totaal > 0 ? "+" + kal.totaal : kal.totaal).replace(".", ",")}</b> ten opzichte van de verwachting (positief: makkelijker dan verwacht).</p><div class="kaarten">${Object.entries(kal.per_vak_niveau || {}).map(([k, x]) => `<div class="kaart"><h3>${esc(k)}</h3><div class="groot">${String(x.gemiddeld_verschil > 0 ? "+" + x.gemiddeld_verschil : x.gemiddeld_verschil).replace(".", ",")}</div><div class="sub">${x.n} vragen</div></div>`).join("")}</div>` : `<p class="sub">Nog geen afnames met verwachte p-waarden. De kalibratie vult zich na de eerste itemanalyse.</p>`}
    <h2 class="sectie">Vragenbank</h2>
    <p class="uitleg">${vr.length} vragen, waarvan ${vr.filter((v) => (v.afnames || []).length).length} met afnamegegevens en ${zwak.length} met een signaal.</p>
    <input class="zoek" placeholder="Zoek in de vragenbank…" value="${esc(S.kwZoek || "")}" oninput="S.kwZoek=this.value;tekenVragenbank()"><div id="vbLijst"></div>`;
  tekenVragenbank();
}
function tekenVragenbank() {
  const q = (S.kwZoek || "").toLowerCase(), vr = (S.kw.vb.vragen || []).filter((v) => !q || JSON.stringify(v).toLowerCase().includes(q)).slice(-300).reverse();
  $("vbLijst").innerHTML = `<table><tr><th>Toets</th><th>Vraag</th><th>Leerdoel</th><th>Bloom</th><th>Anker</th><th class="n">p verw.</th><th class="n">p</th><th class="n">rit</th><th>Signalen</th></tr>${vr.map((v) => { const a = (v.afnames || []).slice(-1)[0] || {}; return `<tr class="${a.signalen && a.signalen.length ? "signaal" : ""}"><td>${esc(v.at || v.naam || "")} ${esc(v.versie || "")}<div class="sub">${esc(v.klas || v.niveau || "")}</div></td><td>${v.nr} · ${v.punten}p<div class="sub">${esc(v.thema || "")}</div></td><td>${esc(v.leerdoel || "")}</td><td>${esc(v.bloom || "")}</td><td class="sub">${esc(v.koppeling || "")}</td><td class="n">${v.p_verwacht == null ? "–" : String(v.p_verwacht).replace(".", ",")}</td><td class="n">${a.p == null ? "–" : String(a.p).replace(".", ",")}</td><td class="n">${a.rit == null ? "–" : String(a.rit).replace(".", ",")}</td><td class="sub">${esc((a.signalen || []).join("; "))}</td></tr>`; }).join("")}</table>`;
}
async function zetRegel(i, status) { S.kw.rr = await api(`/api/leerregels/${i}`, { method: "POST", body: { status } }); tekenKwaliteit(); }
async function bewerkRegel(i) { const r = S.kw.rr.find((x) => x._i === i); const t = prompt("Leerregel:", r.regel); if (!t) return; S.kw.rr = await api(`/api/leerregels/${i}`, { method: "POST", body: { regel: t } }); tekenKwaliteit(); }

// ---------- gesprek ----------
function md(t) {
  const regels = esc(t).split("\n"); let uit = "", lijst = null, tabel = [];
  const inl = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const sluit = () => { if (lijst) { uit += `</${lijst}>`; lijst = null; } if (tabel.length) { uit += "<table>" + tabel.filter((r) => !/^\|?[\s:|-]+\|?$/.test(r)).map((r, i) => "<tr>" + r.replace(/^\||\|$/g, "").split("|").map((c) => `<${i ? "td" : "th"}>${inl(c.trim())}</${i ? "td" : "th"}>`).join("") + "</tr>").join("") + "</table>"; tabel = []; } };
  regels.forEach((r) => { let m;
    if (/^\s*\|.*\|\s*$/.test(r)) { if (lijst) sluit(); tabel.push(r.trim()); return; }
    if ((m = r.match(/^\s*([-*•]|\d+[.)])\s+(.*)/))) { const soort = /\d/.test(m[1]) ? "ol" : "ul"; if (tabel.length || (lijst && lijst !== soort)) sluit(); if (!lijst) { uit += `<${soort}>`; lijst = soort; } uit += `<li>${inl(m[2])}</li>`; return; }
    sluit(); if ((m = r.match(/^#{1,4}\s+(.*)/))) uit += `<h4>${inl(m[1])}</h4>`; else if (r.trim()) uit += `<p>${inl(r)}</p>`; });
  sluit(); return uit;
}
const actieBlok = (a, open) => (a && a.length ? `<details class="acties" ${open ? "open" : ""}><summary>${a.length} ${a.length === 1 ? "handeling" : "handelingen"} van Claude</summary>${a.map((x) => `<div>${esc(x)}</div>`).join("")}</details>` : "");
function tekenGesprek() {
  const g = S.data.gesprek, el = $("berichten");
  el.innerHTML = g.length ? g.map((b) => (b.rol === "docent" ? `<div class="b docent">${esc(b.tekst)}</div>` : actieBlok(b.acties) + `<div class="b claude">${md(b.tekst || "(geen tekst)")}</div>`)).join("") : `<div class="leegchat">Vertel wat u wilt toetsen: het onderwerp, wat in elk geval terug moet komen, de verdeling en de tijd. Of klik op ‘Start de intake’.</div>`;
  el.scrollTop = el.scrollHeight;
}
function zetInvoer() {
  const open = !!S.pid; $("tekst").disabled = !open || S.bezig; $("stuur").disabled = !open || S.bezig; $("stop").hidden = !S.bezig; $("spreek").disabled = !open || S.bezig; if (!open || S.bezig) stopSpreek();
  $("chatStatus").innerHTML = S.bezig ? `<span class="punt"></span>Claude werkt…` : "";
  const st = S.data && S.data.project.status, snel = [];
  if (open && !S.bezig) {
    if (st === "intake" && !S.data.gesprek.length) snel.push(["Start de intake", "Start de intake voor deze toets. Lees de richtlijnen, de kennisbank en de gekozen bronnen en stel mij daarna alle vragen in één keer."]);
    if (st === "intake" && !S.data.gesprek.length) snel.push(["Ik heb al een toetsprompt", "Hieronder staat mijn toetsprompt. Zet die om in de toetsopdracht volgens het sjabloon, vul aan uit de kennisbank en de gekozen bronnen, en zet alles wat ontbreekt, onduidelijk of tegenstrijdig is onder Open punten.\n\n", "vul"]);
    if (st === "intake" && S.data.gesprek.length) snel.push(["Schrijf de toetsopdracht", "Mijn antwoorden zijn compleet. Schrijf nu de toetsopdracht en zet wat nog onduidelijk is onder Open punten."]);
    if (st === "opdracht") snel.push(["Controleer de opdracht", "Controleer de toetsopdracht als toetsdeskundige met de volledigheidscheck uit het sjabloon: volledig, eenduidig en zonder tegenstrijdigheden? Werk Open punten bij en stel mij die vragen in één bericht, elk met uw voorstel."]);
    if (st === "matrijs") snel.push(["Leg de matrijs uit", "Leg in het kort uit welke keuzes u in de matrijs hebt gemaakt en waarom."]);
    if (st === "gebouwd") snel.push(["Wat moet ik nog controleren?", "Wat moet ik in de gebouwde toets nog zelf controleren?"]);
    if (st === "geevalueerd") snel.push(["Wat leer ik van deze afname?", "Vat in vijf regels samen wat de evaluatie betekent voor de nabespreking, de herkansing en de volgende versie van deze toets."]);
  }
  $("snel").innerHTML = snel.map((s, i) => `<button class="licht" onclick="${s[2] === "vul" ? "vulIn" : "verstuur"}(${JSON.stringify(s[1]).replace(/"/g, "&quot;")})">${s[0]}</button>`).join("");
}
function stuur() { stopSpreek(); const t = $("tekst").value.trim(); if (!t) return; $("tekst").value = ""; verstuur(t); }

// ---------------------------------------------------------------- inspreken (spraak naar tekst in de browser)
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const SP = { aan: false, rec: null, basis: "", vast: "", laatst: "" };
function spreekMeld(t, fout) { const m = $("spreekMeld"); m.hidden = !t; m.textContent = t || ""; m.className = "spreekmeld" + (fout ? " fout" : ""); }
function spreekKnop() { const k = $("spreek"); k.classList.toggle("aan", SP.aan); k.textContent = SP.aan ? "■ Stop met luisteren" : "🎤 Inspreken"; $("tekst").classList.toggle("luistert", SP.aan); }
function stopSpreek() {
  if (!SP.aan) return; SP.aan = false;
  try { SP.rec && SP.rec.stop(); } catch (e) {}
  spreekKnop(); spreekMeld("");
}
function startHerkenning() {
  const el = $("tekst"), r = new SR(); SP.rec = r;
  SP.basis = el.value.trim() ? el.value.replace(/\s*$/, " ") : ""; SP.vast = ""; SP.laatst = "";
  r.lang = "nl-NL"; r.continuous = true; r.interimResults = true;
  r.onresult = (e) => {
    if (!SP.aan) return;
    let tussen = "";
    for (let i = e.resultIndex; i < e.results.length; i++) { const t = e.results[i][0].transcript; if (e.results[i].isFinal) { if (t.trim() && t.trim() !== SP.laatst) SP.vast += t.trim() + " "; SP.laatst = t.trim(); } else tussen += t; }
    el.value = SP.basis + SP.vast + tussen; el.scrollTop = el.scrollHeight;
  };
  r.onerror = (e) => {
    if (e.error === "no-speech" || e.error === "aborted") return;  // stilte: onend start opnieuw
    const uitleg = { "not-allowed": "De browser mag de microfoon niet gebruiken. Sta de microfoon toe voor deze pagina (slotje of 'Instellingen voor deze website' in de adresbalk) en klik opnieuw.",
      "service-not-allowed": "Safari krijgt van macOS geen toegang tot spraakherkenning. Zet Safari aan onder Systeeminstellingen > Privacy en beveiliging > Spraakherkenning, sluit Safari helemaal af (Cmd+Q) en open de app opnieuw. Tot die tijd kunt u dicteren met de Mac zelf: klik in het tekstvak en druk op de microfoontoets (F5).",
      "audio-capture": "Er is geen microfoon gevonden. Controleer welke microfoon is gekozen in Systeeminstellingen > Geluid > Invoer.",
      "network": "De spraakherkenning heeft geen verbinding. Controleer de internetverbinding.",
      "language-not-supported": "Deze browser herkent geen Nederlands. Probeer Chrome of Safari." }[e.error] || ("Inspreken is gestopt (" + e.error + ").");
    const detail = e.message ? " [melding van de browser: " + e.message + "]" : "";
    SP.aan = false; spreekKnop(); spreekMeld(uitleg + detail, true);
  };
  r.onend = () => { if (SP.aan) { try { startHerkenning(); } catch (e) { SP.aan = false; spreekKnop(); spreekMeld("Inspreken is gestopt. Klik opnieuw om verder te gaan.", true); } } };
  r.start();
}
function spreek() {
  if (SP.aan) { stopSpreek(); $("tekst").focus(); return; }
  if (!SR) return spreekMeld("Deze browser kan niet luisteren. Open de app in Chrome of Safari, of gebruik de dicteerfunctie van de Mac: klik in het tekstvak en druk twee keer op de fn-toets.", true);
  SP.aan = true; spreekKnop(); spreekMeld("Ik luister. Spreek uw bericht in; de tekst verschijnt in het vak. Klik op Stop of op Verstuur als u klaar bent.");
  try { startHerkenning(); } catch (e) { SP.aan = false; spreekKnop(); spreekMeld("De microfoon kon niet worden gestart. Probeer het opnieuw.", true); }
}
// Typt of verbetert u tijdens het inspreken, dan gaat het inspreken verder vanaf uw tekst.
$("tekst").addEventListener("input", () => { if (SP.aan) { SP.basis = $("tekst").value; SP.vast = ""; } });
$("tekst").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); stuur(); } });
async function stop() { await api(`/api/toetsen/${S.pid}/stop`, { method: "POST" }); }

async function verstuur(bericht, actie, toon) {
  if (S.bezig || !S.pid) return; const pid = S.pid, el = $("berichten");
  if (!S.data.gesprek.length) el.innerHTML = "";
  el.insertAdjacentHTML("beforeend", `<div class="b docent">${esc(toon ? toon + (bericht ? "\n" + bericht : "") : bericht)}</div><details class="acties" open hidden><summary></summary></details><div class="b claude"><span class="punt"></span></div>`);
  const bel = el.lastElementChild, act = bel.previousElementSibling; let tekst = "", acties = []; S.bezig = true; zetInvoer(); el.scrollTop = el.scrollHeight;
  const beneden = () => { if (el.scrollHeight - el.scrollTop - el.clientHeight < 160) el.scrollTop = el.scrollHeight; };
  try {
    const r = await fetch(`/api/toetsen/${pid}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bericht, actie, toon: toon ? toon + (bericht ? "\n" + bericht : "") : null }) });
    if (!r.ok) throw new Error((await r.text()).replace(/<[^>]+>/g, " ").trim().slice(0, 200));
    const lezer = r.body.getReader(), dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await lezer.read(); if (done) break; buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf("\n\n")) >= 0) {
        const blok = buf.slice(0, i); buf = buf.slice(i + 2); if (!blok.startsWith("data: ")) continue; const ev = JSON.parse(blok.slice(6));
        if (ev.type === "tekst") { tekst += ev.delta; bel.innerHTML = md(tekst); beneden(); }
        if (ev.type === "actie") { acties.push(ev.tekst); act.hidden = false; act.innerHTML = `<summary>${acties.length} ${acties.length === 1 ? "handeling" : "handelingen"} van Claude</summary>` + acties.slice(-6).map((x) => `<div>${esc(x)}</div>`).join(""); if (!tekst) bel.innerHTML = `<span class="punt"></span>${esc(ev.tekst)}`; beneden(); }
        if (ev.type === "fout") { bel.className = "b fout"; bel.textContent = ev.tekst; tekst = ev.tekst; }
        if (ev.type === "verbruik" && ev.vijf_uur != null) { const p = $("pilVerbruik"); p.hidden = false; p.textContent = `Verbruik 5 uur: ${Math.round(ev.vijf_uur * 100)}%`; p.className = "pil " + (ev.vijf_uur > 0.8 ? "fout" : "stil"); }
      }
    }
  } catch (e) { bel.className = "b fout"; bel.textContent = "Er ging iets mis: " + e.message; }
  S.bezig = false;
  if (S.pid === pid) { const tab = S.tab, oud = S.data.project.status; await laadToetsen(); await openToets(pid, true); const st = S.data.project.status; if (st !== oud) { S.tab = standaardTab(st); tekenTab(); } else if (tab === "matrijs" || tab === "bestanden" || tab === "opdracht") tekenTab(); }
  else { await laadToetsen(); }
}

// ---------- instellingen ----------
async function openInstellingen() {
  const s = await api("/api/instellingen");
  $("venster").innerHTML = `<h2>Instellingen</h2>
    <div class="veld"><label>Model</label><select id="iModel">${[["", "standaard van Claude Code"], ["sonnet", "Sonnet (zuiniger, sneller)"], ["opus", "Opus (sterker, meer verbruik)"]].map(([v, t]) => `<option value="${v}" ${s.model === v ? "selected" : ""}>${t}</option>`).join("")}</select></div>
    <p class="uitleg">Externe mappen worden alleen gelezen en niet gekopieerd, bijvoorbeeld uw examenmappen in Google Drive. Eén volledig pad per regel.</p>
    <textarea id="iMappen" rows="5" style="width:100%" placeholder="/Users/GRA/Library/CloudStorage/…/COMPLETE EINDEXAMENS">${esc(s.externe_mappen.join("\n"))}</textarea>
    <div class="veld"><label>Database van de Toetsevaluatie (voor de itemanalyse)</label><input id="iDb" style="width:100%" value="${esc(s.toetsevaluatie_db || "")}"></div>
    <div class="sub">${s.externe_mappen.map((m, i) => `${s.mappen_gevonden[i] ? "✓" : "✗ niet gevonden:"} ${esc(m)}`).join("<br>")}</div>
    <div class="knoppen" style="margin-top:12px"><button class="licht" onclick="sluitModaal()">Sluiten</button><button onclick="bewaarInstellingen()">Opslaan</button></div>`;
  $("modaal").hidden = false;
}
async function bewaarInstellingen() { await api("/api/instellingen", { method: "POST", body: { model: $("iModel").value, externe_mappen: $("iMappen").value.split("\n"), toetsevaluatie_db: $("iDb").value } }); sluitModaal(); await laadBronnen(); if (S.pid) tekenTab(); else if (!$("kwaliteit").hidden) toonKwaliteit(); else tekenBieb(); }
$("modaal").addEventListener("click", (e) => { if (e.target.id === "modaal") sluitModaal(); });
start();
