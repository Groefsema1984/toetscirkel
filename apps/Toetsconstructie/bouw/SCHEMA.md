# Schema van de toetsbestanden

Per toets staan in `toetsen/<id>/` twee soorten JSON: `matrijs.json` (ter goedkeuring) en `toets_A.json` / `toets_B.json` (de volledige toets). De bouwmotor maakt van een toetsbestand de Word-bestanden. Volledige, goedgekeurde voorbeelden staan in `voorbeelden/`; lees het voorbeeld van hetzelfde vak en niveau voordat je schrijft.

## matrijs.json

```json
{
  "meta": { "vak": "Economie", "niveau": "Vwo", "klas": "6 vwo", "at": "AT6.11", "onderwerp": "...", "datum": "22 oktober 2026",
            "weging": "15%", "minuten": 100, "tweefasen": false, "versies": ["A", "B"] },
  "rijen": [
    { "nr": 1, "onderdeel": "Markten (opgave 1 en 2)", "opgave": 1, "leerdoel": "...", "leerdoel_code": "ECO-MARKT-02", "eindterm": "D2.2",
      "werkwoord": "Bereken", "bloom": "Toepassen", "koppeling": "Safetybell (2025-II vr. 2)",
      "thema": "Skigebied Hochtal", "bron": "–", "punten": 3, "label": "HV", "vraagmodel": "VM2", "p_verwacht": 0.55 }
  ],
  "gevraagd": { "onderdelen": { "Markten (opgave 1 en 2)": 65 }, "bloom": { "Onthouden": 10, "Begrijpen": 20, "Toepassen": 30, "Analyseren": 40 } },
  "vaste_onderdelen": ["wat de docent in elk geval terug wil zien"],
  "keuzes": ["keuzes die je zelf hebt gemaakt en die de docent moet kennen"],
  "open_vragen": ["wat nog niet is beantwoord"]
}
```

- `bloom` is één van: Onthouden, Begrijpen, Toepassen, Analyseren, Evalueren, Creëren.
- `label` alleen bij tweefasen-vwo: "HV" (telt in havo- en vwo-cijfer) of "V" (alleen vwo).
- `leerdoel_code` is de vaste code uit het leerdoelenregister (`python3 bouw/leerdoelen.py --vak <vak> --niveau <niveau>`). Kies de code waarvan de omschrijving precies dit leerdoel dekt, ook als de context anders is. Past geen code, of is er nog geen register, laat het veld dan weg: de evaluatie koppelt later. Verzin nooit een code. De codes komen in het correctievoorschrift (regel LEERDOELCODES) en worden bij het nakijken overgenomen.
- `koppeling` is de nagezochte ankervraag uit een eindexamen (jaar, tijdvak, vraagnummer), of een streepje.
- `vraagmodel` is het VM-nummer uit kopje 8 van de toetsopdracht (leeg als de vraag niet bij een vast onderdeel hoort).
- `p_verwacht` is de verwachte p-waarde (0 tot 1): het aandeel van de punten dat de klas naar verwachting haalt, bijgesteld met `kennisbank/kalibratie.json`. Na de afname vergelijkt de app dit met de werkelijke p.
- `onderdeel` moet precies overeenkomen met een naam in `onderdelen` van de rekenbare kern in de toetsopdracht; de app vergelijkt de punten per onderdeel, per Bloom-niveau en per label met de kern.
- `onderdeel` is de naam waarmee de verdeling wordt berekend; gebruik precies dezelfde tekst als in `gevraagd.onderdelen`.
- De app rekent punten, aandelen, Bloom-verdeling en tempo zelf uit. Zet die niet in het bestand.

## toets_A.json

```json
{
  "meta": { "vak": "Economie", "niveau": "Vwo", "klas": "6 vwo", "titel": "Schoolexamen economie vwo", "onderwerp": "...", "kort": "korte naam voor de voettekst", "soortnaam": "schoolexamen | oefentoets | proefwerk (bepaalt de vaste zinnen)",
            "at": "AT6.11", "datum": "22 oktober 2026", "maker": "GRA", "weging": "15%", "minuten": 100, "versie": "A",
            "tweefasen": false, "bestandsbasis": "AT6.11_SE_vwo6_economie_versie_A" },
  "opgaven": [ { "titel": "Opgave 1  Skigebied", "doorlopend": false, "items": [ ... ] } ],
  "bijlage": { "intro": "...", "bronnen": [ ... ], "formules": [ ... ] },
  "uitwerkbijlage": [ ... ],
  "cv": [ ... ],
  "matrijs": { "rijen": [ ... zelfde rijen als matrijs.json ... ], "gevraagd": { ... } }
}
```

