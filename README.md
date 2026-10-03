# Speechy

Lokaler PDF-Vorleser für Windows 11 mit Python und einer deutschen Oberfläche.

Die [technische Architektur](architecture.md) beschreibt Prozesse, Datenfluss,
Job-Protokoll, Persistenz und bekannte Grenzen.

## Funktionen

- Text-PDFs öffnen, Textvorschau und automatisches Weiterblättern.
- Deutsche und englische Windows-Stimmen oder lokale Piper-Modelle.
- Start, Pause/Weiter, Stop, vorherige/nächste Seite und Tempo 100–240.
- Letzte Seite pro PDF speichern.
- Optionale OCR für Seiten ohne eingebetteten Text.
- Gesamte PDF als WAV oder MP3 exportieren, ohne Cloud-Dienst.

## Installation unter Windows

### Automatisch mit start.bat

Das gesamte Repository beziehungsweise ZIP in einen beschreibbaren Ordner
entpacken und **start.bat** doppelklicken. Die Startdatei verwendet
`scripts/start.ps1` und `scripts/bootstrap.py`; diese Dateien müssen mit entpackt werden.

Sie prüft Python 3.11–3.13 (64 Bit) einschließlich eines echten Tk-Fenstertests.
Fehlt eine geeignete Installation, lädt sie den signierten
[Python-3.13.16-Installer](https://www.python.org/downloads/windows/)
von python.org und installiert ihn im Unterordner `.runtime/python313`.
Eine eigene Umgebung `.speechy-venv` hält die Python-Pakete getrennt.
Globale PATH-Einstellungen werden nicht geändert; Administratorrechte werden
nicht angefordert.

Danach werden fehlende Pakete, die Piper-Stimmen `de_DE-thorsten-medium` und
`en_US-lessac-medium`, OCR-Sprachdaten für Deutsch/Englisch sowie ffmpeg für MP3
eingerichtet. Modelle, OCR und MP3-Encoder werden geprüft. Speechy startet mit
Piper, voreingestellten Modellpfaden und aktivierter OCR. Windows-Stimmen stehen
weiterhin zur Auswahl, werden aber nicht automatisch als Windows-Sprachpakete
installiert: Für den Standardstart übernimmt Piper die Sprachausgabe.

Beim ersten Start sind Internetzugang und mehrere hundert MB freier Speicher
erforderlich. Weitere Starts verwenden vorhandene gültige Komponenten ohne
Paket-Upgrades oder erneute Downloads. Fehlende oder beschädigte Komponenten
können erneut Internet benötigen. Die Anwendung bleibt nach der Einrichtung lokal.
Bei einem Fehler bleibt die Konsole mit der Fehlermeldung geöffnet.

Mit `start.bat -CheckOnly` lassen sich die Voraussetzungen prüfen, ohne sie
zu installieren oder die Anwendung zu starten. Eine nicht verwendbare vorhandene
virtuelle Umgebung wird aufbewahrt; für einen normalen Start wird eine neue angelegt.
PowerShells Ausführungsrichtlinie wird nur für den gestarteten Prozess gesetzt;
eine durch die Organisation erzwungene Richtlinie kann den Start weiterhin verhindern.

### Manuell

Python 3.11 oder 3.12 (64 Bit) mit Tkinter installieren. Im Repository-Ordner:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe text2speech\reader1.py
```

Die Windows-Stimmen müssen lokal installiert sein. Wenn keine passende deutsche
oder englische SAPI-Stimme vorhanden ist, meldet Speechy dies; es verwendet keine
Stimme einer anderen Sprache als Ersatz. Nicht jede moderne Windows-Stimme ist
über SAPI verfügbar.

## Piper einrichten (optional)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-piper.txt
New-Item -ItemType Directory -Force voices
.\.venv\Scripts\python.exe -m piper.download_voices --data-dir voices de_DE-thorsten-medium en_US-lessac-medium
```

In Speechy die beiden `.onnx`-Dateien über **ONNX wählen** zuordnen und unter
**Stimme** Piper auswählen. Zu jedem Modell muss die gleichnamige
`.onnx.json`-Datei daneben liegen. Die Sprachauswahl bestimmt das verwendete Modell;
der PDF-Text wird nicht übersetzt. Das Tempo steuert bei Piper die relative
Sprechdauer (165 entspricht dem Standardtempo), bei Windows die Sprechrate.

Die Einrichtung und Modelldownloads benötigen Internet. Danach arbeiten
PDF-Verarbeitung, Vorlesen und Export lokal. Modelle werden während des Vorlesens
nicht automatisch heruntergeladen. Piper wird nur bei Auswahl dieser Engine geladen.
Dokumentation: [Piper](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/CLI.md).
Bei Weitergabe des Programms oder der Modelle deren jeweilige Lizenz beachten.

## OCR einrichten (optional)

PyMuPDF enthält die Tesseract-Anbindung. Benötigt werden die lokalen Sprachdateien
`deu.traineddata` und `eng.traineddata`, zum Beispiel aus
[tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast).
Beide Dateien in einen Ordner `tessdata` legen, diesen über **tessdata wählen**
auswählen und **OCR bei Seiten ohne Text** aktivieren. Ein Tesseract-Installationsordner
mit diesen Sprachdaten funktioniert ebenfalls. Alternativ wird `TESSDATA_PREFIX`
als vorausgewählter Pfad verwendet.

OCR erfolgt beim Vorlesen oder Export; danach erscheint der erkannte Seitentext
in der Vorschau. Pro Seite wird die gewählte Sprache verwendet. Seiten mit bereits
eingebettetem Text werden nicht erneut erkannt. Mischseiten mit Text und zusätzlichen
gescannten Textbildern benötigen gegebenenfalls eine vorherige vollständige OCR.
Fehlende Sprachdaten führen zu einer verständlichen Fehlermeldung.

## Bedienung und Export

1. PDF öffnen, Sprache und Engine wählen.
2. **Start** liest ab der aktuellen Seite bis zum Dokumentende.
3. **Pause / Weiter** unterbricht sofort; Weiter wiederholt den unterbrochenen
   Textblock. **Stop** setzt die Textposition zum Anfang der aktuellen Seite zurück.
4. **WAV / MP3 exportieren** verarbeitet die gesamte PDF ab Seite 1.

Export funktioniert mit beiden Engines. MP3 benötigt zusätzlich `ffmpeg` mit
`libmp3lame` im Windows-`PATH`; WAV benötigt kein ffmpeg. Während eines Exports
ist Pause deaktiviert; **Stop** bricht ab. Speechy ersetzt die Zieldatei erst nach
erfolgreichem Abschluss, sodass ein abgebrochener Export keine bestehende Datei
überschreibt. Große WAV-Dateien sind durch das WAV-Format auf etwa 4 GB begrenzt.

Sprach-, Engine-, Modell- oder OCR-Wechsel stoppen einen laufenden Vorgang.
Änderungen am Tempo und manuell eingegebene Pfade gelten beim nächsten Start.
Lesepositionen stehen in `%LOCALAPPDATA%\Speechy\state.json`; gespeichert wird die
Seite, nicht der Textblock. Verschlüsselte PDFs bitte vorher entschlüsseln.
Mehrspaltige Layouts, Tabellen und Kopfzeilen können die Lesereihenfolge beeinflussen.

## Entwicklung und Prüfungen

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Die Tests prüfen echte PDF-Extraktion und WAV-Dateien, simulieren TTS sowie
OCR-Fehler und prüfen, dass veraltete Job-Ereignisse keine neue Lesesitzung verändern.
PDF/OCR/TTS laufen in einem eigenen Prozess. Die Oberfläche verarbeitet nur
Nachrichten und greift nicht aus einem Hintergrundthread auf Tkinter zu.
