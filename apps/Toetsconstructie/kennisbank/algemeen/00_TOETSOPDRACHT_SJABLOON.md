CHANGELOG
13 november 2026, versie 2. Nieuw: rekenbare kern (de app rekent hem na), kopje "Wat deze toets meet", vraagmodellen met ankervraag per vast onderdeel, moeilijkheidsprofiel met verwachte p-waarde, kopje "Lessen uit eerdere afnames", vaststellingsronde vóór goedkeuring. Onderbouwing: Messick (construct-ondervertegenwoordiging en construct-irrelevante variantie), Haladyna, Downing en Rodriguez (2002, richtlijnen voor vraagconstructie), Downing (2005, gevolgen van constructiefouten), de werkwijze van Cito en CvTE bij het centraal examen, en onderzoek naar AI-vragen (een echte ankervraag verbetert de kwaliteit het meest).
13 november 2026, versie 1. Opgebouwd uit de toetsprompts van AT6.21 bedrijfseconomie 6 vwo en AT5.21 economie 5 vwo.

BRONBESTAND: 00_TOETSOPDRACHT_SJABLOON
FUNCTIE: het vaste format van de toetsopdracht (`toetsopdracht.md` in de projectmap). De toetsopdracht is de volledige, door de docent goedgekeurde beschrijving van de toets. Pas als hij geen open punten meer heeft, de rekenbare kern klopt en de docent hem heeft goedgekeurd, komt er een matrijs en daarna een toets.
VOORBEELDEN: goedgekeurde opdrachten staan in `kennisbank/<vak>/opdrachten/`. Lees vóór het schrijven de opdracht die het meest lijkt op deze toets en neem de scherpte en het detail daarvan over, niet de inhoud.

# HET SJABLOON

Schrijf de opdracht in deze volgorde, met deze kopjes. Schrijf korte, stellige zinnen. Een keuze die de docent heeft gemaakt, krijgt "(akkoord docent)". Een voorstel van jou dat de docent nog niet heeft bevestigd, staat onder Open punten en niet als feit in de tekst.

# Toetsopdracht [AT-nummer] · [soort] [vak] [klas]

Gebaseerd op: [nieuw, of de naam van de eerdere opdracht of toets waarop deze voortbouwt]

## Rekenbare kern
Een blok dat de app zelf narekent. Het moet kloppen voordat de docent kan goedkeuren. Schrijf het precies zo, met geldige JSON:

```kern
{
  "totaal_punten": 34,
  "minuten": 100,
  "vragen": 16,
  "versies": ["A", "B"],
  "tweefasen": false,
  "onderdelen": { "Investeren en financieren": 10, "Resultaat en risico": 7, "Jaarrekening en kengetallen": 11, "Niet-financieel en marketing": 6 },
  "procenten": { "Investeren en financieren": 29.4, "Resultaat en risico": 20.6, "Jaarrekening en kengetallen": 32.4, "Niet-financieel en marketing": 17.6 },
  "bloom": { "Onthouden": 3, "Begrijpen": 7, "Toepassen": 14, "Analyseren": 10 },
  "vraagsoorten": { "rekenen": 15, "uitleg": 15, "kennis": 4 },
  "moeilijkheid": { "makkelijk": 8, "gemiddeld": 18, "moeilijk": 8 }
}
```

- `onderdelen`: punten per onderdeel, met precies dezelfde namen als in de matrijs. Ze tellen op tot `totaal_punten`.
- `procenten`: alleen als de docent de verdeling in procenten heeft opgegeven. De app controleert dat elk percentage in hele punten uitkomt (binnen 0,5 procentpunt). Lukt dat niet, dan is het puntentotaal fout gekozen; leg dan twee totalen voor als open punt.
- `bloom`, `vraagsoorten` en `moeilijkheid` in punten; elk telt op tot het totaal.
- Bij tweefasen: `"tweefasen": true` en `"labels": { "HV": 26, "V": 8 }`.
- Tempo: `minuten` : `totaal_punten` ligt tussen 2,4 en 3,4 minuut per punt (CE: havo 3,2 en vwo 3,1).

