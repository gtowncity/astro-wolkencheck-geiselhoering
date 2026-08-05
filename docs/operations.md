# Lokaler Betrieb

## Zweck und Grenze

Der v4.3-Live-Kern ist für den lokalen Betrieb auf dem Windows-10-x64-NINA-Laptop vorgesehen. Er verbindet DWD-RV-Radar, amtliche DWD-CAP-Warnungen, persistente Gefahren-Latches, Sicherheitsentscheidung, Alarmierung, REST-API, SSE und die lokale Webansicht.

> Die Anwendung ist kein zertifiziertes Schutz- oder Unwetterwarnsystem.

Die öffentliche GitHub-Pages-Seite bleibt eine Forecast-Anwendung ohne lokale Live-Sicherheitsfreigabe.

## Voraussetzungen

- Windows 10 x64 oder ein unterstütztes Testsystem
- Python 3.12
- Netzwerkzugriff auf die dokumentierten DWD-Open-Data-Quellen
- Schreibzugriff auf das private lokale Anwendungsdatenverzeichnis
- moderner Browser mit EventSource; Audio und Notifications sind optional

Entwicklungsinstallation:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Lokaler Start:

```powershell
$env:ASTRO_WOLKENCHECK_DATA_DIR = "$env:LOCALAPPDATA\AstroWolkencheck"
python -m uvicorn nowcast_service.app:app --host 127.0.0.1 --port 8765
```

Danach im selben Rechner öffnen:

```text
http://127.0.0.1:8765/
```

Der Dienst soll nicht ohne zusätzliche Schutzmaßnahmen an eine öffentliche Netzwerkschnittstelle gebunden werden.

## Erwartetes Startverhalten

1. Der Dienst öffnet die lokale SQLite-Datenbank und prüft ihre Integrität.
2. Persistierte aktive Gefahren-Latches werden vor der ersten neuen Bewertung geladen.
3. Der Sicherheitszustand beginnt konservativ mit `UNKNOWN`, sofern nicht ein persistierter roter oder gelber Hazard Vorrang hat.
4. RV und CAP werden sofort kontrolliert erstmals abgerufen.
5. `GREEN` ist erst möglich, wenn RV und CAP frisch, vollständig und ohne relevante Gefahr sind und die lokale Persistenz verfügbar ist.

`INITIALIZING`, `STALE`, `FAILED` oder unvollständige Daten einer Kernquelle verhindern Green.

## Bedienung

### Alarme aktivieren

Browser blockieren Audio und Benachrichtigungen bis zu einer Nutzeraktion. Einmal auf **Alarme aktivieren** drücken. Danach:

- Gelb erzeugt einen unterscheidbaren Doppelton.
- Rot erzeugt eine deutlich stärkere Dreitonfolge.
- Browserbenachrichtigungen werden nur nach erteilter Browserberechtigung angezeigt.
- Eine Quittierung stoppt Wiederholungen, verändert aber niemals den Gefahrzustand.
- Eine neue oder stärkere Gefahr alarmiert erneut.

Der reale Alarmton und die Notification-Anzeige müssen einmal auf dem tatsächlichen NINA-Laptop geprüft werden.

### Manuelle Aktualisierung

**Alle Quellen aktualisieren** startet geschützte Quelljobs. Derselbe Quelljob läuft niemals parallel zu sich selbst. Die Schaltfläche ist kein Freigabeschalter: Ein fehlgeschlagener Abruf kann keinen alten Hazard löschen und kein Green erzwingen.

### Quittierung

**Alarm quittieren** speichert den Quittierungszeitpunkt und stoppt Wiederholungen des bestehenden Alarms. Latch, Risiko und Quellenzustand bleiben unverändert.

## Betriebsprüfung

Wichtige Endpunkte:

```text
GET /api/v1/health/live
GET /api/v1/health/ready
GET /api/v1/runtime
GET /api/v1/safety
GET /api/v1/sources
GET /api/v1/alerts
GET /api/v1/diagnostics
GET /api/v1/events
```

Vor einer Aufnahmenacht prüfen:

1. Lokale Ansicht zeigt `LOCAL` und eine verbundene SSE/API-Verbindung.
2. `DWD_RV`, `DWD_CAP` und `LOCAL_PERSISTENCE` sind `LIVE`.
3. Datenalter liegt unter den konfigurierten Frischegrenzen.
4. Snapshot-ID stimmt in Safety-, Nowcast- und Diagnoseansicht überein.
5. Alarmaktivierung wurde im Browser bestätigt.
6. Ein bewusst ausgelöster Testzustand wird hör- und sichtbar angezeigt.
7. Bei deaktiviertem Netzwerk verschwindet ein vorheriges Green sofort und wird `UNKNOWN`.

## Datenbank und Backups

Die SQLite-Datenbank liegt ausschließlich im privaten Anwendungsdatenverzeichnis. Sie verwendet WAL-Modus, Busy Timeout, Transaktionen und Integritätsprüfung.

Vor Schemaänderungen wird eine Sicherung angelegt. Für eine manuelle Sicherung:

1. Dienst sauber beenden.
2. Das gesamte lokale Anwendungsdatenverzeichnis kopieren.
3. Datenbankdatei sowie vorhandene `-wal`- und `-shm`-Dateien gemeinsam behandeln.
4. Sicherung schreibgeschützt aufbewahren.

Eine beschädigte oder nicht beschreibbare Datenbank führt konservativ zu `UNKNOWN` beziehungsweise lässt einen bereits persistierten Gefahrzustand bestehen. Sie darf niemals stillschweigend durch eine leere Datenbank ersetzt werden, solange aktive Latches ungeklärt sind.

## Fehlerbehandlung

### RV oder CAP ist `FAILED`

- Diagnoseansicht öffnen und `failureCode` lesen.
- Netzwerk, Systemzeit und DWD-Erreichbarkeit prüfen.
- Manuelle Aktualisierung einmal auslösen.
- Letzten guten Snapshot nur zur Diagnose verwenden; er ermöglicht nach Ablauf der Frischegrenze kein Green.

### Quelle ist `STALE`

- Datenalter und Schedulerstatus prüfen.
- Der Zustand bleibt konservativ, bis ein frischer vollständiger Zyklus vorliegt.

### Persistenz ist `FAILED`

- Dienst beenden.
- Anwendungsdatenverzeichnis sichern.
- freien Speicher, Dateirechte und SQLite-Integrität prüfen.
- Nicht durch Löschen der Datenbank „reparieren“, solange aktive Gefahren nicht anderweitig geklärt sind.

### SSE oder API fällt aus

Die Webansicht darf kein altes Green weiter anzeigen. Rot und Gelb bleiben sichtbar und werden zusätzlich als nicht mehr live verbunden markiert. Browser neu laden und Dienststatus prüfen.

## Sauberes Beenden

Den Uvicorn-Prozess mit `Ctrl+C` beenden. Der Scheduler stoppt seine Tasks kontrolliert. Kein Prozess darf durch Löschen der Datenbank oder des Cache-Verzeichnisses beendet werden.

## Live-Smoke-Tests

Die getrennten Radar-, CAP- und Warngebiets-Smokes prüfen aktuelle externe Quellformate. Sie sind keine Sicherheitsfreigabe und ersetzen weder reproduzierbare Fixtures noch die lokale Funktionsprüfung auf dem Zielrechner.
