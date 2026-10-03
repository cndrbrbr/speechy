"""Microphone/file transcription in child processes; editable UTF-8 text output."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / '.models' / 'whisper-base'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
EMIT_LOCK = threading.Lock()


def emit(kind, **values):
    with EMIT_LOCK:
        print(json.dumps({'kind': kind, **values}, ensure_ascii=True), flush=True)


def load_model(config):
    from faster_whisper import WhisperModel
    model_path = Path(config['model'])
    if not (model_path / 'model.bin').is_file():
        raise RuntimeError('Whisper-Modell fehlt. Speechy mit start.bat einrichten.')
    emit('status', text='Lade lokale Spracherkennung …')
    return WhisperModel(str(model_path), device='cpu', compute_type='int8', local_files_only=True)


def preview_recording(config, model, rate, condition, state):
    """Read bounded snapshots from disk; inference never blocks microphone capture."""
    cursor = 0
    last_end = 0
    confirmed = []
    chunk_frames = rate * 8
    language = {'Deutsch': 'de', 'Englisch': 'en', 'Automatisch': None}[config['language']]
    try:
        with tempfile.TemporaryDirectory(dir=Path(config['audio']).parent, prefix='preview-') as directory:
            snapshot = str(Path(directory) / 'chunk.wav')
            while True:
                with condition:
                    condition.wait_for(lambda: state['closed'] or state['frames'] - last_end >= rate * 2)
                    if state['closed']:
                        return
                    end = min(state['frames'], cursor + chunk_frames)
                    # The writer flushes under the same lock before advancing frames.
                    with wave.open(config['audio'], 'rb') as source:
                        source.setpos(cursor)
                        pcm = source.readframes(end - cursor)
                with wave.open(snapshot, 'wb') as audio:
                    audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                    audio.writeframes(pcm)
                segments, _ = model.transcribe(snapshot, language=language, beam_size=1,
                                               vad_filter=True, condition_on_previous_text=False)
                current = [segment.text.strip() for segment in segments if segment.text.strip()]
                emit('transcript', text='\n'.join(confirmed + current), final=False)
                last_end = end
                if end - cursor == chunk_frames:
                    confirmed.extend(current)
                    cursor = end
    except Exception as exc:
        with condition:
            state['error'] = exc
        state['stop'].set()


def list_input_devices(config):
    import sounddevice as sd
    devices = [{'id': number, 'name': device['name']} for number, device in enumerate(sd.query_devices())
               if device['max_input_channels'] > 0]
    emit('devices', devices=devices)
    emit('done')


def save_text_atomic(path, text):
    path = Path(path)
    if path.suffix.lower() != '.txt':
        raise ValueError('Bitte eine Datei mit der Endung .txt wählen.')
    if not text.strip():
        raise ValueError('Es ist noch kein Text vorhanden.')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=path.parent, prefix='.speechy-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(text.rstrip() + '\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_audio(config, stop_event=None):
    import sounddevice as sd
    model = load_model(config) if config.get('live') else None
    stop = stop_event if stop_event is not None else threading.Event()
    if stop_event is None:
        def commands():
            # EOF also ends recording if the parent exits normally.
            sys.stdin.readline()
            stop.set()
        threading.Thread(target=commands, daemon=True).start()
    device = config.get('device')
    condition = threading.Condition()
    state = {'frames': 0, 'closed': False, 'error': None, 'stop': stop}
    preview = None
    try:
        info = sd.query_devices(device, 'input')
        rate = int(info['default_samplerate'])
        frames = max(1, rate // 10)
        count = 0
        last_update = time.monotonic()
        try:
            with open(config['audio'], 'wb') as output, wave.open(output, 'wb') as audio:
                audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                with sd.RawInputStream(device=device, samplerate=rate, channels=1, dtype='int16', blocksize=frames) as stream:
                    if model is not None:
                        preview = threading.Thread(target=preview_recording,
                            args=(config, model, rate, condition, state), daemon=True)
                        preview.start()
                    emit('recording')
                    while not stop.is_set():
                        data, overflow = stream.read(frames)
                        with condition:
                            audio.writeframes(data)
                            output.flush()
                            count += len(data) // 2
                            state['frames'] = count
                            condition.notify_all()
                        if overflow:
                            emit('status', text='Mikrofonpuffer übergelaufen; ein Teil des Audios fehlt.')
                        if time.monotonic() - last_update >= 1:
                            emit('status', text=f'Aufnahme läuft: {int(count / rate)} Sekunden …')
                            last_update = time.monotonic()
        finally:
            with condition:
                state['closed'] = True
                condition.notify_all()
            if preview is not None:
                preview.join()
        if state['error'] is not None:
            raise RuntimeError('Live-Erkennung fehlgeschlagen: ' + str(state['error']))
        if count == 0:
            raise RuntimeError('Es wurde kein Audio aufgenommen.')
        emit('recorded', seconds=count / rate)
    except Exception as exc:
        raise RuntimeError('Mikrofonaufnahme fehlgeschlagen. Standardmikrofon und Windows-Mikrofonfreigabe für Desktop-Apps prüfen. ' + str(exc)) from exc
    if model is not None:
        transcribe_audio(config, model=model, replace_preview=True)
    else:
        emit('done')


def transcribe_audio(config, model=None, replace_preview=False):
    if not Path(config['audio']).is_file():
        raise RuntimeError('Die Audiodatei ist nicht mehr vorhanden.')
    if model is None:
        model = load_model(config)
    language = {'Deutsch': 'de', 'Englisch': 'en', 'Automatisch': None}[config['language']]
    segments, info = model.transcribe(config['audio'], language=language, beam_size=5,
                                      vad_filter=True, condition_on_previous_text=False)
    emit('status', text=f'Erkenne Sprache ({info.language}) …')
    result = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            result.append(text)
            if not replace_preview:
                emit('segment', text=text)
            emit('status', text=f'Erkannt bis {segment.end:.1f} Sekunden …')
    if not result:
        raise RuntimeError('Keine Sprache erkannt. Lautstärke, Aufnahme und Sprachwahl prüfen.')
    if replace_preview:
        emit('transcript', text='\n'.join(result), final=True)
    emit('done')


class TranscriberPanel:
    def __init__(self, parent, before_record=None):
        self.root = parent.winfo_toplevel()
        self.before_record = before_record
        self.storage = tempfile.TemporaryDirectory(prefix='speechy-stt-')
        self.events = queue.Queue()
        self.generation = 0
        self.job = None
        self.audio_path = None
        self.saved = True
        self.closed = False
        self.language = tk.StringVar(value='Deutsch')
        self.input_device = tk.StringVar(value='Windows-Standardgerät')
        self.devices = {'Windows-Standardgerät': None}
        self.source = tk.StringVar(value='Noch keine Aufnahme oder Audiodatei.')
        self.status = tk.StringVar(value='Mikrofonaufnahme starten oder Audiodatei öffnen.')
        frame = ttk.Frame(parent, padding=14)
        frame.pack(fill='both', expand=True)
        bar = ttk.Frame(frame)
        bar.pack(fill='x')
        self.open_button = ttk.Button(bar, text='Audiodatei öffnen', command=self.open_audio)
        self.open_button.pack(side='left', padx=3)
        self.record_button = ttk.Button(bar, text='Aufnahme starten', command=self.toggle_record)
        self.record_button.pack(side='left', padx=3)
        self.transcribe_button = ttk.Button(bar, text='In Text umwandeln', command=self.start_transcription)
        self.transcribe_button.pack(side='left', padx=3)
        self.cancel_button = ttk.Button(bar, text='Abbrechen', command=self.cancel)
        self.cancel_button.pack(side='left', padx=3)
        ttk.Label(bar, text='Sprache:').pack(side='left', padx=(15, 5))
        self.language_box = ttk.Combobox(bar, textvariable=self.language, values=['Deutsch', 'Englisch', 'Automatisch'],
                                        state='readonly', width=13)
        self.language_box.pack(side='left')
        microphone_bar = ttk.Frame(frame)
        microphone_bar.pack(fill='x', pady=(12, 4))
        ttk.Label(microphone_bar, text='Mikrofon:').pack(side='left')
        self.device_box = ttk.Combobox(microphone_bar, textvariable=self.input_device,
                                      values=list(self.devices), state='readonly', width=48)
        self.device_box.pack(side='left', padx=6)
        self.refresh_button = ttk.Button(microphone_bar, text='Mikrofone aktualisieren', command=self.refresh_devices)
        self.refresh_button.pack(side='left')
        ttk.Label(frame, text='Live-Vorschau beim Sprechen; nach dem Stoppen wird der Text abschließend geprüft.').pack(anchor='w', pady=(0, 4))
        ttk.Label(frame, textvariable=self.source, wraplength=850).pack(anchor='w')
        ttk.Label(frame, textvariable=self.status, wraplength=850).pack(anchor='w', pady=8)
        ttk.Label(frame, text='Erkannter Text (vor dem Speichern bearbeitbar):').pack(anchor='w')
        self.text = tk.Text(frame, wrap='word', undo=True, height=18)
        self.text.pack(fill='both', expand=True, pady=(4, 8))
        self.text.bind('<<Modified>>', self.mark_modified)
        self.save_button = ttk.Button(frame, text='Als Textdatei speichern …', command=self.save_text)
        self.save_button.pack(anchor='w')
        self.update_controls()
        self.poll_id = self.root.after(80, self.poll)

    @property
    def recording(self):
        return self.job is not None and self.job['mode'] == 'record' and not self.job.get('recorded')

    def mark_modified(self, event=None):
        if self.text.edit_modified():
            self.saved = False
            self.text.edit_modified(False)

    def allow_replace(self):
        return self.saved or not self.text.get('1.0', 'end-1c').strip() or messagebox.askyesno(
            'Speechy', 'Den noch nicht gespeicherten Text durch eine neue Transkription ersetzen?')

    def update_controls(self):
        busy = self.job is not None
        recording = self.recording
        for button in (self.open_button, self.transcribe_button, self.save_button):
            button.configure(state='disabled' if busy else 'normal')
        self.record_button.configure(state='normal' if not busy or recording else 'disabled',
                                     text='Aufnahme stoppen' if recording else 'Aufnahme starten')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.language_box.configure(state='disabled' if busy else 'readonly')
        self.device_box.configure(state='disabled' if busy else 'readonly')
        self.refresh_button.configure(state='disabled' if busy else 'normal')
        self.text.configure(state='disabled' if busy else 'normal')

    def open_audio(self):
        path = filedialog.askopenfilename(title='Audio auswählen', filetypes=[
            ('Audiodateien', '*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.mp4'), ('Alle Dateien', '*.*')])
        if path:
            self.audio_path = path
            self.source.set(f'Audiodatei: {Path(path).name}')
            self.status.set('Bereit. „In Text umwandeln“ starten.')

    def refresh_devices(self):
        if self.job is None:
            self.launch('devices', {})

    def toggle_record(self):
        if self.recording:
            try:
                self.job['process'].stdin.write('stop\n')
                self.job['process'].stdin.flush()
                self.record_button.configure(state='disabled')
                self.status.set('Beende Aufnahme …')
            except (OSError, ValueError):
                self.status.set('Aufnahme wird beendet …')
            return
        if self.job is not None or not self.allow_replace():
            return
        if not (MODEL_DIR / 'model.bin').is_file():
            messagebox.showerror('Speechy', 'Whisper-Modell fehlt. Bitte start.bat ausführen.')
            return
        if self.before_record:
            self.before_record()
        audio = str(Path(self.storage.name) / (uuid.uuid4().hex + '.wav'))
        self.launch('record', {'audio': audio, 'device': self.devices.get(self.input_device.get()),
                              'live': True, 'model': str(MODEL_DIR), 'language': self.language.get()}, replace_approved=True)

    def start_transcription(self, replace_approved=False):
        if self.job is not None:
            return
        if not self.audio_path:
            messagebox.showinfo('Speechy', 'Bitte eine Aufnahme erstellen oder eine Audiodatei öffnen.')
            return
        if not replace_approved and not self.allow_replace():
            return
        if not (MODEL_DIR / 'model.bin').is_file():
            messagebox.showerror('Speechy', 'Whisper-Modell fehlt. Bitte start.bat ausführen.')
            return
        self.launch('transcribe', {'audio': self.audio_path, 'model': str(MODEL_DIR), 'language': self.language.get()})

    def launch(self, mode, config, replace_approved=False):
        try:
            jobfile = Path(self.storage.name) / (uuid.uuid4().hex + '.json')
            jobfile.write_text(json.dumps(config), encoding='utf-8')
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--' + mode, str(jobfile)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
            self.generation += 1
            generation = self.generation
            self.job = {'process': process, 'mode': mode, 'config': config, 'done': False, 'recorded': False,
                        'replace_approved': replace_approved}
            if mode == 'transcribe' or mode == 'record':
                self.text.delete('1.0', 'end')
                self.text.edit_reset()
                self.text.edit_modified(False)
                self.saved = True
            self.status.set({'record': 'Starte Aufnahme …', 'transcribe': 'Starte Transkription …',
                             'devices': 'Suche Mikrofone …'}[mode])
            self.update_controls()
            def collect():
                with process.stdout:
                    for line in process.stdout:
                        self.events.put((generation, line))
                self.events.put((generation, None))
            threading.Thread(target=collect, daemon=True).start()
        except Exception as exc:
            messagebox.showerror('Speechy', str(exc))

    def poll(self):
        for _ in range(100):
            try:
                generation, line = self.events.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation or self.job is None:
                continue
            if line is None:
                job, self.job = self.job, None
                code = job['process'].wait()
                job['process'].stdin.close()
                self.update_controls()
                if code == 0 and job['done']:
                    if job['mode'] == 'devices':
                        self.status.set('Mikrofone aktualisiert. Headset in der Liste auswählen.')
                    elif job['mode'] == 'record' and job['recorded']:
                        self.audio_path = job['config']['audio']
                        self.source.set(f'Mikrofonaufnahme: {job["seconds"]:.1f} Sekunden')
                        if job['config'].get('live'):
                            self.status.set('Text erkannt. Prüfen, bearbeiten und als .txt speichern.')
                        else:
                            self.start_transcription(replace_approved=job['replace_approved'])
                    else:
                        self.saved = False
                        self.status.set('Text erkannt. Prüfen, bearbeiten und als .txt speichern.')
                elif not job.get('error'):
                    self.status.set(f'Vorgang fehlgeschlagen (Code {code}). Einrichtung prüfen.')
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get('kind') if isinstance(event, dict) else None
            if kind == 'recording':
                self.status.set('Mikrofonaufnahme läuft. Zum Beenden „Aufnahme stoppen“ klicken.')
            elif kind == 'devices':
                self.devices = {'Windows-Standardgerät': None}
                self.devices.update({f'{item["id"]}: {item["name"]}': item['id'] for item in event['devices']})
                self.device_box.configure(values=list(self.devices))
                if self.input_device.get() not in self.devices:
                    self.input_device.set('Windows-Standardgerät')
            elif kind == 'recorded':
                self.job['recorded'] = True
                self.job['seconds'] = event['seconds']
                self.audio_path = self.job['config']['audio']
                self.update_controls()
                self.status.set('Aufnahme beendet. Prüfe den vollständigen Text …')
            elif kind == 'status':
                self.status.set(event['text'])
            elif kind == 'segment':
                self.text.configure(state='normal')
                self.text.insert('end', event['text'] + '\n')
                self.text.see('end')
                self.text.configure(state='disabled')
                self.saved = False
            elif kind == 'transcript':
                self.text.configure(state='normal')
                self.text.delete('1.0', 'end')
                if event['text']:
                    self.text.insert('end', event['text'] + '\n')
                self.text.edit_reset()
                self.text.edit_modified(False)
                self.text.see('end')
                self.text.configure(state='disabled')
                self.saved = not bool(event['text'].strip())
            elif kind == 'done':
                self.job['done'] = True
            elif kind == 'error':
                self.job['error'] = True
                self.status.set(event['text'])
                messagebox.showerror('Speechy', event['text'])
        if not self.closed:
            self.poll_id = self.root.after(80, self.poll)

    def save_text(self):
        text = self.text.get('1.0', 'end-1c')
        if not text.strip():
            messagebox.showinfo('Speechy', 'Es ist noch kein Text vorhanden.')
            return
        path = filedialog.asksaveasfilename(title='Transkription speichern', defaultextension='.txt',
                                           filetypes=[('Textdatei', '*.txt')])
        if path:
            try:
                if self.audio_path and Path(path).resolve() == Path(self.audio_path).resolve():
                    raise ValueError('Die Audiodatei darf nicht überschrieben werden.')
                save_text_atomic(path, text)
                self.saved = True
                self.status.set(f'Gespeichert: {path}')
            except Exception as exc:
                messagebox.showerror('Speechy', str(exc))

    def cancel(self):
        self.generation += 1
        job, self.job = self.job, None
        if job is not None:
            process = job['process']
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdin.close()
        self.update_controls()
        self.status.set('Abgebrochen. Bereits erkannter Teiltext kann gespeichert werden.')

    def can_close(self):
        return self.saved or not self.text.get('1.0', 'end-1c').strip() or messagebox.askyesno(
            'Speechy', 'Es gibt ungespeicherten Text. Fenster trotzdem schließen?')

    def close(self):
        self.closed = True
        self.root.after_cancel(self.poll_id)
        self.cancel()
        self.storage.cleanup()


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3 or sys.argv[1] not in ('--record', '--transcribe', '--devices'):
            raise ValueError('Interner Worker benötigt --record/--transcribe/--devices und eine Job-Datei.')
        config = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
        {'--record': record_audio, '--transcribe': transcribe_audio, '--devices': list_input_devices}[sys.argv[1]](config)
    except Exception as exc:
        emit('error', text=str(exc))
        sys.exit(1)
