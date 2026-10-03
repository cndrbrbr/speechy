"""Prepare local dependencies/assets and start Speechy. Invoked by start.bat."""
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VOICES = {'Deutsch': 'de_DE-thorsten-medium', 'Englisch': 'en_US-lessac-medium'}


def required_packages_ready():
    requirements = {'PyMuPDF': (1, 24), 'pyttsx3': (2, 98), 'piper-tts': (1, 3), 'imageio-ffmpeg': (0, 6),
                    'faster-whisper': (1, 1), 'sounddevice': (0, 5), 'av': (14, 0)}
    upper = {'PyMuPDF': 2, 'pyttsx3': 3, 'piper-tts': 2, 'imageio-ffmpeg': 1, 'faster-whisper': 2, 'sounddevice': 1, 'av': 17}
    for name, lower in requirements.items():
        try:
            version = importlib.metadata.version(name)
            parts = tuple(int(part) for part in version.split('.')[:2])
        except (importlib.metadata.PackageNotFoundError, ValueError):
            return False
        if parts < lower or parts[0] >= upper[name]:
            return False
    # Catch broken installations as well as missing distributions.
    result = subprocess.run([sys.executable, '-I', '-c',
                             'from pymupdf import open; from pyttsx3 import init; from piper import PiperVoice; from imageio_ffmpeg import get_ffmpeg_exe; from faster_whisper import WhisperModel; from sounddevice import RawInputStream'], capture_output=True)
    return result.returncode == 0


def download_atomic(url, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.download')
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as target:
            shutil.copyfileobj(response, target)
        if not temporary.stat().st_size:
            raise RuntimeError(f'Leerer Download: {url}')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def asset_paths():
    return ROOT / 'voices', ROOT / 'tessdata', ROOT / '.runtime' / 'bin'


def test_ocr(tessdata, language):
    import pymupdf
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), 'Speechy OCR test', fontsize=20)
        textpage = page.get_textpage_ocr(language=language, dpi=150, full=True, tessdata=str(tessdata))
        if not page.get_text(textpage=textpage).strip():
            raise RuntimeError(f'OCR-Selbsttest fehlgeschlagen: {language}')


def prepare_assets(check_only=False):
    voices_dir, tessdata, bin_dir = asset_paths()
    from piper import PiperVoice
    from piper.download_voices import download_voice
    for model in VOICES.values():
        path = voices_dir / (model + '.onnx')
        valid = False
        if path.is_file() and Path(str(path) + '.json').is_file():
            try:
                voice = PiperVoice.load(str(path))
                del voice
                valid = True
            except Exception:
                pass
        if not valid:
            if check_only:
                raise RuntimeError(f'Piper-Modell fehlt oder ist ungueltig: {model}')
            print(f'Lade Piper-Stimme: {model}', flush=True)
            voices_dir.mkdir(exist_ok=True)
            # Stage downloads so interrupted files are never treated as completed models.
            with tempfile.TemporaryDirectory(dir=voices_dir, prefix='download-') as staging:
                download_voice(model, Path(staging))
                voice = PiperVoice.load(str(Path(staging) / (model + '.onnx')))
                del voice
                for suffix in ('.onnx', '.onnx.json'):
                    os.replace(Path(staging) / (model + suffix), voices_dir / (model + suffix))
    for language in ('deu', 'eng'):
        path = tessdata / (language + '.traineddata')
        url = f'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/{language}.traineddata'
        downloaded = False
        if not path.is_file() or path.stat().st_size < 1024:
            if check_only:
                raise RuntimeError(f'OCR-Sprachdaten fehlen: {language}')
            print(f'Lade OCR-Sprachdaten: {language}', flush=True)
            download_atomic(url, path)
            downloaded = True
        try:
            test_ocr(tessdata, language)
        except Exception:
            if check_only or downloaded:
                raise
            print(f'Erneuere ungueltige OCR-Sprachdaten: {language}', flush=True)
            download_atomic(url, path)
            test_ocr(tessdata, language)
    import imageio_ffmpeg
    supplied_ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
    ffmpeg = bin_dir / 'ffmpeg.exe'
    if not ffmpeg.exists() or ffmpeg.stat().st_size != supplied_ffmpeg.stat().st_size:
        if check_only:
            raise RuntimeError('Lokale ffmpeg.exe fehlt oder ist ungueltig.')
        bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(supplied_ffmpeg, ffmpeg)
    result = subprocess.run([str(ffmpeg), '-hide_banner', '-encoders'], capture_output=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or b'libmp3lame' not in result.stdout:
        raise RuntimeError('ffmpeg-Selbsttest oder MP3-Encoder fehlt.')
    return voices_dir, tessdata, bin_dir


def prepare_stt_assets(check_only=False):
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model
    model_dir = ROOT / '.models' / 'whisper-base'
    try:
        if not (model_dir / 'model.bin').is_file() or not (model_dir / 'tokenizer.json').is_file():
            raise RuntimeError('Whisper-Modell fehlt.')
        model = WhisperModel(str(model_dir), device='cpu', compute_type='int8', local_files_only=True)
        del model
        return model_dir
    except Exception as exc:
        if check_only:
            raise RuntimeError('Whisper-Modell fehlt oder ist ungueltig.') from exc
    print('Lade lokales Whisper-Modell fuer Deutsch/Englisch ...', flush=True)
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    os.environ['HF_HOME'] = str(model_dir.parent / 'hf-cache')
    with tempfile.TemporaryDirectory(dir=model_dir.parent, prefix='whisper-download-') as staging:
        download_model('base', output_dir=staging, cache_dir=str(model_dir.parent / 'hf-cache'))
        model = WhisperModel(staging, device='cpu', compute_type='int8', local_files_only=True)
        del model
        model_dir.mkdir(exist_ok=True)
        for path in Path(staging).iterdir():
            if path.is_file():
                os.replace(path, model_dir / path.name)
    return model_dir


def main():
    check_only = '--check-only' in sys.argv
    if not required_packages_ready():
        if check_only:
            raise RuntimeError('Python-Pakete fehlen oder sind inkompatibel.')
        print('Installiere Python-Pakete fuer Vorlesen und Spracherkennung ...', flush=True)
        subprocess.run([sys.executable, '-I', '-m', 'pip', 'install', '-r', str(ROOT / 'requirements-start.txt')], check=True)
        if not required_packages_ready():
            raise RuntimeError('Paketpruefung nach Installation fehlgeschlagen.')
    voices, tessdata, bin_dir = prepare_assets(check_only)
    prepare_stt_assets(check_only)
    print('PDF, Piper, OCR, MP3 und Spracherkennung: bereit.', flush=True)
    if check_only:
        return
    os.environ['PATH'] = str(bin_dir) + os.pathsep + os.environ.get('PATH', '')
    os.environ['TESSDATA_PREFIX'] = str(tessdata)
    sys.path.insert(0, str(ROOT / 'text2speech'))
    import reader1
    import tkinter as tk
    root = tk.Tk()
    app = reader1.PDFReaderApp(root)
    app.backend.set('Piper')
    for language, model in VOICES.items():
        app.models[language].set(str(voices / (model + '.onnx')))
    app.tessdata.set(str(tessdata))
    app.ocr.set(True)
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Fehler bei Einrichtung oder Start: {exc}', file=sys.stderr)
        sys.exit(1)
