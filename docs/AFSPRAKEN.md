# Afspraken voor ToetsCirkel

Deze afspraken gelden vanaf fase 0 voor alle drie de apps en voor alles wat we samenvoegen.

## 1. Wat wel en niet in Git staat

| In Git | Nooit in Git (staat lokaal op de Mac) |
| --- | --- |
| Code van de drie apps (`apps/`) | `toetsevaluatie.db`, `oefentool.db` en andere databases |
| Kennisbank: richtlijnen, PTA, rekenregels, examenanalyses, goedgekeurde toetsopdrachten | Scans, gemaakt werk, klassenlijsten, exports per leerling |
| Vragenbank en kalibratie (zonder leerlinggegevens) | `toetsen/` (projecten per toets) en `Opdrachten/` (sets per leerling) |
| Leerregels van de constructie, kwaliteitsregels van de oefentool | `docent_leerregels.json` (bevat letterlijke leerlingantwoorden) |
| Drie goedgekeurde voorbeeldtoetsen | `instellingen.json` (persoonlijke paden); zie `instellingen.voorbeeld.json` |

De `.gitignore` dwingt dit af. Twijfel je of iets leerlinggegevens bevat, dan gaat het niet in Git.

## 2. Privacy

- Naar Claude gaan geen namen. Leerlingnummers alleen waar het niet anders kan (het lezen van de scan).
- Namen staan alleen in de klassenlijsten op de Mac en komen alleen op printversies.
- Scans blijven in OneDrive (map `ToetsCirkel Scans`); verwerkte scans gaan naar `Verwerkt/`.

## 3. De toetscode (`toets_id`)

We gebruiken de vorm die de Toetsevaluatie al leest, zodat er niets omgezet hoeft te worden:

```
<toetsnummer>_<soort>_<klas>_<Vak>[_tweefasen]
```

| Deel | Waarden | Voorbeeld |
| --- | --- | --- |
| toetsnummer | het PTA-nummer of een eigen naam zonder spaties | `AT5.11`, `T2Proefwerk`, `OEF3A` |
| soort | `SE` schoolexamen of andere toets, `OE` oefentoets uit de oefentool | `SE` |
| klas | `mavo3`, `mavo4`, `4H`, `4V`, `5H`, `5V`, `6V` | `5H` |
| Vak | `Economie`, `Bedrijfseconomie` | `Economie` |
| tweefasen | alleen bij een tweefasentoets (4V, 5V) | `_tweefasen` |

Voorbeelden: `AT5.11_SE_5H_Economie`, `AT5.11_SE_5V_Economie_tweefasen`, `OEF3A_OE_5H_Economie`.

Het schooljaar staat niet in de code maar in de afnamedatum. In fase 2 krijgt elke toets daarnaast een interne
sleutel in de gezamenlijke database; de `toets_id` blijft dan de naam die jij ziet.

## 4. De gouden toets

`AT5.11_SE_5H_Economie` is de gouden toets. Na elke wijziging die de berekeningen raakt, moet
`tests/gouden_toets.py controleren AT5.11_SE_5H_Economie` "OK" geven. De momentopname staat lokaal in
`~/Documents/ToetsCirkel_data/gouden/` en bevat alleen samenvattingen.

## 5. Werkwijze

- Elke fase komt als pull request; jij keurt goed en voegt samen.
- Code komt in de apps via `scripts/Installeer.command`: dat maakt eerst een back-up en raakt geen gegevens aan.
- `scripts/Backup.command` kan altijd; de laatste 30 back-ups blijven staan in `~/Documents/ToetsCirkel_data/backups/`.