Optioneel in `meta`: `wegingtekst` (hele zin), `hulpmiddelen`, `afronding`, `formuletekst`, `bronnaam`, `letop` (lijst van [kop, tekst]), `logo`.
`bijlage` en `uitwerkbijlage` laat je weg als ze er niet zijn. Punten en aantal vragen telt de bouwmotor zelf.

### items in een opgave

| t | velden | betekenis |
|---|---|---|
| `p` | `text` | contextalinea |
| `li` | `text` (begint met "−  ") | opsommingsregel in de context |
| `use` | `text` | regel boven de vraag: "Gebruik bron 1." (economie havo en 5 vwo) |
| `q` | `nr`, `p`, `text`, `extra` (lijst regels, optioneel) | vraag met punten |
| `tab` | `titel`, `rows`, `head`, `widths`, `align`, `kopkolom`, `legenda` | tabel of pay-offmatrix in de opgave zelf (6 vwo) |
| `fig` | `titel`, `file`, `w`, `h`, `legenda` | figuur in de opgave zelf (6 vwo) |

Een cel met `\n` wordt meerdere regels. De totale breedte van `widths` is hoogstens 8504 in een opgave en 9638 in de bijlage.

### bronnen in de bijlage

`{ "kop": "Opgave 1  Titel", "nieuwePagina": false }` zet een tussenkop (economie). Een bron heeft `nr`, `titel`, `type` en:

- `table`: `head`, `rows`, `widths`, `align` (l, r of c per kolom)
- `figure`: `file`, `w`, `h`
- `box`: `lines`, een lijst van `["b", "kopje"]`, `["p", "alinea"]` of `["-", "opsommingspunt"]`
- `article`: `lines`, een lijst van alinea's (een regel die met * begint wordt een voetnoot)
- `text`: `lines`, elke regel in een eigen kader

Optioneel bij elke bron: `note` (kleine bronvermelding), `legenda`, `nieuwePagina`.
`formules` is een lijst van `["b", "kopje"]`, `["p", "formule"]` en `["k", "verklaring van een symbool"]` en komt als formuleblad op de laatste pagina.

### uitwerkbijlage

Elke vraag krijgt een eigen pagina: `{ "vraag": 10, "titel": "...", "head": [...], "rows": [[...]], "widths": [...], "hoogte": 1300, "noot": "..." }` voor een invultabel, of `{ "vraag": 8, "titel": "...", "file": "markt_A.png", "w": 520, "h": 390, "legenda": ["= accijnsopbrengst", "= welvaartsverlies"] }` voor een figuur om in te arceren.

### cv (correctievoorschrift en nabespreking)

`{ "opgave": "Opgave 1  Skigebied" }` opent een opgave; de titel is exact gelijk aan die in `opgaven`. Daarna per vraag:

```json
{ "nr": 1, "max": 3, "bloom": "Toepassen", "label": "HV",
  "intro": "Een voorbeeld van een juiste berekening is:",
  "bullets": [["stap met uitkomst", 1], ["stap met uitkomst", 1], ["eindantwoord met eenheid", 1]],
  "opm": "optionele opmerking voor de corrector", "fout": "veelgemaakte fout",
  "voorbeeld": "voorbeeldantwoord in leerlingtaal", "uitleg": "korte uitleg voor de nabespreking" }
```

- De deelscores in `bullets` tellen op tot `max`. Uitzonderingen: regels die met "indien" beginnen (de hoogste is dan `max`) en één regel "Per gemaakte fout 1 scorepunt in mindering brengen." met de maximumscore.
- `plain` (losse regels) of `list` (streepjeslijst) komen vóór de deelscores, bijvoorbeeld bij een invulvraag.
- `tabel` (`head`, `rows`, `widths`, `align`) toont een ingevulde uitwerkbijlage; `fig` (`file`, `w`, `h`) toont een gearceerde figuur.
- `label` alleen bij tweefasen.

## Bouwen

```bash
python3 bouw/figuren.py toetsen/<id>/figuren.json        # alleen als er figuren zijn
bash bouw/bouw.sh toetsen/<id>/toets_A.json               # controle, Word-bestanden in toetsen/<id>/uit/, 'lees verder' in de voettekst
```

`bouw.sh` stopt als de controle fouten vindt. Los ze op in het JSON-bestand en bouw opnieuw. Het bouwverslag staat in `toetsen/<id>/uit/bouwverslag_A.json`.
