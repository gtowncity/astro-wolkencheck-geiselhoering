# EUMETSAT Live-Aufnahmezeit-Verifikation

Stand: 2026-08-07

## Ziel

Die lokale Satellitenansicht darf die Uhrzeit des HTTP-Abrufs nicht mit der tatsächlichen Aufnahmezeit des Satellitenbildes verwechseln. Für den untimed EUMETView-WMS-Liveabruf wird deshalb eine Aufnahmezeit nur angezeigt, wenn sie unabhängig verifiziert wurde.

## Vorgehen

1. Das Livebild wird weiterhin über `GetMap` ohne `time` geladen, damit EUMETView das neueste verfügbare Raster liefert.
2. Als Zeitkandidaten werden die jüngsten MTG-Erfassungszyklen aus der EUMETSAT Data Store Browse API gelesen.
3. Für FCI-basierte RGB-Produkte werden die Normal-Resolution-FCI-Zyklen (`EO:EUM:DAT:0662`) verwendet; für das hochaufgelöste Infrarotprodukt die High-Resolution-FCI-Zyklen (`EO:EUM:DAT:0665`); für Lightning AFA `EO:EUM:DAT:0687`.
4. Jeder Zeitkandidat wird erneut als explizit zeitgestempeltes EUMETView-WMS-Raster mit identischen Kartenparametern geladen.
5. Die Aufnahmezeit wird nur akzeptiert, wenn das RGB-Raster pixelgenau mit dem untimed Livebild übereinstimmt.
6. Falls die Browse API keinen passenden Kandidaten liefert, werden die WMS-GetCapabilities-Zeiten auf dieselbe Weise geprüft.
7. Gibt es keinen verifizierten Pixel-Match, zeigt die Anwendung ausdrücklich `Aufnahmezeit noch nicht verifiziert` und erfindet keinen Zeitstempel.

## Anzeige

Bei erfolgreicher Verifikation enthält das Bild sowohl die tatsächliche Aufnahmezeit als auch die separate Abrufzeit, jeweils als Ortszeit und UTC.

Beispiel:

`Aufnahme 07.08.2026 09:10 Ortszeit (07:10 UTC) · LIVE-Abruf 09:16 Ortszeit (07:16 UTC)`

Die Abrufzeit sagt nur, wann die lokale Anwendung das Bild von EUMETView erhalten hat. Die Aufnahmezeit wird ausschließlich nach dem beschriebenen Pixel-Match als verifiziert dargestellt.
