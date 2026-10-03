param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
# Remove inherited Python/Tk overrides only in this launcher process.
foreach ($name in @('PYTHONHOME', 'PYTHONPATH', 'TCL_LIBRARY', 'TK_LIBRARY')) {
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
$env:PYTHONUTF8 = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$runtimeDir = Join-Path $taskRoot '.runtime'
$localPython = Join-Path $runtimeDir 'python313\python.exe'
$venvDir = Join-Path $taskRoot '.speechy-venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$probe = @'
import sys, struct
try:
    assert (3, 11) <= sys.version_info[:2] <= (3, 13)
    assert struct.calcsize('P') == 8
    import tkinter as tk
    root = tk.Tk(); root.withdraw(); root.destroy()
    import venv, ensurepip
except Exception:
    sys.exit(2)
print(sys.executable)
'@

function Find-Python([string]$Executable, [string[]]$Prefix = @()) {
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable)) { return $null }
    $ErrorActionPreference = 'SilentlyContinue'
    $result = & $Executable @Prefix -I -c $probe 2>$null
    if ($LASTEXITCODE -eq 0 -and $result) { return ([string]($result | Select-Object -Last 1)).Trim() }
    return $null
}

function Run-Python([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python-Schritt fehlgeschlagen (Exit-Code $LASTEXITCODE)." }
}

function Find-LauncherPython([string]$Executable) {
    $ErrorActionPreference = 'SilentlyContinue'
    # Enumerate installed interpreters; do not request a version that a newer
    # Python install manager could automatically download, especially in CheckOnly.
    $installed = & $Executable -0p 2>$null
    foreach ($line in @($installed)) {
        if ([string]$line -match '([A-Za-z]:\\.+python.exe)\s*$') {
            $found = Find-Python $Matches[1]
            if ($found) { return $found }
        }
    }
    return $null
}

try {
    Write-Host 'Speechy: Voraussetzungen werden geprueft ...'
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Speechy benoetigt 64-Bit-Windows.' }
    foreach ($file in @('text2speech\reader1.py', 'requirements-start.txt', 'scripts\bootstrap.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $taskRoot $file))) { throw "Datei fehlt: $file. Gesamtes ZIP entpacken." }
    }
    $pythonPath = Find-Python $venvPython
    if (-not $pythonPath) {
        foreach ($existing in @(Get-ChildItem -LiteralPath $taskRoot -Directory -Filter '.speechy-venv-*' | Sort-Object LastWriteTime -Descending)) {
            $candidate = Join-Path $existing.FullName 'Scripts\python.exe'
            $pythonPath = Find-Python $candidate
            if ($pythonPath) { $venvDir = $existing.FullName; $venvPython = $candidate; break }
        }
    }
    if (-not $pythonPath) { $pythonPath = Find-Python $localPython }
    if (-not $pythonPath) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            $pythonPath = Find-LauncherPython $launcher.Source
        }
    }
    if (-not $pythonPath) {
        foreach ($name in @('python.exe', 'python3.exe')) {
            $candidate = Get-Command $name -ErrorAction SilentlyContinue
            if ($candidate -and $candidate.Source -notmatch 'WindowsApps') {
                $pythonPath = Find-Python $candidate.Source
                if ($pythonPath) { break }
            }
        }
    }
    if (-not $pythonPath -and $env:LOCALAPPDATA) {
        foreach ($candidate in @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe') -File -ErrorAction SilentlyContinue)) {
            $pythonPath = Find-Python $candidate.FullName
            if ($pythonPath) { break }
        }
    }
    if (-not $pythonPath) {
        if ($CheckOnly) { throw 'Keine geeignete Python-Installation mit Tk gefunden; CheckOnly installiert nichts.' }
        Write-Host 'Installiere Python 3.13.16 mit Tkinter lokal im Speechy-Ordner ...'
        New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $installer = Join-Path $runtimeDir 'python-3.13.16-amd64.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe' -OutFile $installer
        $signature = Get-AuthenticodeSignature -LiteralPath $installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
            throw 'Die Signatur des Python-Installers ist nicht gueltig. Installer wird nicht ausgefuehrt.'
        }
        $targetDir = Split-Path -Parent $localPython
        $arguments = '/quiet InstallAllUsers=0 Include_pip=1 Include_tcltk=1 Include_launcher=0 Include_test=0 Include_doc=0 PrependPath=0 AssociateFiles=0 Shortcuts=0 TargetDir="' + $targetDir + '"'
        $installation = Start-Process -FilePath $installer -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
        if ($installation.ExitCode -notin @(0, 3010)) { throw "Python-Installation fehlgeschlagen: $($installation.ExitCode)" }
        $pythonPath = Find-Python $localPython
        if (-not $pythonPath) { throw 'Python ist installiert, aber der Tk-Test scheitert. Installation oder Windows-Sitzung pruefen.' }
    }
    Write-Host "Python mit Tk: $pythonPath"
    if ($CheckOnly) {
        Run-Python $pythonPath @('-I', (Join-Path $PSScriptRoot 'bootstrap.py'), '--check-only')
        exit 0
    }
    if ($pythonPath -ne $venvPython) {
        # An unusable existing environment is preserved rather than overwritten.
        if (Test-Path -LiteralPath $venvDir) {
            $venvDir = Join-Path $taskRoot ('.speechy-venv-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
            $venvPython = Join-Path $venvDir 'Scripts\python.exe'
        }
        Write-Host 'Erstelle virtuelle Python-Umgebung ...'
        Run-Python $pythonPath @('-I', '-m', 'venv', $venvDir)
    }
    Run-Python $venvPython @('-I', (Join-Path $PSScriptRoot 'bootstrap.py'))
    exit 0
} catch {
    Write-Host "Fehler: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Erster Start: Internetzugang und ein beschreibbarer Speechy-Ordner sind erforderlich.'
    exit 1
}
