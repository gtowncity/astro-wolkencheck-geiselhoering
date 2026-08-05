# Astro-Wolkencheck Geiselhöring

[![GitHub Pages](https://github.com/gtowncity/astro-wolkencheck-geiselhoering/actions/workflows/deploy-pages.yml/badge.svg)](https://github.com/gtowncity/astro-wolkencheck-geiselhoering/actions/workflows/deploy-pages.yml)

Öffentliche stabile Version der Astro-Wetterplanung für Geiselhöring.

**Live-Webseite:** https://gtowncity.github.io/astro-wolkencheck-geiselhoering/

## Zweck

Die Webseite unterstützt die Planung astronomischer Beobachtungen und Aufnahmen am Standort Geiselhöring. Sie verbindet astronomische Berechnungen mit numerischen Wettervorhersagen mehrerer allgemein zugänglicher Wetterdatenanbieter.

Die dargestellten Werte sind Modellrechnungen und keine Wettergarantie. Kurzfristige lokale Wetteränderungen können von den Vorhersagen abweichen.

## Veröffentlichung

- Stabile Version: **4.2.1**
- Release-Tag: **v4.2.1**
- Deployment: automatisch über den offiziellen GitHub-Pages-Workflow auf `main`
- Deploymentstatus: siehe Statusanzeige oben

## Entwicklung: lokaler Live-Kern v4.3

Der Branch `feature/dwd-nowcast-v4.3` ergänzt die bestehende Forecast-Anwendung um einen ausschließlich lokal betriebenen Sicherheits- und Nowcast-Kern:

- DWD-RV-Radar und amtliche DWD-CAP-Warnungen
- konservative Entscheidung `GREEN`, `YELLOW`, `RED` oder `UNKNOWN`
- persistente Gefahren-Latches und SQLite-Historie
- geschützte REST-API und Server-Sent Events
- lokale Alarmierung sowie zugängliche Live-Webansicht
- getrennte, klar als Demo markierte visuelle Vorschau unter `preview/`

> Die Anwendung ist kein zertifiziertes Schutz- oder Unwetterwarnsystem. Die öffentliche GitHub-Pages-Seite besitzt keine lokale Live-Sicherheitsfreigabe und zeigt niemals einen lokalen Live-Green-Zustand.

Lokaler Entwicklungsstart mit Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
$env:ASTRO_WOLKENCHECK_DATA_DIR = "$env:LOCALAPPDATA\AstroWolkencheck"
python -m uvicorn nowcast_service.app:app --host 127.0.0.1 --port 8765
```

Danach `http://127.0.0.1:8765/` öffnen.

Ausführliche Hinweise:

- [Lokaler Betrieb](docs/operations.md)
- [Sicherheitskonzept](docs/security.md)
- [Architektur](docs/architecture.md)
- [DWD-Radarquellenprüfung](docs/source-verification/dwd-radar-discovery-2026-08-05.md)
- [DWD-CAP-Quellenprüfung](docs/source-verification/dwd-cap-2026-08-05.md)
- [DWD-Warngebietsprüfung](docs/source-verification/dwd-warning-areas-2026-08-05.md)

## Datenschutz

Die öffentliche Webseite benötigt keine Anmeldung, verwendet keine eigenen Tracker und speichert keine Daten auf einem eigenen Server. Einstellungen und zwischengespeicherte Wetterdaten können ausschließlich lokal im Browser über LocalStorage gespeichert werden. Beim Wetterabruf stellt der Browser direkte Verbindungen zu den verwendeten öffentlichen Datenanbietern her; hierfür gelten deren jeweilige Datenschutzbestimmungen.

Der lokale v4.3-Dienst speichert seine sicherheitsrelevanten Zustände ausschließlich im privaten lokalen Anwendungsdatenverzeichnis. Datenbank, lokale Konfiguration, genaue private Koordinaten, Cache und Logs dürfen nicht in öffentliche Builds oder Commits gelangen.

## Technischer Hinweis

Die veröffentlichte Anwendung ist eine eigenständige HTML-Datei. GitHub Pages stellt ausschließlich die für die öffentliche Forecast-Webseite benötigten Dateien bereit. Der lokale FastAPI-Dienst und seine privaten Laufzeitdaten werden nicht über GitHub Pages ausgeliefert.