## 1. Vaste gegevens
AT-nummer, klas en niveau (tweefasen ja of nee), vak, maker, type (schoolexamen, toets, oefentoets, herkansing), weging in de formulering van het voorblad, tijdsduur, datum van afname, versies (B is de herkansing op dezelfde matrijs), bestandsnamen per versie.

## 2. Doel van de toets
Twee tot vier zinnen: welke lessenreeks of hoofdstukken de toets afsluit, welke beslissing op het cijfer rust (schoolexamen, toetsweek, oefening) en hoe de toets zich verhoudt tot het centraal examen.

## 3. Wat deze toets meet
- Per onderdeel twee of drie kerninzichten of vaardigheden die een volle score aantoont ("de leerling kan van een renteverandering via de kapitaalstromen redeneren naar de wisselkoers").
- **Hoort bij de meting:** wat bewust meetelt, ook als het geen vakinhoud is (bijvoorbeeld de leeslast van het CE, omdat de toets op het examen voorbereidt).
- **Mag niet meewegen:** wat de score niet mag bepalen (bijvoorbeeld rekenvaardigheid buiten de rekenregels, kennis van buiten de lesstof, toetstrucs, lange zinnen zonder functie). Dit is de toets tegen construct-irrelevante moeilijkheid en gemak.

## 4. Bronnen
Genummerd, met per bron waarvoor hij leidend is (stof, begrippen, stijl, eindtermcodes, rekenregels).

## 5. Stof
Per onderdeel of les de concrete leerdoelen, elk met de eindtermcode (syllabus en versie) of "lesstof docent". Bij tweefasen achter elk leerdoel het label H+V of V.
Daaronder: **Niet in deze toets** (stof die buiten valt, met de reden).

## 6. Wat het CE vraagt
Bij een schoolexamen in de bovenbouw: per onderdeel welke vraagtypen het vaakst voorkomen in de examens van het niveau (met de jaren die je hebt gelezen) en ongeveer welk deel van de punten. Bij een andere toets: "n.v.t." met de reden.

## 7. Opbouw, verdeling en moeilijkheid
Totaal punten, aantal vragen en opgaven, tempo. Per opgave: onderwerp, punten en welke vragen er ongeveer in komen ("3p berekening ..., 2p uitleg ..."). Bloom en vraagsoorten in punten.
**Moeilijkheidsprofiel:** elke opgave begint met een instapvraag (verwachte p-waarde 0,75 of hoger) en loopt op. Over de hele toets ongeveer een kwart makkelijk, de helft gemiddeld (p 0,45 tot 0,75) en een kwart moeilijk, tenzij de docent anders kiest. Gebruik de kalibratie in `kennisbank/kalibratie.json` om je schattingen bij te stellen.

## 8. Vaste onderdelen en vraagmodellen
Een tabel. Elk vast onderdeel (wat er zeker in moet) krijgt een vraagmodel:

| VM | Vast onderdeel | Ankervraag (nagezocht) | Vast: denkstappen en punten | Varieert in A en B | Veelgemaakte fout | Verwachte p |
|---|---|---|---|---|---|---|
| VM1 | Resultaatverdeling in de vaste volgorde | vwo 2023-II vraag 14 | 3p: Vpb in twee schijven (1), preferent dividend (1), reservering (1) | bedrijf, bedragen, aantal aandelen | dividendbelasting aftrekken van de winst | 0,55 |

- De ankervraag is een echte examenvraag die je in de examentekst van het juiste niveau hebt teruggevonden (jaar, tijdvak, vraagnummer). Vind je er geen, schrijf dan "geen anker" en zet het als open punt voor de docent. Gebruik geen opgave die de leerlingen al hebben gemaakt (zie kopje 10).
- "Vast" en "Varieert" maken versie B gelijkwaardig: beide versies komen uit hetzelfde model.
- Raadpleeg de vragenbank (`python3 bouw/vragenbank.py zoek --vak ... --tekst ...`): een vraagmodel dat eerder goed werkte (rit 0,25 of hoger) heeft de voorkeur; een model met een lage of negatieve rit gebruik je alleen met de verbetering uit de evaluatie.
- Daaronder de vaste vormen, zoals "minstens één 'Doe het als volgt:'" of "minstens één 'Toon met een berekening aan' met conclusie".

