"""Windows package identity for Speechy (package with external location).

With identity, Speechy is listed as its own app under Settings > Privacy &
security > Microphone. "Let desktop apps access your microphone" can stay off,
so other desktop and Python programs do not get the microphone.

The signed identity package (packaging/speechy-identity.msix) contains only the
manifest and logos; the manifest points at .speechy-venv\\Scripts\\pythonw.exe in
this folder, which is registered as the package's external location.
"""
import asyncio
import ctypes
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = 'Speechy.Local'
PUBLISHER = 'CN=Speechy Local Identity'
APP_ID = 'Speechy'
APPMODEL_ERROR_NO_PACKAGE = 15700
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class IdentityUnavailable(RuntimeError):
    """Speechy cannot be started with its own app identity."""


def package_files():
    folder = ROOT / 'packaging'
    return folder / 'speechy-identity.msix', folder / 'speechy-identity.cer'


def marker_path():
    return ROOT / '.speechy-identity.json'


def has_identity():
    """True if the current process runs with a package identity."""
    if sys.platform != 'win32':
        return False
    length = ctypes.c_uint32(0)
    return ctypes.windll.kernel32.GetCurrentPackageFullName(ctypes.byref(length), None) != APPMODEL_ERROR_NO_PACKAGE


def _deployment(install=True):
    """Import the WinRT projections, installing requirements-identity.txt on first use."""
    for attempt in range(2):
        try:
            from winrt.windows.foundation import Uri
            from winrt.windows.management.deployment import AddPackageOptions, PackageManager
            # Projections for the Package objects and iterables returned by PackageManager.
            import winrt.windows.applicationmodel  # noqa: F401
            import winrt.windows.foundation.collections  # noqa: F401
            return Uri, AddPackageOptions, PackageManager
        except ImportError:
            if attempt or not install:
                raise IdentityUnavailable('Die WinRT-Pakete fuer die App-Identitaet fehlen.') from None
        print('Installiere WinRT-Pakete fuer die App-Identitaet ...', flush=True)
        subprocess.run([sys.executable, '-I', '-m', 'pip', 'install', '-r', str(ROOT / 'requirements-identity.txt')],
                       check=True)
        importlib.invalidate_caches()


def find_package(install=True):
    """(family name, full name) of the registered Speechy package for the current user, or None."""
    PackageManager = _deployment(install)[2]
    for package in PackageManager().find_packages_by_user_security_id_name_publisher('', PACKAGE_NAME, PUBLISHER):
        return package.id.family_name, package.id.full_name
    return None


def _await(operation, action):
    async def wait():
        return await operation
    try:
        result = asyncio.run(wait())
    except OSError as exc:
        raise IdentityUnavailable(f'{action} fehlgeschlagen: {exc}') from exc
    if result is not None and getattr(result, 'error_text', ''):
        raise IdentityUnavailable(f'{action} fehlgeschlagen: {result.error_text}')
    return result


def add_package(msix, location):
    Uri, AddPackageOptions, PackageManager = _deployment()
    options = AddPackageOptions()
    options.external_location_uri = Uri(Path(location).as_uri())
    options.force_update_from_any_version = True
    _await(PackageManager().add_package_by_uri_async(Uri(Path(msix).as_uri()), options),
           'Registrierung des Identitaetspakets')


def remove_package(full_name):
    PackageManager = _deployment()[2]
    _await(PackageManager().remove_package_async(full_name), 'Entfernen des Identitaetspakets')


def cert_thumbprint(cer):
    # The build script exports the certificate DER-encoded, so this is the Windows SHA-1 thumbprint.
    return hashlib.sha1(Path(cer).read_bytes()).hexdigest()


def cert_trusted(cer):
    result = subprocess.run(['certutil', '-store', 'TrustedPeople', cert_thumbprint(cer)],
                            capture_output=True, creationflags=NO_WINDOW)
    return result.returncode == 0


