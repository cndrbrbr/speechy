# Speechy

Lokaler PDF-Vorleser und Spracherkennung für Windows 11 mit Python und einer deutschen Oberfläche.

Die [technische Architektur](architecture.md) beschreibt Prozesse, Datenfluss,
Job-Protokoll, Persistenz und bekannte Grenzen.

## Funktionen

- Text-PDFs öffnen, Textvorschau und automatisches Weiterblättern.
- Deutsche und englische Windows-Stimmen oder lokale Piper-Modelle.
- Start, Pause/Weiter, Stop, vorherige/nächste Seite und Tempo 100–240.
- Letzte Seite pro PDF speichern.
- Optionale OCR für Seiten ohne eingebetteten Text.
- Gesamte PDF als WAV oder MP3 exportieren, ohne Cloud-Dienst.
- Mikrofon aufnehmen oder Audiodateien lokal in Text umwandeln.
- Erkannten Text bearbeiten und als einfache UTF-8-Textdatei (`.txt`) speichern.

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
und ein mehrsprachiges Whisper-Modell für die Spracherkennung eingerichtet.
Modelle, OCR und MP3-Encoder werden geprüft. Speechy startet mit
Piper, voreingestellten Modellpfaden und aktivierter OCR. Windows-Stimmen stehen
weiterhin zur Auswahl, werden aber nicht automatisch als Windows-Sprachpakete
installiert: Für den Standardstart übernimmt Piper die Sprachausgabe.

Beim ersten Start sind Internetzugang und mehrere hundert MB freier Speicher
erforderlich. Weitere Starts verwenden vorhandene gültige Komponenten ohne
Paket-Upgrades oder erneute Downloads. Fehlende oder beschädigte Komponenten
können erneut Internet benötigen. Die Anwendung bleibt nach der Einrichtung lokal.
Bei einem Fehler bleibt die Konsole mit der Fehlermeldung geöffnet.
Wenn Windows Python noch als installiert führt, aber Dateien im Speechy-Ordner
fehlen, versucht der Starter einmal die Installer-Reparatur. Die Protokolle
liegen unter `.runtime/python-install.log`, `.runtime/python-repair.log` und
bei weiterhin fehlerhaftem Python/Tk unter `.runtime/python-probe.log`.
Ein fehlendes `python.exe` wird getrennt von einem Tkinter-Fehler gemeldet.

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

## Sprache in Text und Textdateien

Speechy hat die Reiter **Text vorlesen** und **Sprache in Text**.
Der zweite Reiter nutzt [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
mit einem lokalen mehrsprachigen `base`-Modell auf der CPU (INT8).
`start.bat` installiert die benötigten Pakete und lädt das Modell einmalig nach
`.models/whisper-base`. Dafür ist Internet erforderlich; Audio wird dabei nicht
hochgeladen. Die anschließende Erkennung verwendet ausschließlich das lokale Modell.

Für Mikrofon-Diktate:

1. Reiter **Sprache in Text** öffnen und Deutsch, Englisch oder Automatisch wählen.
2. Bei Bedarf **Mikrofone aktualisieren** klicken und das Headset auswählen.
   Sonst wird das Windows-Standardmikrofon verwendet. **Aufnahme starten** klicken.
3. Beim Sprechen erscheint eine laufende Textvorschau mit kurzer Verzögerung.
   **Aufnahme stoppen** beendet das Mikrofon und prüft danach die gesamte Aufnahme
   noch einmal; die Vorschau bleibt bis zum endgültigen Ergebnis sichtbar.
4. Den erkannten Text prüfen und gegebenenfalls bearbeiten.
5. **Als Textdatei speichern …** wählen und eine `.txt`-Datei speichern.

Für vorhandene Dateien **Audiodatei öffnen** und danach **In Text umwandeln**
wählen. WAV, MP3, M4A, FLAC, OGG und weitere von PyAV unterstützte Formate können
verarbeitet werden. Dateiendungen allein garantieren keine Decoder-Unterstützung.
Die Ausgabe ist UTF-8 ohne Zeitstempel oder technische Metadaten, mit einem
erkannten Textabschnitt je Zeile. Vorhandene Zieldateien werden erst nach
erfolgreichem Schreiben ersetzt. Ungespeicherter Text wird vor einer neuen
Transkription oder dem Schließen durch eine Rückfrage geschützt.

**Abbrechen** beendet Aufnahme/Erkennung. Bereits erkannter Teiltext bleibt
bearbeitbar und speicherbar. Die Live-Vorschau wird ungefähr alle zwei Sekunden
aktualisiert, zuzüglich der Rechenzeit des PCs. Der aktuelle Text kann sich dabei
noch ändern. Nach dem Stoppen ersetzt das abschließende Ergebnis die Vorschau.
Während der Aufnahme und der Abschlussprüfung ist die Bearbeitung gesperrt. Bei fehlendem Mikrofonzugriff in Windows die
Freigabe für Desktop-Apps prüfen und das gewünschte Mikrofon auswählen.
Beim Starten einer Aufnahme wird laufendes Vorlesen gestoppt; Vorlesen lässt
sich während einer Aufnahme nicht starten.

Aufnahmen liegen temporär auf dem lokalen Datenträger und werden beim regulären
Schließen entfernt. Vorhandene Audiodateien werden unverändert eingelesen.
Nach einem Programmabsturz können temporäre Dateien zurückbleiben. Die Erkennung
ist nicht fehlerfrei; insbesondere Namen, undeutliche Sprache und Hintergrundgeräusche
erfordern eine Textprüfung. Lange Audiodateien benötigen zusätzlichen Speicher.

Bei manueller Installation außerdem `requirements-stt.txt` installieren und das
Modell bereitstellen:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-stt.txt
.\.venv\Scripts\python.exe -c "from faster_whisper.utils import download_model; download_model('base', output_dir='.models/whisper-base')"
```

## Entwicklung und Prüfungen

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
powershell -NoProfile -File tests\test_start.ps1
```

Die Tests prüfen echte PDF-Extraktion und WAV-Dateien, simulieren TTS sowie
OCR-Fehler und prüfen, dass veraltete Job-Ereignisse keine neue Lesesitzung verändern.
PDF/OCR/TTS laufen in einem eigenen Prozess. Die Oberfläche verarbeitet nur
Nachrichten und greift nicht aus einem Hintergrundthread auf Tkinter zu.

Für die vollständige Testsuite einschließlich Spracherkennung zusätzlich die
STT-Pakete installieren. Ein reales Whisper-Modell wird für die Unit-Tests nicht
benötigt. Die Mikrofonprüfung in den Tests verwendet simulierte Audiodaten.
