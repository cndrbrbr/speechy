"""Offline PDF reader. Tk owns the UI; a separate process owns PDF/OCR/TTS."""
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import wave

import pymupdf as fitz

APP_TITLE = "Speechy – Text und Sprache"
STATE_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Speechy" / "state.json"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def clean_text(text):
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\r?\n(?=\w)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def split_text(text, max_chars=500):
    if max_chars < 1:
        raise ValueError("max_chars muss positiv sein")
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?;:])\s+", text.strip()):
        if not sentence:
            continue
        if len(current) + len(sentence) + bool(current) <= max_chars:
            current = (current + " " + sentence).strip()
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(sentence) > max_chars:
            cut = sentence.rfind(" ", 0, max_chars + 1)
            cut = cut if cut > 0 else max_chars
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        current = sentence
    if current:
        chunks.append(current)
    return chunks


def load_state(path=STATE_FILE):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def saved_page(data, pdf_path, page_count):
    # Accept the original reader's integer bookmark format as well.
    value = data.get(str(Path(pdf_path).resolve()), 0)
    if isinstance(value, dict):
        value = value.get("page", 0)
    try:
        return max(0, min(int(value), page_count - 1))
    except (TypeError, ValueError):
        return 0


def choose_voice(engine, language):
    preferred = ("german", "de-de", "de_de", "deu", "hedwig", "katja", "stefan") if language == "Deutsch" else (
        "english", "en-us", "en-gb", "en_us", "en_gb", "zira", "david", "mark")
    for voice in engine.getProperty("voices"):
        label = f"{voice.name} {voice.id} {voice.languages}".lower()
        if any(term in label for term in preferred):
            engine.setProperty("voice", voice.id)
            return
    raise RuntimeError(f"Keine passende Windows-Stimme für {language} gefunden. Sprachstimme installieren oder Piper wählen.")


def emit(kind, **values):
    print(json.dumps({"kind": kind, **values}, ensure_ascii=True), flush=True)


def extract_text(page, config):
    text = clean_text(page.get_text("text", sort=True))
    if not text and config["ocr"]:
        emit("status", text=f"OCR auf Seite {page.number + 1} …")
        kwargs = {"language": "deu" if config["language"] == "Deutsch" else "eng",
                  "dpi": 200, "full": True}
        if config["tessdata"]:
            kwargs["tessdata"] = config["tessdata"]
        try:
            textpage = page.get_textpage_ocr(**kwargs)
            text = clean_text(page.get_text("text", textpage=textpage, sort=True))
        except Exception as exc:
            raise RuntimeError("OCR fehlgeschlagen. Tesseract-Sprachdaten (deu/eng) installieren und den tessdata-Ordner auswählen. " + str(exc)) from exc
    return text


def append_wav(destination, source):
    with wave.open(str(source), "rb") as part:
        params = (part.getnchannels(), part.getsampwidth(), part.getframerate())
        if destination.getnframes() == 0:
            destination.setnchannels(params[0])
            destination.setsampwidth(params[1])
            destination.setframerate(params[2])
        elif params != (destination.getnchannels(), destination.getsampwidth(), destination.getframerate()):
            raise RuntimeError("Audioformate stimmen nicht überein.")
        while True:
            frames = part.readframes(65536)
            if not frames:
                break
            destination.writeframes(frames)


