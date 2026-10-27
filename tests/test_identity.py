import importlib.util
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


identity = load('win_identity', ROOT / 'scripts/win_identity.py')
sys.modules['win_identity'] = identity
bootstrap = load('bootstrap', ROOT / 'scripts/bootstrap.py')

NS = {'m': 'http://schemas.microsoft.com/appx/manifest/foundation/windows10',
      'uap10': 'http://schemas.microsoft.com/appx/manifest/uap/windows10/10',
      'rescap': 'http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities'}


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = ET.parse(ROOT / 'packaging/AppxManifest.xml').getroot()

    def test_identity_matches_registration_code(self):
        element = self.manifest.find('m:Identity', NS)
        self.assertEqual(element.get('Name'), identity.PACKAGE_NAME)
        self.assertEqual(element.get('Publisher'), identity.PUBLISHER)
        self.assertEqual(self.manifest.find('m:Applications/m:Application', NS).get('Id'), identity.APP_ID)

    def test_microphone_and_external_content_declared(self):
        self.assertEqual(self.manifest.find('m:Properties/uap10:AllowExternalContent', NS).text, 'true')
        devices = [element.get('Name') for element in self.manifest.findall('m:Capabilities/m:DeviceCapability', NS)]
        self.assertIn('microphone', devices)
        restricted = [element.get('Name') for element in self.manifest.findall('m:Capabilities/rescap:Capability', NS)]
        self.assertIn('runFullTrust', restricted)

    def test_app_entry_runs_bootstrap_from_registered_folder(self):
        application = self.manifest.find('m:Applications/m:Application', NS)
        self.assertEqual(application.get('Executable'), r'.speechy-venv\Scripts\pythonw.exe')
        arguments = shlex.split(application.get(f'{{{NS["uap10"]}}}Parameters'))
        self.assertEqual(arguments[:2], ['-I', '-c'])
        self.assertEqual(arguments[3:], ['--identity-launch'])
        # Simulate pythonw.exe inside a Speechy folder and check the script path the entry computes.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'Speechy'
            executable = folder / '.speechy-venv' / 'Scripts' / 'pythonw.exe'
            with patch('runpy.run_path') as run_path, patch.object(sys, 'executable', str(executable)):
                exec(arguments[2], {})
            run_path.assert_called_once_with(str(folder / 'scripts' / 'bootstrap.py'), run_name='__main__')


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / 'packaging').mkdir()
        self.msix = self.root / 'packaging' / 'speechy-identity.msix'
        self.cer = self.root / 'packaging' / 'speechy-identity.cer'
        self.msix.write_bytes(b'msix')
        self.cer.write_bytes(b'cer')
        for patcher in (patch.object(identity, 'ROOT', self.root), patch.object(identity.sys, 'platform', 'win32')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.directory.cleanup)

    def marker(self):
        return {'location': str(self.root), 'package': identity.hashlib.sha256(b'msix').hexdigest()}

    def test_missing_package_file_is_reported(self):
        self.msix.unlink()
        with self.assertRaisesRegex(identity.IdentityUnavailable, 'speechy-identity.msix fehlt'):
            identity.ensure_registered()

    def test_first_registration_trusts_certificate_and_writes_marker(self):
        found = [None, ('Speechy.Local_abc', 'Speechy.Local_1.0.0.0_neutral__abc')]
        with patch.object(identity, 'find_package', side_effect=found), \
                patch.object(identity, 'cert_trusted', return_value=False), \
                patch.object(identity, 'trust_cert') as trust, \
                patch.object(identity, 'remove_package') as remove, \
                patch.object(identity, 'add_package') as add:
            self.assertEqual(identity.ensure_registered(), 'Speechy.Local_abc')
        trust.assert_called_once_with(self.cer)
        remove.assert_not_called()
        add.assert_called_once_with(self.msix, self.root)
        self.assertEqual(json.loads(identity.marker_path().read_text()), self.marker())

    def test_unchanged_registration_is_reused(self):
        identity.marker_path().write_text(json.dumps(self.marker()))
        with patch.object(identity, 'find_package', return_value=('Speechy.Local_abc', 'full')), \
                patch.object(identity, 'cert_trusted') as trusted, patch.object(identity, 'add_package') as add:
            self.assertEqual(identity.ensure_registered(), 'Speechy.Local_abc')
        trusted.assert_not_called()
        add.assert_not_called()

    def test_moved_folder_is_registered_again(self):
        identity.marker_path().write_text(json.dumps({**self.marker(), 'location': r'C:\alt\Speechy'}))
        with patch.object(identity, 'find_package', return_value=('Speechy.Local_abc', 'full')), \
                patch.object(identity, 'cert_trusted', return_value=True), \
                patch.object(identity, 'remove_package') as remove, patch.object(identity, 'add_package') as add:
            identity.ensure_registered()
        remove.assert_called_once_with('full')
        add.assert_called_once_with(self.msix, self.root)
        self.assertEqual(json.loads(identity.marker_path().read_text()), self.marker())

    def test_declined_certificate_prompt_stops_registration(self):
        with patch.object(identity, 'find_package', return_value=None), \
                patch.object(identity, 'cert_trusted', return_value=False), \
                patch.object(identity, '_run_elevated', return_value=None), \
                patch.object(identity, 'add_package') as add:
            with self.assertRaisesRegex(identity.IdentityUnavailable, 'Zertifikat wurde nicht eingetragen'):
                identity.ensure_registered()
        add.assert_not_called()


