# Sicherheitskonzept

## Sicherheitsziel

Der Live-Kern soll einen technisch konservativen Hardware-Risikozustand für das lokale Astro-Setup erzeugen. Er darf fehlende, veraltete, unvollständige oder widersprüchliche Kerndaten niemals als Sicherheit interpretieren.

> Die Anwendung ist kein zertifiziertes Schutz- oder Unwetterwarnsystem.

Die endgültige Verantwortung für Aufbau, Abdeckung und Schutz der Ausrüstung bleibt beim Nutzer.

## Vertrauensgrenzen

### Beobachtete Daten

- DWD-RV-Radardaten
- amtliche DWD-CAP-Warnungen
- amtliche DWD-Warngebietsgeometrien

Diese Daten werden als externe Eingaben behandelt und vor der Nutzung begrenzt, validiert und vollständig verarbeitet.

### Eigene technische Auswertung

Die Anwendung berechnet unter anderem Standortbezug, Niederschlagsannäherung, Datenalter, Vollständigkeit und Gefahrenevidenz. Diese Auswertung ist klar von amtlichen Texten getrennt.

### Eigene Sicherheitsentscheidung

Die Decision Engine erzeugt eine hardwarebezogene Entscheidung `GREEN`, `YELLOW`, `RED` oder `UNKNOWN`. Eine aktive relevante Gewitter- oder Starkregenwarnung am Standort ist unabhängig von einer niedrigeren CAP-Severity rot.

### Ausgeschlossene Systeme

Rain Alarm ist kein Bestandteil der sicherheitsrelevanten Pipeline. Es gibt kein Scraping, Reverse Engineering oder Verwenden undokumentierter Endpunkte. Das Ausbleiben einer Rain-Alarm-Meldung kann niemals Green erzeugen.

## Fail-closed-Regeln

- Rot hat Vorrang vor Gelb, Unknown und Green.
- Gelb bleibt sichtbar, wenn eine andere Quelle ausfällt.
- Ein technischer Fehler entfernt keinen erkannten Hazard.
- `DWD_RV`, `DWD_CAP` und `LOCAL_PERSISTENCE` müssen für Green frisch und `LIVE` sein.
- Ein alter guter Snapshot bleibt diagnostisch erhalten, ermöglicht nach Ablauf seiner Frische aber kein Green.
- Ein Prozessneustart löscht keine persistierte Gefahr.
- Quittierung verändert weder Latch noch Risiko noch Quellenzustand.
- Eine Entwarnung benötigt frische vollständige Quellendaten, abgelaufene Mindesthaltezeit und die konfigurierte Zahl klarer Folgezyklen.
- Eine beschädigte Datenbank oder ein fehlgeschlagener persistenter Schreibvorgang kann kein Green veröffentlichen.
- Die öffentliche Seite zeigt niemals lokalen Live-Green-Status.

## Netzwerk und lokaler Zugriff

Der Standardbetrieb bindet an `127.0.0.1`. Trusted-Host-Middleware akzeptiert nur Loopback-/Testhosts. Eine Freigabe ins LAN oder Internet ist nicht Teil dieser Phase und erfordert eine eigene Authentifizierungs-, TLS- und Firewallbewertung.

Schreibende Endpunkte sind durch SameSite-CSRF-Cookie, `X-CSRF-Token` und Origin-Prüfung geschützt. Sicherheits- und Zustandsantworten werden mit `Cache-Control: no-store` ausgeliefert.

Zusätzliche Header:

- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- restriktive Permissions Policy für Kamera und Mikrofon
- Content Security Policy für lokale und bewusst erlaubte Ressourcen

## Download- und Archivschutz

Externe Downloads verwenden:

- feste Zeitlimits
- Größenlimits
- erlaubte Dateiendungen
- atomare Zieldateien
- Inhalts-Hashes
- begrenzte Wiederholungen

TAR- und ZIP-Verarbeitung lehnt unter anderem Pfad-Traversal, absolute Pfade, Links, übermäßige Dateizahl, zu große expandierte Inhalte, unerwartete Dateitypen und widersprüchliche Archive ab.

Ein Produktzyklus wird erst `LIVE`, wenn alle fachlich notwendigen Dateien konsistent, vollständig und erfolgreich ausgewertet sind.