def _run_elevated(program, parameters):
    """Run program via the UAC prompt, wait for it and return its exit code (None if declined)."""
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG), ('hwnd', wintypes.HWND),
                    ('lpVerb', wintypes.LPCWSTR), ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                    ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE),
                    ('lpIDList', ctypes.c_void_p), ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                    ('dwHotKey', wintypes.DWORD), ('hIconOrMonitor', wintypes.HANDLE), ('hProcess', wintypes.HANDLE)]

    see_mask_nocloseprocess, sw_hide, infinite = 0x40, 0, 0xFFFFFFFF
    info = ShellExecuteInfo(cbSize=ctypes.sizeof(ShellExecuteInfo), fMask=see_mask_nocloseprocess,
                            lpVerb='runas', lpFile=program, lpParameters=parameters, nShow=sw_hide)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return None
    try:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, infinite)
        code = wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        ctypes.windll.kernel32.CloseHandle(info.hProcess)


def trust_cert(cer):
    print('Windows fragt jetzt einmalig nach Administratorrechten, um das Speechy-Zertifikat '
          'unter "Vertrauenswuerdige Personen" einzutragen ...', flush=True)
    code = _run_elevated('certutil.exe', f'-addstore TrustedPeople "{cer}"')
    if code != 0 or not cert_trusted(cer):
        raise IdentityUnavailable('Das Speechy-Zertifikat wurde nicht eingetragen (Administratorabfrage abgelehnt?).')


def _read_marker():
    try:
        return json.loads(marker_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def ensure_registered():
    """Register the identity package for this folder if needed; return the package family name."""
    if sys.platform != 'win32':
        raise IdentityUnavailable('Eine App-Identitaet gibt es nur unter Windows.')
    msix, cer = package_files()
    if not msix.is_file() or not cer.is_file():
        raise IdentityUnavailable('Das Identitaetspaket packaging\\speechy-identity.msix fehlt '
                                  '(siehe packaging\\README.md).')
    expected = {'location': str(ROOT), 'package': hashlib.sha256(msix.read_bytes()).hexdigest()}
    found = find_package()
    if found and _read_marker() == expected:
        return found[0]
    if not cert_trusted(cer):
        trust_cert(cer)
    # The external location is fixed at registration; a moved folder or a rebuilt
    # package therefore needs a fresh registration.
    if found:
        print('Registriere die App-Identitaet fuer diesen Ordner neu ...', flush=True)
        remove_package(found[1])
    else:
        print('Registriere Speechy als eigene Windows-App ...', flush=True)
    add_package(msix, ROOT)
    found = find_package()
    if not found:
        raise IdentityUnavailable('Das Identitaetspaket ist nach der Registrierung nicht auffindbar.')
    marker_path().write_text(json.dumps(expected), encoding='utf-8')
    return found[0]


def launch(family_name):
    """Start Speechy through its app entry so the process gets the package identity."""
    subprocess.Popen(['explorer.exe', f'shell:AppsFolder\\{family_name}!{APP_ID}'])


def remove():
    found = find_package()
    if found:
        remove_package(found[1])
    marker_path().unlink(missing_ok=True)
    return found is not None


def status():
    if sys.platform != 'win32':
        return 'nicht verfuegbar (nur Windows)'
    msix, cer = package_files()
    if not msix.is_file() or not cer.is_file():
        return 'Identitaetspaket fehlt (siehe packaging\\README.md)'
    try:
        found = find_package(install=False)
    except IdentityUnavailable:
        return 'noch nicht registriert (WinRT-Pakete fehlen)'
    if not found:
        return 'noch nicht registriert'
    if _read_marker() != {'location': str(ROOT), 'package': hashlib.sha256(msix.read_bytes()).hexdigest()}:
        return f'registriert als {found[0]}, wird beim naechsten Start fuer diesen Ordner erneuert'
    return f'registriert als {found[0]}'
