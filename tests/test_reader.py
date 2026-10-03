import contextlib
import importlib.util
import io
import json
from pathlib import Path
import queue
import tempfile
import subprocess
import sys
import types
import unittest
from unittest.mock import patch, Mock
import wave

import pymupdf as fitz

spec = importlib.util.spec_from_file_location("reader", Path(__file__).resolve().parents[1] / "text2speech" / "reader1.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class ReaderTests(unittest.TestCase):
    def test_text_and_chunks(self):
        self.assertEqual(reader.clean_text("Silben-\ntrennung\u00ad\n  Test"), "Silbentrennung Test")
        text = "Ein langer Satz ohne Satzzeichen " * 80
        chunks = reader.split_text(text, 100)
        self.assertTrue(all(0 < len(chunk) <= 100 for chunk in chunks))
        self.assertEqual(" ".join(chunks), text.strip())
        self.assertEqual(reader.split_text(""), [])
        self.assertEqual("".join(reader.split_text("x" * 230, 100)), "x" * 230)

    def test_bookmark_validation(self):
        path = str(Path("book.pdf").resolve())
        self.assertEqual(reader.saved_page({path: {"page": 999}}, path, 10), 9)
        self.assertEqual(reader.saved_page({path: "invalid"}, path, 10), 0)
        self.assertEqual(reader.saved_page({path: 3}, path, 10), 3)
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "state.json"
            broken.write_text("[]")
            self.assertEqual(reader.load_state(broken), {})

    def test_no_wrong_language_fallback(self):
        engine = Mock()
        engine.getProperty.return_value = [types.SimpleNamespace(name="English David", id="en-US", languages=["en_US"])]
        with self.assertRaisesRegex(RuntimeError, "Keine passende"):
            reader.choose_voice(engine, "Deutsch")
        reader.choose_voice(engine, "Englisch")
        engine.setProperty.assert_called_once_with("voice", "en-US")

    def test_pdf_extraction_and_ocr_failure(self):
        config = {"ocr": True, "language": "Deutsch", "tessdata": "missing"}
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), "Dies ist ein Test.")
            with patch.object(fitz.Page, "get_textpage_ocr", side_effect=AssertionError("OCR must not run")):
                self.assertEqual(reader.extract_text(page, config), "Dies ist ein Test.")
            blank = doc.new_page()
            with patch.object(fitz.Page, "get_textpage_ocr", side_effect=RuntimeError("missing deu")):
                with self.assertRaisesRegex(RuntimeError, "OCR fehlgeschlagen"):
                    reader.extract_text(blank, config)

    def test_export_real_pdf_to_valid_wav(self):
        class FakeVoice:
            def synthesize_wav(self, text, audio, syn_config):
                audio.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
                audio.writeframes(b"\x01\x00" * 100)
        piper = types.SimpleNamespace(PiperVoice=types.SimpleNamespace(load=Mock(return_value=FakeVoice())),
                                     SynthesisConfig=lambda **kwargs: kwargs)
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "sample.pdf"
            with fitz.open() as doc:
                doc.new_page().insert_text((72, 72), "Erste Seite.")
                doc.new_page()  # Empty pages are skipped.
                doc.new_page().insert_text((72, 72), "Second page.")
                doc.save(pdf)
            config = {"backend": "Piper", "model": "model.onnx", "rate": 165,
                      "export": ".wav", "workdir": directory, "pdf": str(pdf), "page": 0,
                      "chunk": 0, "ocr": False, "language": "Deutsch", "tessdata": ""}
            output = io.StringIO()
            with patch.dict("sys.modules", {"piper": piper}), contextlib.redirect_stdout(output):
                reader.run_job(config)
            with wave.open(str(Path(directory) / "complete.wav")) as audio:
                self.assertEqual(audio.getnframes(), 200)
                self.assertEqual(audio.getframerate(), 22050)
            piper.PiperVoice.load.assert_called_once()
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(events[-1]["kind"], "done")
            self.assertEqual([event["page"] for event in events if event["kind"] == "page"], [0, 1, 2])

    def test_export_rejects_incompatible_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "part.wav"
            target = Path(directory) / "all.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0\0" * 10)
            with wave.open(str(target), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0\0" * 10)
                with self.assertRaisesRegex(RuntimeError, "Audioformate"):
                    reader.append_wav(audio, source)

    def test_stale_events_do_not_change_new_session(self):
        app = reader.PDFReaderApp.__new__(reader.PDFReaderApp)
        app.root = Mock()
        app.generation = 2
        app.job = {"target": None}
        app.current_page = 5
        app.events = queue.Queue()
        app.events.put((1, json.dumps({"kind": "page", "page": 99, "text": "old"})))
        app.events.put((1, None))
        app.poll()
        self.assertEqual(app.current_page, 5)
        self.assertIsNotNone(app.job)

    def test_cancel_export_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "book.wav"
            destination.write_bytes(b"original")
            temporary = tempfile.TemporaryDirectory(dir=directory)
            (Path(temporary.name) / "complete.wav").write_bytes(b"partial")
            app = reader.PDFReaderApp.__new__(reader.PDFReaderApp)
            app.generation = 1
            process = Mock()
            process.poll.return_value = 0
            app.job = {"process": process, "temporary": temporary, "target": str(destination)}
            app.cancel_job()
            self.assertIsNone(app.job)
            self.assertFalse(Path(temporary.name).exists())
            self.assertEqual(destination.read_bytes(), b"original")

    def test_cancel_terminates_worker(self):
        app = reader.PDFReaderApp.__new__(reader.PDFReaderApp)
        app.generation = 1
        temporary = tempfile.TemporaryDirectory()
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   creationflags=reader.NO_WINDOW)
        app.job = {"process": process, "temporary": temporary, "target": None}
        try:
            app.cancel_job()
            self.assertIsNotNone(process.poll())
            self.assertFalse(Path(temporary.name).exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            temporary.cleanup()

    def test_empty_export_reports_no_text(self):
        engine = Mock()
        engine.getProperty.return_value = [types.SimpleNamespace(name="English David", id="en-US", languages=[])]
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "empty.pdf"
            with fitz.open() as doc:
                doc.new_page()
                doc.save(pdf)
            config = {"backend": "Windows", "rate": 165, "language": "Englisch", "export": ".wav",
                      "workdir": directory, "pdf": str(pdf), "page": 0, "chunk": 0, "ocr": False}
            with patch.dict("sys.modules", {"pyttsx3": types.SimpleNamespace(init=lambda: engine)}), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "Kein lesbarer Text"):
                    reader.run_job(config)
            self.assertFalse((Path(directory) / "complete.wav").exists())


if __name__ == "__main__":
    unittest.main()
