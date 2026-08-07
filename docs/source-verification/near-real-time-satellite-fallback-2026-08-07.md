# Near-Real-Time-Satellitenfallback

Stand: 2026-08-07

Für die Live-Position ist Aktualität wichtiger als die Beibehaltung eines verspäteten RGB-Produkts. EUMETView liefert ohne `time` jeweils die neueste verfügbare Darstellung eines Layers, aber einzelne Visualisierungs-Layer können unterschiedlich weit hinter der aktuellen Zeit liegen.

Die lokale Anwendung darf deshalb bei einer verifizierten Aufnahme älter als 20 Minuten automatisch auf eine frischere MTG-FCI-Darstellung wechseln. Priorität für den Fallback ist der direkt bildgebende IR10.5-HRFI-Layer, danach GeoColour. Historische Frames und manuell gewählte Zeitpunkte werden nicht umgeschrieben.

Die Oberfläche muss den tatsächlich angezeigten Layer, die verifizierte Aufnahmezeit und das Alter offen ausweisen. Ein automatisch verwendeter Fallback darf nicht als das ursprünglich gewählte Produkt beschriftet werden.