def run_job(config):
    """No Tk calls here. Cancellation kills this process, including active audio."""
    export = bool(config["export"])
    if config["backend"] == "Piper":
        from piper import PiperVoice, SynthesisConfig
        voice = PiperVoice.load(config["model"])
        synthesis = SynthesisConfig(length_scale=165 / config["rate"])
        engine = None
    else:
        import pyttsx3
        try:
            engine = pyttsx3.init()
        except Exception as exc:
            raise RuntimeError("Windows-Sprachausgabe konnte nicht gestartet werden. SAPI-Stimmen prüfen oder Piper wählen. " + str(exc)) from exc
        engine.setProperty("rate", config["rate"])
        choose_voice(engine, config["language"])
    workdir = Path(config["workdir"])
    combined_path = workdir / "complete.wav"
    combined = None
    found_text = False
    try:
        with fitz.open(config["pdf"]) as doc:
            for number in range(config["page"], len(doc)):
                text = extract_text(doc[number], config)
                emit("page", page=number, text=text)
                if not text:
                    emit("status", text=f"Seite {number + 1}: Kein Text; übersprungen. Bei Scans OCR aktivieren.")
                    continue
                found_text = True
                chunks = split_text(text)
                first = config["chunk"] if number == config["page"] else 0
                for index in range(first, len(chunks)):
                    emit("chunk", page=number, chunk=index)
                    emit("status", text=f"{'Exportiere' if export else 'Lese'} Seite {number + 1}, Textblock {index + 1}/{len(chunks)} …")
                    chunk_path = workdir / "chunk.wav"
                    if engine is None:
                        with wave.open(str(chunk_path), "wb") as audio:
                            voice.synthesize_wav(chunks[index], audio, syn_config=synthesis)
                    elif export:
                        if chunk_path.exists():
                            chunk_path.unlink()
                        engine.save_to_file(chunks[index], str(chunk_path))
                        engine.runAndWait()
                    else:
                        engine.say(chunks[index])
                        engine.runAndWait()
                    if export:
                        if combined is None:
                            combined = wave.open(str(combined_path), "wb")
                        append_wav(combined, chunk_path)
                    elif engine is None:
                        import winsound
                        winsound.PlaySound(str(chunk_path), winsound.SND_FILENAME)
                    emit("position", page=number, chunk=index + 1)
        if not found_text:
            raise RuntimeError("Kein lesbarer Text gefunden. Bei gescannten PDFs OCR aktivieren.")
    finally:
        if combined is not None:
            combined.close()
        if engine is not None:
            engine.stop()
    if export and config["export"] == ".mp3":
        emit("status", text="Konvertiere MP3 …")
        result = subprocess.run([config["ffmpeg"], "-nostdin", "-y", "-i", str(combined_path),
                                 "-codec:a", "libmp3lame", "-q:a", "2", str(workdir / "complete.mp3")],
                                capture_output=True, creationflags=NO_WINDOW)
        if result.returncode:
            raise RuntimeError("ffmpeg: " + result.stderr.decode("utf-8", errors="replace")[-2000:])
    emit("done", text="Export abgeschlossen." if export else "Ende des Dokuments erreicht.")


class PDFReaderApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("920x650")
        root.minsize(820, 580)
        self.doc = None
        self.pdf_path = None
        self.current_page = 0
        self.chunk = 0
        self.job = None
        self.paused = False
        self.generation = 0
        self.events = queue.Queue()
        self.language = tk.StringVar(value="Deutsch")
        self.backend = tk.StringVar(value="Windows")
        self.rate = tk.DoubleVar(value=165)
        self.ocr = tk.BooleanVar(value=False)
        self.models = {lang: tk.StringVar() for lang in ("Deutsch", "Englisch")}
        self.tessdata = tk.StringVar(value=os.environ.get("TESSDATA_PREFIX", ""))
        self.status = tk.StringVar(value="Bitte eine PDF öffnen.")
        self.page_info = tk.StringVar(value="Seite – / –")
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(80, self.poll)

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)
        pdf_tab = ttk.Frame(notebook)
        speech_tab = ttk.Frame(notebook)
        notebook.add(pdf_tab, text="Text vorlesen")
        notebook.add(speech_tab, text="Sprache in Text")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from speech2text.transcriber import TranscriberPanel
        self.transcriber = TranscriberPanel(speech_tab, before_record=self.stop_reading)
        frame = ttk.Frame(pdf_tab, padding=14)
        frame.pack(fill="both", expand=True)
        top = ttk.Frame(frame)
        top.pack(fill="x")
        ttk.Button(top, text="PDF öffnen", command=self.open_pdf).pack(side="left")
        for label, variable, values in (("Sprache", self.language, ["Deutsch", "Englisch"]),
                                         ("Stimme", self.backend, ["Windows", "Piper"])):
            ttk.Label(top, text=label + ":").pack(side="left", padx=(16, 5))
            box = ttk.Combobox(top, textvariable=variable, values=values, state="readonly", width=12)
            box.pack(side="left")
            box.bind("<<ComboboxSelected>>", self.settings_changed)
        ttk.Label(top, text="Tempo:").pack(side="left", padx=(16, 5))
        ttk.Scale(top, from_=100, to=240, variable=self.rate, length=140).pack(side="left")
        settings = ttk.LabelFrame(frame, text="Optionale lokale Modelle und OCR", padding=8)
        settings.pack(fill="x", pady=12)
        for row, language in enumerate(self.models):
            ttk.Label(settings, text=f"Piper {language}:").grid(row=row, column=0, sticky="w")
            ttk.Entry(settings, textvariable=self.models[language]).grid(row=row, column=1, sticky="ew", padx=6)
            ttk.Button(settings, text="ONNX wählen", command=lambda lang=language: self.select_model(lang)).grid(row=row, column=2)
        ttk.Checkbutton(settings, text="OCR bei Seiten ohne Text", variable=self.ocr, command=self.settings_changed).grid(row=2, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.tessdata).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Button(settings, text="tessdata wählen", command=self.select_tessdata).grid(row=2, column=2)
        settings.columnconfigure(1, weight=1)
        controls = ttk.Frame(frame)
        controls.pack(fill="x")
        for text, command in (("<< Seite", lambda: self.change_page(-1)), ("Start", self.start_reading),
                              ("Pause / Weiter", self.toggle_pause), ("Stop", self.stop_reading),
                              ("Seite >>", lambda: self.change_page(1)), ("WAV / MP3 exportieren", self.export_audio)):
            ttk.Button(controls, text=text, command=command).pack(side="left", padx=3)
        ttk.Label(frame, textvariable=self.page_info, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(12, 4))
        ttk.Label(frame, textvariable=self.status, wraplength=850).pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text="Textvorschau (OCR-Ergebnis erscheint beim Vorlesen):").pack(anchor="w")
        self.preview = tk.Text(frame, wrap="word", height=14, state="disabled")
        self.preview.pack(fill="both", expand=True)

    def settings_changed(self, event=None):
        self.stop_reading()

    def select_model(self, language):
        path = filedialog.askopenfilename(filetypes=[("Piper-Modell", "*.onnx")])
        if path:
            self.stop_reading()
            self.models[language].set(path)

    def select_tessdata(self):
        path = filedialog.askdirectory(title="Ordner mit deu.traineddata / eng.traineddata")
        if path:
            self.stop_reading()
            self.tessdata.set(path)

    def open_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        candidate = None
        try:
            candidate = fitz.open(path)
            if not candidate.is_pdf or candidate.needs_pass or not len(candidate):
                raise ValueError("Bitte eine unverschlüsselte PDF mit mindestens einer Seite öffnen.")
            self.stop_reading()
            if self.doc is not None:
                self.doc.close()
            self.doc = candidate
            self.pdf_path = str(Path(path).resolve())
            self.current_page = saved_page(load_state(), path, len(candidate))
            self.chunk = 0
            self.show_page()
            self.status.set(f"Geladen: {Path(path).name}")
        except Exception as exc:
            if candidate is not None and candidate is not self.doc:
                candidate.close()
            messagebox.showerror(APP_TITLE, str(exc))

    def show_page(self, text=None):
        if self.doc is None:
            return
        if text is None:
            text = clean_text(self.doc[self.current_page].get_text("text", sort=True))
        self.page_info.set(f"Seite {self.current_page + 1} / {len(self.doc)}")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text or "[Kein Text. Bei gescannten Seiten OCR aktivieren.]")
        self.preview.configure(state="disabled")
        self.save_state()

    def save_state(self):
        if not self.pdf_path:
            return
        data = load_state()
        data[self.pdf_path] = {"page": self.current_page}
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = STATE_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(STATE_FILE)
        except OSError:
            self.status.set("Leseposition konnte nicht gespeichert werden.")

    def job_config(self, export=""):
        if self.doc is None:
            raise ValueError("Bitte zuerst eine PDF öffnen.")
        model = self.models[self.language.get()].get().strip()
        if self.backend.get() == "Piper":
            if not Path(model).is_file() or not Path(model + ".json").is_file():
                raise ValueError("Piper benötigt eine .onnx-Datei und die zugehörige .onnx.json im selben Ordner.")
        ffmpeg = shutil.which("ffmpeg")
        if export == ".mp3" and not ffmpeg:
            raise ValueError("Für MP3 ffmpeg installieren und zu PATH hinzufügen. WAV funktioniert ohne ffmpeg.")
        return {"pdf": self.pdf_path, "page": 0 if export else self.current_page,
                "chunk": 0 if export else self.chunk, "backend": self.backend.get(),
                "language": self.language.get(), "model": model, "rate": int(self.rate.get()),
                "ocr": self.ocr.get(), "tessdata": self.tessdata.get().strip(),
                "export": export, "ffmpeg": ffmpeg}

    def start_reading(self):
        if self.transcriber.recording:
            self.status.set("Bitte zuerst die Mikrofonaufnahme beenden.")
            return
        if self.job is not None:
            return
        self.launch_job()

    def export_audio(self):
        if self.doc is None:
            messagebox.showinfo(APP_TITLE, "Bitte zuerst eine PDF öffnen.")
            return
        target = filedialog.asksaveasfilename(title="Gesamte PDF als Audio exportieren", defaultextension=".wav",
                                            filetypes=[("WAV", "*.wav"), ("MP3", "*.mp3")])
        if target:
            if Path(target).resolve() == Path(self.pdf_path).resolve():
                messagebox.showerror(APP_TITLE, "Die geöffnete PDF darf nicht überschrieben werden.")
                return
            self.stop_reading()
            self.launch_job(target)

    def launch_job(self, target=None):
        temporary = None
        try:
            extension = Path(target).suffix.lower() if target else ""
            if target and extension not in (".wav", ".mp3"):
                raise ValueError("Bitte WAV oder MP3 als Dateiendung wählen.")
            config = self.job_config(extension)
            temporary = tempfile.TemporaryDirectory(prefix="speechy-", dir=str(Path(target).parent) if target else None)
            config["workdir"] = temporary.name
            jobfile = Path(temporary.name) / "job.json"
            jobfile.write_text(json.dumps(config), encoding="utf-8")
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--job", str(jobfile)],
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            self.generation += 1
            generation = self.generation
            self.job = {"process": process, "temporary": temporary, "target": target, "extension": extension, "done": False}
            self.paused = False
            self.status.set("Starte lokale Sprachausgabe …")
            def collect():
                with process.stdout:
                    for line in process.stdout:
                        self.events.put((generation, line))
                self.events.put((generation, None))
            threading.Thread(target=collect, daemon=True).start()
        except Exception as exc:
            if temporary is not None:
                temporary.cleanup()
            messagebox.showerror(APP_TITLE, str(exc))

    def poll(self):
        for _ in range(100):
            try:
                generation, line = self.events.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation or self.job is None:
                continue
            if line is None:
                job = self.job
                code = job["process"].wait()
                self.job = None
                try:
                    if code == 0 and job["done"]:
                        if job["target"]:
                            source = Path(job["temporary"].name) / ("complete" + job["extension"])
                            os.replace(source, job["target"])
                            self.status.set(f"Gespeichert: {job['target']}")
                        else:
                            self.chunk = 0
                            self.status.set("Ende des Dokuments erreicht.")
                    elif code and not job.get("error"):
                        self.status.set(f"Sprachausgabe fehlgeschlagen (Code {code}). Abhängigkeiten prüfen.")
                except Exception as exc:
                    messagebox.showerror(APP_TITLE, str(exc))
                finally:
                    job["temporary"].cleanup()
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("kind") if isinstance(event, dict) else None
            if kind == "status":
                self.status.set(event["text"])
            elif kind == "page" and not self.job["target"]:
                self.current_page = event["page"]
                self.chunk = 0
                self.show_page(event["text"])
            elif kind in ("chunk", "position") and not self.job["target"]:
                self.current_page, self.chunk = event["page"], event["chunk"]
            elif kind == "done":
                self.job["done"] = True
            elif kind == "error":
                self.job["error"] = True
                self.status.set(event["text"])
                messagebox.showerror(APP_TITLE, event["text"])
        self.root.after(80, self.poll)

    def cancel_job(self):
        self.generation += 1
        job, self.job = self.job, None
        if job is None:
            return
        process = job["process"]
        if process.poll() is None:
            # Only kill the process tree belonging to this reader job (including ffmpeg).
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            if process.poll() is None:
                process.kill()
        process.wait()
        job["temporary"].cleanup()

    def toggle_pause(self):
        if self.job is not None:
            if self.job["target"]:
                self.status.set("Export kann mit Stop abgebrochen werden.")
                return
            self.cancel_job()
            self.paused = True
            self.status.set("Pause. Weiter wiederholt den unterbrochenen Textblock.")
        elif self.paused:
            self.start_reading()

    def stop_reading(self):
        self.cancel_job()
        self.paused = False
        self.chunk = 0
        self.status.set("Gestoppt. Start beginnt am Anfang der aktuellen Seite.")

    def change_page(self, direction):
        if self.doc is not None:
            self.stop_reading()
            self.current_page = max(0, min(self.current_page + direction, len(self.doc) - 1))
            self.show_page()

    def on_close(self):
        if not self.transcriber.can_close():
            return
        self.transcriber.close()
        self.cancel_job()
        self.save_state()
        if self.doc is not None:
            self.doc.close()
        self.root.destroy()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--job":
        try:
            run_job(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
        except Exception as exc:
            emit("error", text=str(exc))
            sys.exit(1)
    else:
        if os.name != "nt":
            raise SystemExit("Die Sprachausgabe dieser Anwendung benötigt Windows.")
        root = tk.Tk()
        app = PDFReaderApp(root)
        root.mainloop()
