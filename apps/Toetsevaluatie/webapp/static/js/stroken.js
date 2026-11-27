/* Verdeelt elke feedbackstrook over vaste A4-vellen, zodat dubbelzijdig printen altijd klopt:
   - voorkant: kop, onderdelen en leerdoelen; achterkant: plan en reflectie (altijd een nieuw vel);
   - past een kant niet, dan eerst iets kleinere letters, en anders een extra vel;
   - heeft een leerling een oneven aantal vellen, dan komt er een lege pagina bij.
   Zo begint de strook van elke leerling op de voorkant van een nieuw vel. */
(function () {
  'use strict';
  const SCHALEN = [1, 0.95, 0.9, 0.86, 0.82, 0.78, 0.74];   // letters mogen tot 74% krimpen
  const VERVOLG = 0.9;                                // schaal als het toch over meer vellen moet
  const vellen = document.getElementById('vellen');
  const knop = document.getElementById('afdrukken');

  function nieuwVel(schaal) {
    const vel = document.createElement('div');
    vel.className = 'vel';
    const inhoud = document.createElement('div');
    inhoud.className = 'inhoud';
    inhoud.style.fontSize = (10 * schaal).toFixed(2) + 'pt';
    vel.appendChild(inhoud);
    vellen.appendChild(vel);
    return vel;
    }
  const vol = (vel) => vel.scrollHeight > vel.clientHeight + 1;

  /* Zet de blokken achter elkaar op vellen met deze schaal. Geeft de vellen terug. */
  function verdeel(blokken, schaal) {
    const uit = [nieuwVel(schaal)];
    for (const blok of blokken) {
      let vel = uit[uit.length - 1];
      vel.firstChild.appendChild(blok);
      if (vol(vel) && vel.firstChild.children.length > 1) {
        vel.firstChild.removeChild(blok);
        vel = nieuwVel(schaal);
        vel.firstChild.appendChild(blok);
        uit.push(vel);
      }
    }
    return uit;
  }

  /* Liefst één vel (desnoods met kleinere letters); anders meer vellen. */
  function kant(blokken) {
    for (const schaal of SCHALEN) {
      const geprobeerd = verdeel(blokken, schaal);
      if (geprobeerd.length === 1 && !vol(geprobeerd[0])) return geprobeerd;
      geprobeerd.forEach((v) => v.remove());
    }
    return verdeel(blokken, VERVOLG);
  }

  function leegVel() {
    const vel = document.createElement('div');
    vel.className = 'vel leeg';
    vel.textContent = 'Deze kant is bewust leeg gelaten.';
    vellen.appendChild(vel);
  }

  function opmaken() {
    let totaal = 0;
    const strooks = [...document.querySelectorAll('#bronnen .bronstrook')];
    for (const bron of strooks) {
      const kop = document.createElement('p');
      kop.className = 'velkop';
      vellen.appendChild(kop);
      const voor = kant([...bron.querySelector('.bron.voor').children]);
      const achter = kant([...bron.querySelector('.bron.achter').children]);
      let aantal = voor.length + achter.length;
      if (aantal % 2) { leegVel(); aantal += 1; }
      kop.innerHTML = `<b></b> ${aantal} pagina's (${aantal / 2} vel dubbelzijdig)`;
      kop.firstChild.textContent = bron.dataset.naam;
      totaal += aantal;
      bron.remove();
    }
    knop.disabled = false;
    knop.textContent = `Afdrukken (${strooks.length} ${strooks.length === 1 ? 'strook' : 'stroken'}, ${totaal} pagina's)`;
    knop.addEventListener('click', () => window.print());
    document.body.dataset.paginas = String(totaal);
  }

  (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(opmaken);
})();
