/* Validatiepagina: besluiten per vraag, wees-pagina's koppelen, koppelingen controleren. */
(function () {
  'use strict';
  const toets = window.TOETS;
  const venster = document.getElementById('venster');
  const veld = (id) => document.getElementById(id);
  const nl = (x) => String(Math.round(x * 100) / 100).replace('.', ',');

  async function stuur(pad, gegevens) {
    const r = await fetch(pad, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ toets }, gegevens)),
    });
    let d = {};
    try { d = await r.json(); } catch (e) { d = { ok: false, fout: 'Onverwacht antwoord van de app.' }; }
    if (d.herladen) { location.reload(); return new Promise(() => {}); }
    return d;
  }

  function toonTellers(open) {
    if (!open) return;
    for (const el of document.querySelectorAll('[data-tel]')) {
      const n = open[el.dataset.tel];
      if (n === undefined) continue;
      el.textContent = n;
      const vak = el.closest('[data-telvak]');
      if (vak) vak.classList.toggle('nul', !n);
    }
    veld('definitief').disabled = open.totaal > 0;
    veld('vasttekst').textContent = open.totaal
      ? 'Handel alles af; daarna kun je definitief maken.' : 'Alles is afgehandeld.';
  }

  function nieuweLeerling(vak) {
    const w = (n) => (vak.querySelector(`[name=${n}]`) || {}).value || '';
    return { nummer: w('nummer'), naam: w('naam'), klas: w('klas') };
  }

  /* Wees-pagina: 'leerling toevoegen' gekozen? Dan de velden tonen. */
  for (const sel of document.querySelectorAll('select.wees-keuze')) {
    sel.addEventListener('change', () => {
      const vak = sel.closest('[data-wees]').querySelector('.nieuwe-ll');
      const nieuw = sel.value === '__nieuw__';
      if (nieuw) { vak.hidden = false; vak.querySelectorAll('.velden').forEach((v) => { v.hidden = false; }); vak.querySelector('[name=nummer]').focus(); }
      else if (!vak.querySelector('[data-actie=suggestie]')) vak.hidden = true;
    });
  }

  function meld(bij, tekst) {
    let p = bij.querySelector(':scope > .melding.fout.tijdelijk');
    if (!p) { p = document.createElement('p'); p.className = 'melding fout tijdelijk'; bij.appendChild(p); }
    p.textContent = tekst;
  }

  /* Na een besluit: alle weergaven van dezelfde vraag bijwerken (kaart en tabelrij). */
  function werkBij(sectie, vraag, d) {
    for (const el of sectie.querySelectorAll(`[data-kaart][data-vraag="${vraag}"]`)) {
      const eind = el.querySelector('[data-eind]');
      if (eind) eind.textContent = nl(d.eind);
      const code = el.querySelector('[data-code]');
      if (code) code.textContent = d.leergat || '';
      if (!el.hasAttribute('data-rij')) el.classList.toggle('klaar', !!d.besluit);
    }
    let som = 0;
    for (const b of sectie.querySelectorAll('[data-rij] [data-eind]')) som += Number(b.textContent.replace(',', '.')) || 0;
    const totaal = sectie.querySelector('[data-totaal]');
    if (totaal) totaal.textContent = nl(som);
    toonTellers(d.open);
  }

  async function besluit(el, keuze, extra) {
    const sectie = el.closest('[data-leerling]');
    const d = await stuur('/api/besluit', Object.assign(
      { leerling: sectie.dataset.leerling, vraag: Number(el.dataset.vraag), keuze }, extra || {}));
    if (d.ok) werkBij(sectie, el.dataset.vraag, d);
    return d;
  }

  /* Het venster: aangepaste score, reden van afwijken, of alleen een ontbrekend leergat. */
  let huidig = null;
  function openVenster(el, keuze) {
    const max = Number(el.dataset.max), ai = Number(el.dataset.ai);
    const docent = el.dataset.docent === '' ? null : Number(el.dataset.docent);
    huidig = { el, keuze, max };
    veld('v-fout').hidden = true;
    veld('v-reden').value = '';
    veld('v-leergat').value = el.dataset.leergat || '';
    const metScore = keuze === 'aangepast';
    veld('v-scoreveld').hidden = !metScore;
    veld('v-score').max = max;
    veld('v-score').value = metScore ? (docent !== null ? docent : ai) : '';
    const score = keuze === 'docent' ? docent : (keuze === 'claude' ? ai : null);
    veld('v-gatveld').hidden = score !== null && (score >= max || !!el.dataset.leergat);
    veld('v-redenveld').hidden = keuze === 'claude' || keuze === 'akkoord';
    veld('v-titel').textContent = keuze === 'docent' ? 'Waarom wijk je af?'
      : keuze === 'aangepast' ? `Vraag ${el.dataset.vraag}: aangepaste score` : 'Welk leergat hoort hierbij?';
    veld('v-uitleg').textContent = keuze === 'docent'
      ? `Vraag ${el.dataset.vraag}: jij gaf ${nl(docent)}, Claude geeft ${nl(ai)} van de ${max} punten. Jouw score wordt definitief.`
      : keuze === 'aangepast' ? `Maximaal ${max} punten. Bij de volle score hoef je geen leergat te kiezen.`
      : 'Er gaan punten af, maar er staat nog geen leergat bij.';
    venster.showModal();
    (metScore ? veld('v-score') : veld('v-redenveld').hidden ? veld('v-leergat') : veld('v-reden')).focus();
  }
  veld('v-annuleer').addEventListener('click', () => venster.close());
  veld('v-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const extra = { leergat: veld('v-leergat').value, reden: veld('v-reden').value };
    if (huidig.keuze === 'aangepast') extra.score = veld('v-score').value;
    const d = await besluit(huidig.el, huidig.keuze, extra);
    if (d.ok) { venster.close(); return; }
    if (/leergat/i.test(d.fout || '')) veld('v-gatveld').hidden = false;
    veld('v-fout').textContent = d.fout || 'Opslaan is niet gelukt.';
    veld('v-fout').hidden = false;
  });

  document.addEventListener('click', async (ev) => {
    const uit = ev.target.closest('.uitsnede');
    if (uit && !ev.target.closest('button, select')) { uit.classList.toggle('heel'); return; }
    const knop = ev.target.closest('[data-actie]');
    if (!knop) return;
    ev.preventDefault();
    const actie = knop.dataset.actie;

    if (actie === 'besluit') {
      const el = knop.closest('[data-kaart]');
      const keuze = knop.dataset.keuze;
      if (keuze === 'docent' || keuze === 'aangepast') { openVenster(el, keuze); return; }
      const d = await besluit(el, keuze);
      if (!d.ok && /leergat/i.test(d.fout || '')) openVenster(el, keuze);
      else if (!d.ok) meld(el.querySelector('div:nth-child(2)') || el, d.fout);
      return;
    }
    if (actie === 'bulk') {
      knop.disabled = true;
      const d = await stuur('/api/bulk', { soort: knop.dataset.soort, keuze: knop.dataset.keuze });
      if (d.ok) location.reload(); else knop.disabled = false;
      return;
    }
    if (actie === 'suggestie') {
      const vak = knop.closest('.nieuwe-ll');
      vak.querySelector('[name=nummer]').value = knop.dataset.nummer;
      vak.querySelector('[name=naam]').value = knop.dataset.naam;
      if (knop.dataset.klas) vak.querySelector('[name=klas]').value = knop.dataset.klas;
      vak.querySelectorAll('.velden').forEach((v) => { v.hidden = false; });
      const sel = knop.closest('[data-wees]').querySelector('select.wees-keuze');
      sel.value = '__nieuw__';
      return;
    }
    if (actie === 'll-toevoegen') {
      const vak = knop.closest('.nieuwe-ll');
      knop.disabled = true;
      const d = await stuur('/api/leerling-toevoegen', nieuweLeerling(vak));
      if (!d.ok) { knop.disabled = false; meld(vak, d.fout); }
      return;
    }
    if (actie === 'wees-koppel' || actie === 'wees-negeer') {
      const kaart = knop.closest('[data-wees]');
      const lid = actie === 'wees-negeer' ? '__negeer__' : kaart.querySelector('select').value;
      if (!lid) { meld(kaart.lastElementChild, 'Kies eerst een leerling.'); return; }
      knop.disabled = true;
      const extra = lid === '__nieuw__' ? { nieuw: nieuweLeerling(kaart.querySelector('.nieuwe-ll')) } : {};
      const d = await stuur('/api/wees', Object.assign({ wees: kaart.dataset.wees, leerling_id: lid }, extra));
      if (d.ok) { kaart.remove(); toonTellers(d.open); } else { knop.disabled = false; meld(kaart.lastElementChild, d.fout); }
      return;
    }
    if (actie === 'terughalen') {
      const item = knop.closest('[data-weg]');
      const lid = item.querySelector('select').value;
      if (!lid) { meld(item, 'Kies eerst een leerling.'); return; }
      knop.disabled = true;
      const d = await stuur('/api/terughalen', { soort: item.dataset.soort, id: item.dataset.weg, leerling_id: lid });
      if (!d.ok) { knop.disabled = false; meld(item, d.fout); }
      return;
    }
    if (actie === 'koppeling-ok' || actie === 'herkoppel') {
      const item = knop.closest('[data-koppel]');
      if (actie === 'koppeling-ok') {
        const d = await stuur('/api/koppeling_ok', { leerling: item.dataset.koppel });
        if (d.ok) { item.classList.remove('let-op'); knop.remove(); toonTellers(d.open); }
        return;
      }
      const naar = item.querySelector('select').value;
      if (!naar) { meld(item, 'Kies eerst van wie dit werk is.'); return; }
      const d = await stuur('/api/herkoppel', { van: item.dataset.koppel, naar });
      if (!d.ok) meld(item, d.fout);
      return;
    }
    if (actie === 'verdeel') {
      openVerdeel(knop.closest('[data-paginas]'));
      return;
    }
    if (actie === 'leerling') {
      const sectie = knop.closest('[data-leerling]');
      knop.disabled = true;
      const d = await stuur('/api/leerling', { leerling: sectie.dataset.leerling, actie: knop.dataset.wat });
      if (!d.ok) { knop.disabled = false; meld(sectie, d.fout); }
    }
  });
  /* Pagina's verdelen: pagina's van de ene leerling naar een andere (of weglaten). */
  const verdeel = document.getElementById('verdeel');
  let verdeelVan = null;
  function openVerdeel(bron) {
    const paginas = JSON.parse(bron.dataset.paginas);
    verdeelVan = bron.dataset.koppel || bron.dataset.leerling;
    veld('vd-titel').textContent = `Pagina's van ${bron.dataset.naam} verdelen`;
    veld('vd-fout').hidden = true;
    veld('vd-naar').value = '';
    for (const o of veld('vd-naar').options) o.hidden = o.value === verdeelVan;
    const doos = veld('vd-paginas');
    doos.innerHTML = '';
    for (const p of paginas) {
      const label = document.createElement('label');
      label.className = 'mini';
      label.innerHTML = `<a href="/beeld/${encodeURIComponent(toets)}/${p}" target="_blank" rel="noopener" title="Groot bekijken">`
        + `<img src="/beeld/${encodeURIComponent(toets)}/${p}" alt="Pagina ${p + 1}" loading="lazy"></a>`
        + `<span><input type="checkbox" value="${p}"> Pagina ${p + 1}</span>`;
      doos.appendChild(label);
    }
    verdeel.showModal();
  }
  veld('vd-annuleer').addEventListener('click', () => verdeel.close());
  veld('vd-paginas').addEventListener('change', (ev) => {
    const mini = ev.target.closest('.mini');
    if (mini) mini.classList.toggle('gekozen', ev.target.checked);
  });
  veld('vd-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const paginas = [...veld('vd-paginas').querySelectorAll('input:checked')].map((i) => Number(i.value));
    const naar = veld('vd-naar').value;
    const fout = (t) => { veld('vd-fout').textContent = t; veld('vd-fout').hidden = false; };
    if (!paginas.length) return fout("Vink eerst de pagina's aan die je wilt verplaatsen.");
    if (!naar) return fout('Kies bij wie de pagina\'s horen.');
    const d = await stuur('/api/verplaats', { van: verdeelVan, paginas, naar });
    if (!d.ok) fout(d.fout || 'Verplaatsen is niet gelukt.');
  });

  document.addEventListener('keydown', (ev) => {
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.classList.contains('uitsnede')) {
      ev.preventDefault(); ev.target.classList.toggle('heel');
    }
  });

  /* Wordt er nog iemand nagekeken (na het koppelen van een wees)? Ververs zodra dat klaar is. */
  const wachtend = [...document.querySelectorAll('[data-leerling]')]
    .filter((el) => el.dataset.status === 'bezig' || el.dataset.status === 'wacht');
  if (wachtend.length) {
    const peil = async () => {
      try {
        const d = await (await fetch('/api/status/' + encodeURIComponent(toets))).json();
        const bezig = Object.values(d.leerlingen).some((s) => s === 'bezig');
        const veranderd = wachtend.some((el) => d.leerlingen[el.dataset.leerling] !== el.dataset.status);
        if (veranderd && !bezig) { location.reload(); return; }
      } catch (e) { /* opnieuw proberen */ }
      setTimeout(peil, 3000);
    };
    setTimeout(peil, 2000);
  }
})();