## Persistenz

SQLite verwendet:

- WAL-Modus
- Busy Timeout
- Fremdschlüsselprüfung
- Schema-Version
- Integritätsprüfung
- Transaktionen für Entscheidung und Latches
- Backup vor nicht kompatiblen Migrationen

Gespeichert werden sicherheitsrelevante Quellensnapshots, unveränderliche Entscheidungssnapshots, aktive Latches, Alarmereignisse und Quittierungen.

Ein Fehler beim Speichern einer vermeintlich grünen Entscheidung führt zu einer erneuten konservativen Bewertung mit ausgefallener Persistenz. Das noch nicht erfolgreich gespeicherte Green wird nicht veröffentlicht.

## Datenschutz und öffentliche Artefakte

Private lokale Daten liegen außerhalb des Repositorys im Anwendungsdatenverzeichnis. Nicht committen:

- genaue private Koordinaten
- lokale Konfigurationsdateien
- SQLite-Datenbanken und WAL-Dateien
- Cache- und Downloadinhalte
- Logs mit privaten Pfaden
- Tokens, Cookies oder Request-Header

Der öffentliche Build verwendet eine Positivliste. Er enthält keine lokale Datenbank, keine private Konfiguration und keine Live-API. Die öffentliche Runtime-Konfiguration lautet sinngemäß `PUBLIC` und `localApiAvailable=false`.

Öffentliche Diagnose- und API-Antworten geben keine vollständigen lokalen Dateipfade, Request-Header oder geheimen Inhalte aus. Fehler werden als begrenzter Code plus sichere Meldung veröffentlicht.

## Browseralarm

Audio und Notifications sind Komfortkanäle, nicht die Quelle des Sicherheitszustands. Browser verlangen eine Nutzeraktion für Audio und eine ausdrückliche Berechtigung für Notifications.

- Alarme werden aus Backend-Übergangsereignissen erzeugt.
- Identische Event-IDs werden im Browser nicht erneut abgespielt.
- Quittierung stoppt Wiederholungen, nicht die Gefahr.
- Rot und Gelb sind zusätzlich durch Text, Symbol, Handlung und Seitentitel erkennbar.
- Verbindungsverlust entfernt ein altes Green sofort.
- Ein bereits sichtbares Rot oder Gelb wird bei Verbindungsverlust nicht zu Green oder „sicher“ umgedeutet.

## Abhängigkeiten und Lieferkette

Neue Abhängigkeiten müssen gepflegt, frei verfügbar und auf den konkreten Zweck begrenzt sein. Versionen sind in kompatiblen Bereichen festgelegt. CI prüft Linux und Windows, Ruff, Mypy, Tests, Branch-Coverage, öffentlichen Build und einen isolierten Chromium-End-to-End-Test.

Live-DWD-Smokes laufen getrennt von reproduzierbaren Unit- und Integrationstests. Externe Erreichbarkeit ist keine Voraussetzung für die normale Sicherheits-Testmatrix.

## Sicherheitsrelevante Änderungen

Bei Änderungen an Decision Engine, Quellrunnern, Latches, Persistenz, Alarmen, API oder öffentlichem Build müssen mindestens geprüft werden:

1. Start ohne Daten bleibt `UNKNOWN`.
2. Fehlende oder veraltete Kernquelle verhindert Green.
3. Rot beziehungsweise Gelb überlebt einen nachfolgenden Quellenausfall.
4. Aktive Gefahren überleben einen Neustart.
5. Quittierung löscht keine Gefahr.
6. API, SSE, Persistenz und Webansicht beziehen sich auf dieselbe Snapshot-ID.
7. Ein öffentlicher Build enthält keine privaten Daten und kein Live-Green.
8. Ein API-/SSE-Verlust lässt kein altes Green sichtbar.

## Meldung von Sicherheitsproblemen

Keine sensiblen Daten in öffentliche Issues kopieren. Fehlerberichte sollen sichere Fehlercodes, anonymisierte Zeitpunkte und reproduzierbare Schritte enthalten. Lokale Konfiguration, Datenbank, Koordinaten und vollständige Logs nur nach vorheriger Bereinigung weitergeben.
