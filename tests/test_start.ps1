# Runs launcher recovery tests without installing software or opening Tk windows.
$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot '..\scripts\start.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($launcher, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors -join "`n") }
$definition = $ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Confirm-InstalledPython'
}, $true)[0]
Invoke-Expression $definition.Extent.Text
$runtimeDir = Join-Path ([System.IO.Path]::GetTempPath()) ('speechy-launcher-test-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $runtimeDir | Out-Null
$script:probeFailures = @{}
$script:probeResults = [System.Collections.Generic.Queue[object]]::new()
$script:repairs = 0
function Find-Python([string]$Executable) {
    $script:probeFailures[$Executable] = 'test diagnostic'
    return $script:probeResults.Dequeue()
}
function Find-RegisteredPython { return $null }
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$Wait, [switch]$PassThru)
    if ($ArgumentList -notmatch '/repair /quiet /log' -or $WindowStyle -ne 'Hidden') { throw 'Wrong repair invocation' }
    $script:repairs++
    return [pscustomobject]@{ ExitCode = 0 }
}
try {
    $script:probeResults.Enqueue('valid-python')
    $result = Confirm-InstalledPython 'missing.exe' 'installer.exe' $runtimeDir 'install.log'
    if ($result -ne 'valid-python' -or $script:repairs -ne 0) { throw 'Healthy Python must not be repaired' }

    $script:probeResults.Enqueue($null)
    $script:probeResults.Enqueue('restored-python')
    $result = Confirm-InstalledPython 'missing.exe' 'installer.exe' $runtimeDir 'install.log'
    if ($result -ne 'restored-python' -or $script:repairs -ne 1) { throw 'Missing files must be repaired and retested' }

    $script:probeResults.Enqueue($null)
    $script:probeResults.Enqueue($null)
    $message = ''
    try { Confirm-InstalledPython 'missing.exe' 'installer.exe' $runtimeDir 'install.log' } catch { $message = $_.Exception.Message }
    if ($message -notmatch 'python.exe nicht am Zielpfad' -or -not (Test-Path (Join-Path $runtimeDir 'python-probe.log'))) {
        throw 'Missing executable must produce a specific error and saved diagnostics'
    }
    $executable = Join-Path $runtimeDir 'python.exe'
    Set-Content -LiteralPath $executable -Value 'stub'
    $script:probeResults.Enqueue($null)
    $script:probeResults.Enqueue($null)
    try { Confirm-InstalledPython $executable 'installer.exe' $runtimeDir 'install.log' } catch { $message = $_.Exception.Message }
    if ($message -notmatch 'Python/Tk-Pruefung fehlgeschlagen') { throw 'Tk error must differ from missing executable' }
    Write-Host '4 launcher recovery tests passed.'
} finally {
    foreach ($file in @(Get-ChildItem -LiteralPath $runtimeDir -File)) { Remove-Item -LiteralPath $file.FullName }
    Remove-Item -LiteralPath $runtimeDir
}
