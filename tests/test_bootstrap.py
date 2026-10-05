import importlib.util
import io
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).resolve().parents[1] / 'scripts/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def test_download_success_replaces_file_after_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'eng.traineddata'
            target.write_bytes(b'old')
            with patch.object(bootstrap.urllib.request, 'urlopen', return_value=io.BytesIO(b'complete')):
                bootstrap.download_atomic('https://example.test/asset', target)
            self.assertEqual(target.read_bytes(), b'complete')
            self.assertFalse(target.with_suffix('.traineddata.download').exists())

    def test_failed_download_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'eng.traineddata'
            target.write_bytes(b'old')
            with patch.object(bootstrap.urllib.request, 'urlopen', return_value=io.BytesIO(b'')):
                with self.assertRaisesRegex(RuntimeError, 'Leerer Download'):
                    bootstrap.download_atomic('https://example.test/asset', target)
            self.assertEqual(target.read_bytes(), b'old')
            self.assertFalse(target.with_suffix('.traineddata.download').exists())

    def test_missing_or_out_of_range_packages_are_detected(self):
        with patch.object(bootstrap.importlib.metadata, 'version', side_effect=bootstrap.importlib.metadata.PackageNotFoundError):
            self.assertFalse(bootstrap.required_packages_ready())
        with patch.object(bootstrap.importlib.metadata, 'version', return_value='9.0'):
            self.assertFalse(bootstrap.required_packages_ready())
        versions = {'PyMuPDF': '1.28.2', 'pyttsx3': '2.99', 'piper-tts': '1.8.0', 'imageio-ffmpeg': '0.6.0',
                    'faster-whisper': '1.1.1', 'sounddevice': '0.5.1', 'av': '14.4.0'}
        with patch.object(bootstrap.importlib.metadata, 'version', side_effect=versions.__getitem__), patch.object(
                bootstrap.subprocess, 'run', return_value=types.SimpleNamespace(returncode=1)):
            self.assertFalse(bootstrap.required_packages_ready())

    def test_check_only_does_not_install_packages(self):
        with patch.object(bootstrap.sys, 'argv', ['bootstrap.py', '--check-only']), patch.object(
                bootstrap, 'required_packages_ready', return_value=False), patch.object(bootstrap.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Python-Pakete fehlen'):
                bootstrap.main()
            run.assert_not_called()

    def test_check_only_does_not_download_missing_models(self):
        downloader = Mock()
        modules = {'piper': types.SimpleNamespace(PiperVoice=Mock()),
                   'piper.download_voices': types.SimpleNamespace(download_voice=downloader)}
        with tempfile.TemporaryDirectory() as directory, patch.object(bootstrap, 'ROOT', Path(directory)), patch.dict('sys.modules', modules):
            with self.assertRaisesRegex(RuntimeError, 'Piper-Modell fehlt'):
                bootstrap.prepare_assets(check_only=True)
            downloader.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
