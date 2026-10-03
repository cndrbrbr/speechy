import contextlib
import importlib.util
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch
import wave

spec = importlib.util.spec_from_file_location('transcriber', Path(__file__).resolve().parents[1] / 'speech2text/transcriber.py')
stt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stt)


class TranscriberTests(unittest.TestCase):
    def test_utf8_plain_text_saved_without_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'diktat.txt'
            stt.save_text_atomic(path, 'Grüße aus Köln.\nSecond line.\n\n')
            self.assertEqual(path.read_bytes(), 'Grüße aus Köln.\nSecond line.\n'.encode('utf-8'))
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_failed_save_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'diktat.txt'
            path.write_text('original')
            with patch.object(stt.os, 'replace', side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):
                    stt.save_text_atomic(path, 'replacement')
            self.assertEqual(path.read_text(), 'original')
            self.assertEqual(list(Path(directory).iterdir()), [path])
            with self.assertRaises(ValueError):
                stt.save_text_atomic(Path(directory) / 'wrong.wav', 'text')

    def test_transcription_is_local_and_emits_only_nonempty_text(self):
        model = Mock()
        model.transcribe.return_value = (iter([types.SimpleNamespace(text=' Hallo Welt! ', end=1.2),
                                              types.SimpleNamespace(text=' ', end=2.0)]),
                                         types.SimpleNamespace(language='de'))
        constructor = Mock(return_value=model)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'model.bin').write_bytes(b'model')
            audio = path / 'sample.wav'
            audio.write_bytes(b'audio')
            output = io.StringIO()
            with patch.dict('sys.modules', {'faster_whisper': types.SimpleNamespace(WhisperModel=constructor)}), contextlib.redirect_stdout(output):
                stt.transcribe_audio({'model': str(path), 'audio': str(audio), 'language': 'Deutsch'})
            constructor.assert_called_once_with(str(path), device='cpu', compute_type='int8', local_files_only=True)
            self.assertEqual(model.transcribe.call_args.kwargs['language'], 'de')
            self.assertTrue(model.transcribe.call_args.kwargs['vad_filter'])
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([e['text'] for e in events if e['kind'] == 'segment'], ['Hallo Welt!'])
            self.assertEqual(events[-1]['kind'], 'done')

    def test_microphone_writes_pcm_and_stops_gracefully(self):
        stop = threading.Event()
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        def read(frames):
            stop.set()
            return b'\x01\x00' * frames, False
        stream.read.side_effect = read
        sd = types.SimpleNamespace(query_devices=Mock(return_value={'default_samplerate': 48000}), RawInputStream=Mock(return_value=stream))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recording.wav'
            with patch.dict('sys.modules', {'sounddevice': sd}), contextlib.redirect_stdout(io.StringIO()):
                stt.record_audio({'audio': str(path)}, stop_event=stop)
            with wave.open(str(path)) as audio:
                self.assertEqual(audio.getframerate(), 48000)
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                self.assertEqual(audio.getnframes(), 4800)

    def test_stale_events_do_not_change_transcript(self):
        panel = stt.TranscriberPanel.__new__(stt.TranscriberPanel)
        panel.root = Mock()
        panel.closed = False
        panel.text = Mock()
        panel.generation = 2
        panel.job = {'mode': 'transcribe'}
        panel.events = queue.Queue()
        panel.events.put((1, json.dumps({'kind': 'segment', 'text': 'old text'})))
        panel.events.put((1, None))
        panel.poll()
        panel.text.insert.assert_not_called()
        self.assertIsNotNone(panel.job)

    def test_input_devices_exclude_output_only_and_do_not_record(self):
        sd = types.SimpleNamespace(query_devices=Mock(return_value=[
            {'name': 'Speakers', 'max_input_channels': 0},
            {'name': 'USB Headset', 'max_input_channels': 1}]), RawInputStream=Mock())
        output = io.StringIO()
        with patch.dict('sys.modules', {'sounddevice': sd}), contextlib.redirect_stdout(output):
            stt.list_input_devices({})
        event = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(event['devices'], [{'id': 1, 'name': 'USB Headset'}])
        sd.RawInputStream.assert_not_called()


if __name__ == '__main__':
    unittest.main()