class StartTests(unittest.TestCase):
    def run_main(self, *arguments, platform='win32', registration=None):
        assets = (Path('voices'), Path('tessdata'), Path('bin'))
        with patch.object(bootstrap.sys, 'argv', ['bootstrap.py', *arguments]), \
                patch.object(bootstrap.sys, 'platform', platform), \
                patch.object(bootstrap, 'required_packages_ready', return_value=True), \
                patch.object(bootstrap, 'prepare_assets', return_value=assets), \
                patch.object(bootstrap, 'prepare_stt_assets'), \
                patch.object(bootstrap, 'start_gui') as gui, \
                patch.object(identity, 'ensure_registered', return_value='Speechy.Local_abc',
                             side_effect=registration) as ensure, \
                patch.object(identity, 'launch') as launch:
            bootstrap.main()
        return gui, ensure, launch

    def test_windows_start_launches_app_with_identity(self):
        gui, _, launch = self.run_main()
        launch.assert_called_once_with('Speechy.Local_abc')
        gui.assert_not_called()

    def test_no_identity_starts_directly(self):
        gui, ensure, _ = self.run_main('--no-identity')
        ensure.assert_not_called()
        gui.assert_called_once()

    def test_unavailable_identity_falls_back_to_direct_start(self):
        gui, _, launch = self.run_main(registration=identity.IdentityUnavailable('fehlt'))
        launch.assert_not_called()
        gui.assert_called_once()

    def test_other_platforms_start_directly(self):
        gui, ensure, _ = self.run_main(platform='linux')
        ensure.assert_not_called()
        gui.assert_called_once()

    def test_identity_launch_skips_setup_and_registration(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bootstrap, 'ROOT', Path(directory)), \
                patch.object(bootstrap.sys, 'argv', ['-c', '--identity-launch']), \
                patch.object(bootstrap, 'required_packages_ready') as ready, \
                patch.object(bootstrap, 'start_gui') as gui, \
                patch.object(identity, 'ensure_registered') as ensure:
            with self.assertRaisesRegex(RuntimeError, 'start.bat ausfuehren'):
                bootstrap.main()
            for path in bootstrap.missing_assets():
                (Path(directory) / path).parent.mkdir(parents=True, exist_ok=True)
                (Path(directory) / path).write_bytes(b'x')
            bootstrap.main()
            expected = bootstrap.asset_paths()
        ready.assert_not_called()
        ensure.assert_not_called()
        gui.assert_called_once_with(*expected)


if __name__ == '__main__':
    unittest.main()