## 9. Vorm, rekenregels en beoordeling
CE-vorm van het vak, vraagwoorden, bronnen (bijlage of in de opgave), uitwerkbijlage, tekenen of arceren, formuleblad (ja of nee, en welke formules), hulpmiddelen. Elke formule of rekenregel met schrijfwijze en afronding. Definities van begrippen die op meer manieren kunnen worden uitgelegd. Echte tarieven met bron en jaar; fictieve gegevens met "(fictief)".
**Beoordeling:** regels voor rekenfouten en doorwerkfouten van het vak, wat "gelijke strekking" is, hoe een ander juist antwoord wordt beloond, en dat een deelscore één waarneembaar element beloont.

## 10. Examenkoppeling
Welke examens het stijlanker zijn. Welke opgaven de leerlingen al hebben gemaakt (niet gebruiken). De koppeling per vraag staat in matrijs en correctievoorschrift, niet in de toets.

## 11. Thema's
Versie A en versie B per opgave (akkoord docent). Namen en contexten die niet mogen: uit eerdere toetsen en oefentoetsen over deze stof (zie `python3 bouw/vragenbank.py themas --vak ...`).

## 12. Niet opnemen
Alles wat er uitdrukkelijk niet in mag.

## 13. Controle vóór oplevering
Een toetsspecifieke lijst die bij het bouwen wordt afgevinkt.

## 14. Lessen uit eerdere afnames
Bij een herkansing of een toets die voortbouwt op een eerdere: de bevindingen uit de evaluatie van die toets (vragen met een lage rit of extreme p, veelgemaakte leergaten, verbeteringen) en wat daarmee in deze toets gebeurt. Bij een nieuwe toets: wat de vragenbank en de leerregels zeggen over dit onderwerp, of "n.v.t.".

## 15. Na de constructie
Welke kennisbankbestanden na "AFRONDEN" worden bijgewerkt.

## Bewust open gelaten
Gegevens die de docent bewust open laat en die als plaatshouder in de toets komen. Deze blokkeren de goedkeuring niet.

## Open punten
Genummerd. Elke vraag die de docent nog moet beantwoorden, met jouw voorstel. Schrijf "Geen." als alles duidelijk is. Zolang hier iets staat, kan de docent niet goedkeuren.

# VOLLEDIGHEIDSCHECK

Voer deze check uit na elke versie van de opdracht. Wat niet klopt, wordt een open punt (met voorstel) of je lost het op met wat de docent al heeft gezegd.
1. Elk kopje is gevuld, of er staat "n.v.t." met de reden.
2. De rekenbare kern is geldige JSON en klopt: de onderdelen tellen op tot het totaal, elk percentage komt uit in hele punten, Bloom, vraagsoorten en moeilijkheid tellen op, het tempo ligt tussen 2,4 en 3,4. De app rekent dit na en blokkeert goedkeuring bij een fout.
3. Kopje 3 noemt per onderdeel wat een volle score aantoont, en wat niet mag meewegen.
4. Elk vast onderdeel heeft een vraagmodel met een nagezochte ankervraag, vaste denkstappen met punten, wat varieert, een veelgemaakte fout en een verwachte p. De vraagmodellen samen passen in de punten per onderdeel.
5. Elke berekening heeft een rekenregel of formule, met schrijfwijze en afronding. Elk echt tarief heeft een bron en een jaar.
6. Elk begrip waarbij een leerling een verdedigbaar ander antwoord kan geven, heeft een definitie.
7. Tweefasen: elk leerdoel heeft een label volgens de labelbestanden; anders staat er "geen tweefasen".
8. Beslist zijn: formuleblad, hulpmiddelen, tekenen of arceren, uitwerkbijlage, aantal versies, thema's van A en B.
9. Het moeilijkheidsprofiel is haalbaar: elke opgave heeft een instapvraag, en de verwachte p-waarden passen bij de verdeling in de kern.
10. Er is niets dat elkaar tegenspreekt: tussen de kopjes, met de bevestigde leerregels, met de richtlijnen en met eerdere antwoorden van de docent.
11. Er staat niets in dat de docent niet heeft gezegd of bevestigd, behalve onder Open punten.
12. Bij een herkansing of vervolg: kopje 14 verwerkt de evaluatie van de bron-toets.
