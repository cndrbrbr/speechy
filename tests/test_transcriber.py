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

    def test_live_snapshots_replace_partial_text_and_keep_completed_chunks(self):
        condition = threading.Condition()
        state = {'frames': 2000, 'closed': False, 'error': None, 'stop': threading.Event()}
        lengths = []
        words = iter(['Hallo', 'Hallo Welt', 'Hallo Welt fertig', 'Nächster Satz'])
        next_frames = iter([4000, 8000, 10000, None])
        def recognize(path, **kwargs):
            with wave.open(path) as audio:
                lengths.append(audio.getnframes())
            text = next(words)
            with condition:
                amount = next(next_frames)
                if amount is None:
                    state['closed'] = True
                else:
                    state['frames'] = amount
                condition.notify_all()
            return iter([types.SimpleNamespace(text=text)]), None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.wav'
            with wave.open(str(path), 'wb') as audio:
                audio.setparams((1, 2, 1000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\x01\x00' * 10000)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                stt.preview_recording({'audio': str(path), 'language': 'Deutsch'},
                                     types.SimpleNamespace(transcribe=recognize), 1000, condition, state)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(lengths, [2000, 4000, 8000, 2000])
            self.assertEqual([event['text'] for event in events],
                             ['Hallo', 'Hallo Welt', 'Hallo Welt fertig', 'Hallo Welt fertig\nNächster Satz'])
            self.assertTrue(all(not event['final'] for event in events))
            self.assertIsNone(state['error'])

    def test_live_final_pass_emits_one_replacement(self):
        model = Mock()
        model.transcribe.return_value = (iter([types.SimpleNamespace(text=' Der fertige Text. ', end=2)]),
                                         types.SimpleNamespace(language='de'))
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'recording.wav'
            audio.write_bytes(b'audio')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                stt.transcribe_audio({'audio': str(audio), 'language': 'Deutsch'}, model=model, replace_preview=True)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual([event for event in events if event['kind'] == 'transcript'],
                             [{'kind': 'transcript', 'text': 'Der fertige Text.', 'final': True}])
            self.assertFalse(any(event['kind'] == 'segment' for event in events))
            self.assertEqual(events[-1]['kind'], 'done')

    def test_live_gui_replaces_preview_without_starting_another_transcription(self):
        panel = stt.TranscriberPanel.__new__(stt.TranscriberPanel)
        panel.root = Mock()
        panel.closed = False
        panel.text = Mock()
        panel.status = Mock()
        panel.source = Mock()
        panel.update_controls = Mock()
        panel.start_transcription = Mock()
        panel.generation = 3
        panel.job = {'mode': 'record', 'recorded': False, 'done': False,
                     'process': Mock(), 'config': {'audio': 'recording.wav', 'live': True}}
        panel.job['process'].wait.return_value = 0
        panel.events = queue.Queue()
        for event in [{'kind': 'transcript', 'text': 'Hallo', 'final': False},
                      {'kind': 'transcript', 'text': 'Hallo Welt', 'final': False},
                      {'kind': 'recorded', 'seconds': 3},
                      {'kind': 'transcript', 'text': 'Hallo Welt!', 'final': True}, {'kind': 'done'}]:
            panel.events.put((3, json.dumps(event)))
        panel.events.put((3, None))
        panel.poll()
        self.assertEqual(panel.text.delete.call_count, 3)
        self.assertEqual([call.args for call in panel.text.insert.call_args_list],
                         [('end', 'Hallo\n'), ('end', 'Hallo Welt\n'), ('end', 'Hallo Welt!\n')])
        panel.start_transcription.assert_not_called()
        self.assertIsNone(panel.job)
        self.assertFalse(panel.saved)

    def test_preview_failure_stops_capture(self):
        condition = threading.Condition()
        stop = threading.Event()
        state = {'frames': 2000, 'closed': False, 'error': None, 'stop': stop}
        stt.preview_recording({'audio': 'missing.wav', 'language': 'Deutsch'}, Mock(), 1000, condition, state)
        self.assertTrue(stop.is_set())
        self.assertIsInstance(state['error'], FileNotFoundError)

    def test_capture_continues_while_live_inference_is_busy(self):
        stop = threading.Event()
        entered = threading.Event()
        release = threading.Event()
        reads = []
        def recognize(path, **kwargs):
            if Path(path).name == 'chunk.wav':
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('capture blocked by inference')
            return iter([types.SimpleNamespace(text='Test', end=2.1)]), types.SimpleNamespace(language='de')
        def read(frames):
            reads.append(frames)
            if len(reads) == 21:
                self.assertTrue(entered.wait(3), 'no preview during capture')
                self.assertFalse(release.is_set())
                stop.set()
                release.set()
            return b'\x01\x00' * frames, False
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.read.side_effect = read
        sd = types.SimpleNamespace(query_devices=Mock(return_value={'default_samplerate': 1000}),
                                   RawInputStream=Mock(return_value=stream))
        model = types.SimpleNamespace(transcribe=recognize)
        with tempfile.TemporaryDirectory() as directory:
            config = {'audio': str(Path(directory) / 'input.wav'), 'live': True, 'language': 'Deutsch'}
            with patch.dict('sys.modules', {'sounddevice': sd}), patch.object(stt, 'load_model', return_value=model), contextlib.redirect_stdout(io.StringIO()):
                stt.record_audio(config, stop_event=stop)
            self.assertEqual(len(reads), 21)


if __name__ == '__main__':
    unittest.main()
