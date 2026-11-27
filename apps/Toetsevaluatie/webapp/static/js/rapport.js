/* Klassenrapportage: grafieken met Chart.js. De cijfers komen kant-en-klaar uit Python. */
(function () {
  'use strict';
  const D = JSON.parse(document.getElementById('grafiekdata').textContent);

  /* Knop 'advies (opnieuw) maken' */
  for (const knop of document.querySelectorAll('[data-advies]')) {
    knop.addEventListener('click', async () => {
      knop.disabled = true; knop.textContent = 'Claude schrijft het advies (ongeveer een minuut)';
      const fout = document.getElementById('adviesfout');
      try {
        const r = await fetch('/api/klasadvies', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                 body: JSON.stringify({ toets: window.TOETS }) });
        const d = await r.json();
        if (d.ok) { location.reload(); return; }
        fout.textContent = d.fout || 'Het advies is niet gelukt.';
      } catch (e) { fout.textContent = 'De app reageert niet.'; }
      fout.hidden = false; knop.disabled = false; knop.textContent = 'Opnieuw proberen';
    });
  }

  /* Leerdoelen ophalen voor een toets die al geanalyseerd is */
  const ldKnop = document.getElementById('leerdoelen-knop');
  if (ldKnop) ldKnop.addEventListener('click', async () => {
    ldKnop.disabled = true; ldKnop.textContent = 'Claude leest het correctievoorschrift (ongeveer een halve minuut)';
    const fout = document.getElementById('leerdoelen-fout');
    try {
      const r = await fetch('/api/leerdoelen', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                               body: JSON.stringify({ toets: window.TOETS }) });
      const d = await r.json();
      if (d.ok) { location.reload(); return; }
      fout.textContent = d.fout || 'Ophalen is niet gelukt.';
    } catch (e) { fout.textContent = 'De app reageert niet.'; }
    fout.hidden = false; ldKnop.disabled = false; ldKnop.textContent = 'Opnieuw proberen';
  });

  /* Normering: voorvertoning van de N-term, opslaan pas na een klik. */
  (function () {
    const veld = document.getElementById('n-term');
    if (!veld) return;
    const havo = document.getElementById('n-havo');
    const knop = document.getElementById('n-opslaan'), uit = document.getElementById('n-voorbeeld');
    const fout = document.getElementById('n-fout');
    const begin = { n: veld.value, h: havo ? havo.value : '' };
    const getal = (x) => Number(String(x).replace(',', '.'));
    const nl1 = (x) => (x === null || x === undefined || isNaN(x)) ? '-' : (Math.round(x * 10) / 10).toFixed(1).replace('.', ',');
    function cijfer(s, l, n) {                       /* zelfde regel als analyse.cijfer() */
      if (!l) return null;
      let c = 9 * s / l + n;
      if (n > 1) c = Math.min(c, 1 + s * 9 / l * 2, 10 - (l - s) * 9 / l * 0.5);
      else if (n < 1) c = Math.max(c, 1 + s * 9 / l * 0.5, 10 - (l - s) * 9 / l * 2);
      return Math.floor(c * 10 + 0.5 + 1e-9) / 10;
    }
    function samenvatting(n, havoKant) {
      const c = D.scores.map((r) => havoKant ? cijfer(r[2], r[3], n) : cijfer(r[0], r[1], n)).filter((x) => x !== null);
      const gem = c.reduce((a, b) => a + b, 0) / (c.length || 1);
      const max = havoKant ? (D.scores[0] || [])[3] : (D.scores[0] || [])[1];
      let ces = null;
      for (let s = 0; s <= Math.floor(max || 0); s++) { if (cijfer(s, max, n) >= 5.5) { ces = s; break; } }
      return { gem, onv: c.filter((x) => x < 5.5).length, laag: Math.min(...c), hoog: Math.max(...c), ces, max, aantal: c.length };
    }
    function zin(n, havoKant, naam) {
      const nu = samenvatting(getal(havoKant ? begin.h : begin.n), havoKant), nw = samenvatting(n, havoKant);
      const verschil = Math.abs(n - getal(havoKant ? begin.h : begin.n)) > 1e-9;
      const was = (a, b) => (verschil ? ` <span class="nu">(nu ${a})</span>` : '');
      return `<b>${naam}:</b> gemiddeld ${nl1(nw.gem)}${was(nl1(nu.gem))}, `
        + `${nw.onv} onvoldoende${nw.onv === 1 ? '' : 's'}${was(nu.onv)}, laagste ${nl1(nw.laag)} en hoogste ${nl1(nw.hoog)}. `
        + `Een 5,5 vanaf ${nw.ces} van de ${nw.max} punten${was(nu.ces)}.`;
    }
    function toon() {
      fout.hidden = true;
      const n = getal(veld.value), h = havo ? getal(havo.value) : null;
      const geldig = (x) => !isNaN(x) && x >= 0 && x <= 3;
      if (!geldig(n) || (havo && !geldig(h))) { uit.textContent = 'Vul een N-term tussen 0,0 en 3,0 in.'; knop.disabled = true; return; }
      uit.innerHTML = havo ? zin(n, false, 'Vwo-cijfer') + '<br>' + zin(h, true, 'Havo-cijfer') : zin(n, false, 'Met deze N-term');
      knop.disabled = veld.value === begin.n && (!havo || havo.value === begin.h);
    }
    veld.addEventListener('input', toon);
    if (havo) havo.addEventListener('input', toon);
    document.getElementById('n-reset').addEventListener('click', () => { veld.value = '1.0'; if (havo) havo.value = '1.0'; toon(); });
    knop.addEventListener('click', async () => {
      knop.disabled = true;
      try {
        const r = await fetch('/api/normering', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ toets: window.TOETS, n: veld.value, n_havo: havo ? havo.value : null }) });
        const d = await r.json();
        if (d.ok) { location.reload(); return; }
        fout.textContent = d.fout || 'Opslaan is niet gelukt.';
      } catch (e) { fout.textContent = 'De app reageert niet.'; }
      fout.hidden = false; knop.disabled = false;
    });
    if (D.scores.length) toon();
  })();

  if (!window.Chart) { document.getElementById('geen-grafiek').hidden = false; return; }

  const css = getComputedStyle(document.documentElement);
  const kleur = (naam) => css.getPropertyValue(naam).trim();
  const BLAUW = kleur('--grafiek'), ROOD = kleur('--kritiek');
  Chart.defaults.font.family = kleur('--tekst');
  Chart.defaults.font.size = 13;
  Chart.defaults.color = kleur('--inkt-2');
  Chart.defaults.borderColor = kleur('--lijn-zacht');
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation = false;
  const nl = (x, d) => (x === null || x === undefined) ? '-' : Number(x).toFixed(d === undefined ? 0 : d).replace('.', ',');

  /* Eén reeks staven. opties: {max, eenheid, horizontaal, kleuren, titelX, titelY, tip} */
  function staven(id, labels, waarden, o) {
    const doek = document.getElementById(id);
    if (!doek) return;
    o = o || {};
    const waardeAs = { beginAtZero: true, max: o.max, grid: { color: kleur('--lijn-zacht') }, border: { display: false },
                       ticks: { precision: 0, callback: (v) => nl(v) + (o.eenheid || '') },
                       title: { display: !!o.titelWaarde, text: o.titelWaarde } };
    const labelAs = { grid: { display: false }, border: { color: kleur('--lijn') },
                      title: { display: !!o.titelLabel, text: o.titelLabel } };
    new Chart(doek, {
      type: 'bar',
      data: { labels, datasets: [{ data: waarden, backgroundColor: o.kleuren || BLAUW, borderRadius: 4,
                                   borderSkipped: 'start', maxBarThickness: 34, categoryPercentage: .78 }] },
      options: {
        indexAxis: o.horizontaal ? 'y' : 'x',
        scales: o.horizontaal ? { x: waardeAs, y: labelAs } : { x: labelAs, y: waardeAs },
        plugins: { tooltip: { callbacks: { label: (c) => (o.tip ? o.tip(c.dataIndex) : nl(c.parsed[o.horizontaal ? 'x' : 'y'], 1) + (o.eenheid || '')) } } },
      },
    });
  }

  staven('g-verdeling', ['1-2', '2-3', '3-4', '4-5', '5-6', '6-7', '7-8', '8-9', '9-10'], D.verdeling,
         { titelLabel: 'Cijfer', titelWaarde: 'Leerlingen', tip: (i) => D.verdeling[i] + ' leerling(en)' });
  staven('g-domein', D.domein.map((d) => d.naam), D.domein.map((d) => d.pct),
         { max: 100, eenheid: '%', horizontaal: D.domein.length > 4 });
  staven('g-bloom', D.bloom.map((b) => b.naam), D.bloom.map((b) => b.uitval),
         { max: 100, eenheid: '%', tip: (i) => `${nl(D.bloom[i].uitval, 1)}% gemist, ${D.bloom[i].vragen} vraag/vragen` });
  staven('g-leergaten', D.leergaten.map((g) => g.naam), D.leergaten.map((g) => g.punten),
         { titelWaarde: 'Punten', horizontaal: true, tip: (i) => nl(D.leergaten[i].punten, 1) + ' punten verloren' });
  staven('g-vragen', D.vragen.map((v) => String(v.nr)), D.vragen.map((v) => v.p),
         { max: 100, eenheid: '%', titelLabel: 'Vraag', kleuren: D.vragen.map((v) => (v.signaal ? ROOD : BLAUW)),
           tip: (i) => `Vraag ${D.vragen[i].nr}: P = ${nl(D.vragen[i].p / 100, 2)}` + (D.vragen[i].signaal ? ' (signaal, zie tabel)' : '') });
  if (D.tweefasen.length) {
    staven('g-tweefasen', D.tweefasen.map((t) => t.naam), D.tweefasen.map((t) => t.pct),
           { max: 100, eenheid: '%', horizontaal: true });
  }

  const trend = document.getElementById('g-trend');
  if (trend) {
    new Chart(trend, {
      type: 'line',
      data: { labels: D.trend.map((t) => t.label),
              datasets: [{ data: D.trend.map((t) => t.gem), borderColor: BLAUW, backgroundColor: BLAUW, borderWidth: 2, tension: 0,
                           pointRadius: D.trend.map((t) => (t.huidig ? 7 : 4)), pointHoverRadius: 8,
                           pointBorderColor: kleur('--vel'), pointBorderWidth: 2 }] },
      options: {
        scales: { y: { min: 1, max: 10, grid: { color: kleur('--lijn-zacht') }, border: { display: false },
                       title: { display: true, text: 'Gemiddeld cijfer' } },
                  x: { grid: { display: false }, border: { color: kleur('--lijn') } } },
        plugins: { tooltip: { callbacks: { label: (c) => `Gemiddeld ${nl(c.parsed.y, 1)} (${D.trend[c.dataIndex].n} leerlingen)` } } },
      },
    });
  }
})();
