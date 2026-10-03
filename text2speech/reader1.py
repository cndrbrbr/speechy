import os
import re
import json
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz  # PyMuPDF
import pyttsx3

APP_TITLE = "PDF Vorleser"
STATE_FILE = os.path.join(os.path.expanduser("~"), ".pdf_vorleser_state.json")


def clean_text(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"-\n(?=\w)", "", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def split_text(text: str, max_chars: int = 700):
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?;:])\s+", text)
    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            if len(sentence) <= max_chars:
                current = sentence
            else:
                # Fallback: split very long sentences
                for i in range(0, len(sentence), max_chars):
                    part = sentence[i:i + max_chars].strip()
                    if part:
                        chunks.append(part)
                current = ""
    if current:
        chunks.append(current)
    return chunks


class PDFReaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("760x420")
        self.root.minsize(720, 380)

        self.doc = None
        self.pdf_path = None
        self.current_page = 0
        self.is_speaking = False
        self.is_paused = False
        self.stop_requested = False
        self.speech_thread = None
        self.engine = None

        self.language = tk.StringVar(value="Deutsch")
        self.rate = tk.IntVar(value=165)
        self.status = tk.StringVar(value="Bitte eine PDF-Datei öffnen.")
        self.page_info = tk.StringVar(value="Seite - / -")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=14)
        frm.pack(fill="both", expand=True)

        top = ttk.Frame(frm)
        top.pack(fill="x")

        ttk.Button(top, text="PDF öffnen", command=self.open_pdf).pack(side="left")

        ttk.Label(top, text="Sprache:").pack(side="left", padx=(20, 6))
        lang_box = ttk.Combobox(
            top,
            textvariable=self.language,
            values=["Deutsch", "Englisch"],
            state="readonly",
            width=12,
        )
        lang_box.pack(side="left")

        ttk.Label(top, text="Geschwindigkeit:").pack(side="left", padx=(20, 6))
        scale = ttk.Scale(
            top,
            from_=100,
            to=240,
            variable=self.rate,
            orient="horizontal",
            length=180,
        )
        scale.pack(side="left")

        controls = ttk.Frame(frm)
        controls.pack(fill="x", pady=(20, 10))

        ttk.Button(controls, text="<< Vorherige Seite", command=self.prev_page).pack(side="left", padx=4)
        ttk.Button(controls, text="Start", command=self.start_reading).pack(side="left", padx=4)
        ttk.Button(controls, text="Pause / Weiter", command=self.toggle_pause).pack(side="left", padx=4)
        ttk.Button(controls, text="Stop", command=self.stop_reading).pack(side="left", padx=4)
        ttk.Button(controls, text="Nächste Seite >>", command=self.next_page).pack(side="left", padx=4)

        ttk.Label(frm, textvariable=self.page_info, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(10, 4))
        ttk.Label(frm, textvariable=self.status, wraplength=700).pack(anchor="w", pady=(0, 8))

        ttk.Label(frm, text="Vorschau der aktuellen Seite:").pack(anchor="w")
        self.preview = tk.Text(frm, height=12, wrap="word")
        self.preview.pack(fill="both", expand=True, pady=(4, 0))
        self.preview.configure(state="disabled")

    def open_pdf(self):
        path = filedialog.askopenfilename(
            title="PDF auswählen",
            filetypes=[("PDF-Dateien", "*.pdf"), ("Alle Dateien", "*.*")]
        )
        if not path:
            return

        try:
            self.stop_reading()
            if self.doc:
                self.doc.close()

            self.doc = fitz.open(path)
            self.pdf_path = path
            self.current_page = self.load_saved_page(path)
            self.current_page = max(0, min(self.current_page, len(self.doc) - 1))
            self.update_page_display()

            self.status.set(f"Geladen: {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Fehler", f"PDF konnte nicht geöffnet werden:\n{exc}")

    def get_page_text(self, page_number):
        if not self.doc:
            return ""
        text = self.doc[page_number].get_text("text")
        return clean_text(text)

    def update_page_display(self):
        if not self.doc:
            self.page_info.set("Seite - / -")
            return

        text = self.get_page_text(self.current_page)
        self.page_info.set(f"Seite {self.current_page + 1} / {len(self.doc)}")

        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text if text else "[Kein auslesbarer Text auf dieser Seite]")
        self.preview.configure(state="disabled")

        self.save_state()

    def choose_voice(self, engine):
        language = self.language.get().lower()
        voices = engine.getProperty("voices")

        preferred = []
        if language == "deutsch":
            preferred = ["german", "de-de", "de_de", "deu", "hedwig", "katja", "stefan"]
        else:
            preferred = ["english", "en-us", "en-gb", "en_us", "en_gb", "zira", "david", "mark"]

        for voice in voices:
            haystack = " ".join([
                str(getattr(voice, "name", "")),
                str(getattr(voice, "id", "")),
                str(getattr(voice, "languages", "")),
            ]).lower()

            if any(term in haystack for term in preferred):
                engine.setProperty("voice", voice.id)
                return

    def start_reading(self):
        if not self.doc:
            messagebox.showinfo(APP_TITLE, "Bitte zuerst eine PDF-Datei öffnen.")
            return

        if self.is_speaking:
            self.is_paused = False
            self.status.set("Vorlesen wird fortgesetzt.")
            return

        self.stop_requested = False
        self.is_paused = False
        self.speech_thread = threading.Thread(target=self._speech_worker, daemon=True)
        self.speech_thread.start()

    def _speech_worker(self):
        self.is_speaking = True

        try:
            while self.doc and self.current_page < len(self.doc) and not self.stop_requested:
                page_num = self.current_page
                text = self.get_page_text(page_num)

                if not text:
                    self.root.after(
                        0,
                        lambda p=page_num: self.status.set(
                            f"Seite {p + 1}: Kein Text gefunden. "
                            "Bei gescannten PDFs ist OCR nötig."
                        )
                    )
                    if page_num < len(self.doc) - 1:
                        self.current_page += 1
                        self.root.after(0, self.update_page_display)
                        continue
                    break

                chunks = split_text(text)

                self.root.after(
                    0,
                    lambda p=page_num: self.status.set(f"Lese Seite {p + 1} ...")
                )

                for chunk in chunks:
                    if self.stop_requested or page_num != self.current_page:
                        break

                    while self.is_paused and not self.stop_requested:
                        time.sleep(0.1)

                    if self.stop_requested or page_num != self.current_page:
                        break

                    engine = pyttsx3.init()
                    self.engine = engine
                    engine.setProperty("rate", int(self.rate.get()))
                    self.choose_voice(engine)
                    engine.say(chunk)
                    engine.runAndWait()
                    engine.stop()
                    self.engine = None

                if self.stop_requested:
                    break

                if page_num == self.current_page:
                    if self.current_page < len(self.doc) - 1:
                        self.current_page += 1
                        self.root.after(0, self.update_page_display)
                    else:
                        self.root.after(0, lambda: self.status.set("Ende des Dokuments erreicht."))
                        break

        except Exception as exc:
            self.root.after(
                0,
                lambda: messagebox.showerror("Vorlesefehler", str(exc))
            )
        finally:
            self.is_speaking = False
            self.is_paused = False
            self.engine = None

    def toggle_pause(self):
        if not self.is_speaking:
            return

        if self.is_paused:
            self.is_paused = False
            self.status.set("Vorlesen wird fortgesetzt.")
        else:
            self.is_paused = True
            if self.engine:
                try:
                    self.engine.stop()
                except Exception:
                    pass
            self.status.set("Pause. Der aktuelle Textblock kann dabei abgebrochen werden.")

    def stop_reading(self):
        self.stop_requested = True
        self.is_paused = False
        if self.engine:
            try:
                self.engine.stop()
            except Exception:
                pass
        self.status.set("Gestoppt.")

    def prev_page(self):
        if not self.doc:
            return
        self.stop_reading()
        if self.current_page > 0:
            self.current_page -= 1
            self.update_page_display()

    def next_page(self):
        if not self.doc:
            return
        self.stop_reading()
        if self.current_page < len(self.doc) - 1:
            self.current_page += 1
            self.update_page_display()

    def load_saved_page(self, path):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return int(data.get(os.path.abspath(path), 0))
        except Exception:
            pass
        return 0

    def save_state(self):
        if not self.pdf_path:
            return
        try:
            data = {}
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data[os.path.abspath(self.pdf_path)] = self.current_page
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def on_close(self):
        self.stop_reading()
        self.save_state()
        if self.doc:
            self.doc.close()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = PDFReaderApp(root)
    root.mainloop()

