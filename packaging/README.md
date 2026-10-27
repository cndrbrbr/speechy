# App-Identität für Speechy (Mikrofonfreigabe nur für Speechy)

Unter Windows gibt es für Programme ohne Paketidentität nur einen gemeinsamen
Schalter „Desktop-Apps den Zugriff auf das Mikrofon erlauben“. Wer ihn
einschaltet, gibt das Mikrofon allen Desktop-Programmen und allen
Python-Skripten frei.

Dieses Verzeichnis enthält ein **Identitätspaket mit externem Speicherort**
(Microsoft: „Packaging with external location“). Es enthält keinen
Programmcode, nur `AppxManifest.xml` und Logos. Das Manifest verweist auf
`.speechy-venv\Scripts\pythonw.exe` im Speechy-Ordner. Wird Speechy über diese
App gestartet, läuft es mit eigener Identität und erscheint unter
**Einstellungen → Datenschutz und Sicherheit → Mikrofon → „Apps den Zugriff
auf das Mikrofon erlauben“** mit eigenem Schalter. „Desktop-Apps“ kann
ausgeschaltet bleiben.

Für die Lautsprecher gibt es in Windows keine Freigabe; Vorlesen funktioniert
mit und ohne Identität.

## Dateien

| Datei | Zweck |
| --- | --- |
| `AppxManifest.xml` | Identität `Speechy.Local`, Mikrofon-Fähigkeit, Startbefehl |
| `build-identity-package.cmd` / `.ps1` | Baut und signiert das Paket (nur Entwickler) |
| `speechy-identity.msix` | Signiertes Identitätspaket (wird committet) |
| `speechy-identity.cer` | Öffentliches Zertifikat zum Paket (wird committet) |

## Paket bauen (einmalig, Entwicklerrechner)

Voraussetzung: Windows 10 2004 oder neuer und das
[Windows SDK](https://developer.microsoft.com/windows/downloads/windows-sdk/)
(enthält `MakeAppx.exe` und `SignTool.exe`).

```cmd
packaging\build-identity-package.cmd
```

Das Skript erzeugt beim ersten Lauf ein selbstsigniertes Zertifikat
`CN=Speechy Local Identity` im Benutzerspeicher `CurrentUser\My` und verwendet
es bei jedem weiteren Lauf wieder. Danach `speechy-identity.msix` und
`speechy-identity.cer` committen. Der private Schlüssel bleibt im
Zertifikatspeicher und gehört nicht ins Repository.

Wird das Zertifikat neu erzeugt (z. B. auf einem anderen Rechner), müssen alle
Benutzer das neue Zertifikat einmal bestätigen; `start.bat` erledigt das beim
nächsten Start automatisch.

## Was start.bat damit macht

1. Einrichtung wie bisher (Pakete, Stimmen, OCR, Whisper) in der Konsole.
2. Beim ersten Mal: WinRT-Pakete aus `requirements-identity.txt` installieren,
   das Zertifikat unter „Vertrauenswürdige Personen“ des Computers eintragen
   (**einmalige Administratorabfrage**) und das Paket mit diesem Ordner als
   externem Speicherort registrieren (ohne Administratorrechte).
3. Speechy über den App-Eintrag starten. Danach gibt es auch einen
   Startmenü-Eintrag „Speechy“.

Ist das Paket nicht vorhanden, wird die Administratorabfrage abgelehnt oder
schlägt die Registrierung fehl, startet Speechy wie bisher ohne Identität und
zeigt einen Hinweis.

Die Registrierung gilt für genau diesen Ordner. Wird er verschoben oder das
Paket neu gebaut, registriert `start.bat` beim nächsten Start neu
(`.speechy-identity.json` merkt sich Ordner und Paket). Der Ordner muss auf
einem lokalen NTFS-Laufwerk liegen.

## Befehle

```cmd
start.bat                  Einrichten und mit App-Identitaet starten
start.bat -NoIdentity      Ohne App-Identitaet starten (wie bisher)
start.bat -RemoveIdentity  Registrierung entfernen
start.bat -CheckOnly       Pruefen, zeigt auch den Status der App-Identitaet
```

Das Zertifikat bleibt nach `-RemoveIdentity` eingetragen. Entfernen in einer
Eingabeaufforderung als Administrator:

```cmd
certutil -delstore TrustedPeople "Speechy Local Identity"
```

## Prüfen

- **Einstellungen → Datenschutz und Sicherheit → Mikrofon:** Unter „Apps den
  Zugriff auf das Mikrofon erlauben“ steht „Speechy“. Falls der Schalter aus
  ist, einschalten.
- Im Reiter **Sprache in Text** aufnehmen, während „Desktop-Apps den Zugriff
  auf das Mikrofon erlauben“ ausgeschaltet ist.
- Ein anderes Python-Programm (z. B. `python -c "import sounddevice as sd;
  print(sd.rec(16000, 16000, 1, blocking=True).max())"`) bekommt bei
  ausgeschaltetem „Desktop-Apps“-Schalter kein Mikrofon (Fehler oder nur Stille).
