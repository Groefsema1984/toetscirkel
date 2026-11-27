// Oefentool: opdrachtpagina (voortgang volgen, plan aanpassen, opgaven beoordelen).
(function () {
  async function post(url, data) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    const d = await r.json().catch(() => ({ ok: false, fout: 'Geen antwoord van de app.' }));
    if (!d.ok) throw new Error(d.fout || 'Mislukt');
    return d;
  }

  // ---- voortgang volgen: herlaad zodra de stap klaar is
  if (typeof BEZIG !== 'undefined' && BEZIG) {
    let vorige = null;
    const kijk = async () => {
      try {
        const r = await fetch(`/api/opdracht/${OID}/status`);
        const d = await r.json();
        const sleutel = JSON.stringify([d.status, d.bezig, d.in_bewerking, d.fout]);
        if (vorige !== null && sleutel !== vorige) { location.reload(); return; }
        vorige = sleutel;
        const vak = document.getElementById('bezig');
        if (vak && d.voortgang && d.voortgang.totaal) {
          const balk = vak.querySelector('.balk span');
          if (balk) balk.style.width = `${Math.round(100 * d.voortgang.gedaan / d.voortgang.totaal)}%`;
        }
        if (!d.bezig && !(d.in_bewerking || []).length) { location.reload(); return; }
      } catch (e) { /* app even niet bereikbaar: opnieuw proberen */ }
      setTimeout(kijk, 2500);
    };
    setTimeout(kijk, 2500);
  }

  // ---- plan aanpassen
  for (const kaart of document.querySelectorAll('.plankaart')) {
    const code = kaart.dataset.code;
    for (const inp of kaart.querySelectorAll('input[data-stap]')) {
      inp.addEventListener('change', async () => {
        try { await post(`/api/opdracht/${OID}/plan`, { code, stap: inp.dataset.stap, aantal: inp.value }); inp.classList.add('bewaard'); }
        catch (e) { alert(e.message); }
      });
    }
    const weg = kaart.querySelector('[data-weg]');
    if (weg) weg.addEventListener('click', async () => {
      try { await post(`/api/opdracht/${OID}/plan`, { code, weglaten: true }); kaart.remove(); }
      catch (e) { alert(e.message); }
    });
  }

  // ---- opgaven beoordelen
  const reden = document.getElementById('reden'), redenTekst = document.getElementById('reden-tekst');
  const zelf = document.getElementById('zelf');
  function vraagReden(kop) {
    return new Promise((klaar) => {
      document.getElementById('reden-kop').textContent = kop;
      redenTekst.value = '';
      reden.returnValue = '';
      reden.showModal();
      reden.addEventListener('close', () => klaar(reden.returnValue === 'ok' ? redenTekst.value.trim() : null), { once: true });
    });
  }
  for (const kaart of document.querySelectorAll('.opgave')) {
    const uid = kaart.dataset.uid;
    for (const knop of kaart.querySelectorAll('[data-actie]')) {
      knop.addEventListener('click', async () => {
        const actie = knop.dataset.actie;
        try {
          if (actie === 'goed') {
            await post(`/api/opdracht/${OID}/oordeel`, { uid, soort: 'goed' });
            kaart.classList.remove('status-concept'); kaart.classList.add('status-goedgekeurd');
            kaart.querySelector('header').insertAdjacentHTML('beforeend', '<span class="stempel goed">goedgekeurd</span>');
            knop.closest('.keuzes').innerHTML = '<span class="bij">Goedgekeurd.</span>';
            return;
          }
          if (actie === 'zelf') {
            const d = JSON.parse(kaart.querySelector('.itemdata').textContent);
            for (const k of ['titel', 'context', 'vraag', 'voorbeeld', 'uitleg']) document.getElementById('z-' + k).value = d[k] || '';
            zelf.returnValue = '';
            zelf.showModal();
            zelf.addEventListener('close', async () => {
              if (zelf.returnValue !== 'ok') return;
              const nieuw = { uid };
              for (const k of ['titel', 'context', 'vraag', 'voorbeeld', 'uitleg']) nieuw[k] = document.getElementById('z-' + k).value;
              try { await post(`/api/opdracht/${OID}/bewerk`, nieuw); location.reload(); } catch (e) { alert(e.message); }
            }, { once: true });
            return;
          }
          const tekst = await vraagReden(actie === 'afgekeurd' ? 'Waarom keur je deze opgave af?' : 'Wat moet er anders?');
          if (!tekst) return;
          await post(`/api/opdracht/${OID}/oordeel`, { uid, soort: actie, reden: tekst });
          location.reload();
        } catch (e) { alert(e.message); }
      });
    }
  }
})();
