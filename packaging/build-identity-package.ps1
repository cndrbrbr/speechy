# Builds packaging\speechy-identity.msix and packaging\speechy-identity.cer.
# Run once on a developer machine with the Windows SDK (MakeAppx.exe, SignTool.exe),
# then commit both files. Users never run this script.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$manifestPath = Join-Path $here 'AppxManifest.xml'
[xml]$manifest = Get-Content -LiteralPath $manifestPath -Encoding UTF8
$publisher = $manifest.Package.Identity.Publisher

function Find-SdkTool([string]$name) {
    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $tool = Get-ChildItem -Path $kits -Filter $name -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -eq 'x64' -and $_.Directory.Parent.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
        Sort-Object { [version]$_.Directory.Parent.Name } -Descending |
        Select-Object -First 1
    if (-not $tool) { throw "$name nicht gefunden. Bitte das Windows SDK installieren." }
    return $tool.FullName
}
$makeappx = Find-SdkTool 'makeappx.exe'
$signtool = Find-SdkTool 'signtool.exe'

# Package layout: manifest plus logos only, no program files.
$build = Join-Path $here 'build'
$layout = Join-Path $build 'layout'
if (Test-Path -LiteralPath $build) { Remove-Item -LiteralPath $build -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $layout 'Assets') -Force | Out-Null
Copy-Item -LiteralPath $manifestPath -Destination $layout
$logo = Join-Path $root 'text2speech\speechy.png'
foreach ($name in 'StoreLogo.png', 'Square150x150Logo.png', 'Square44x44Logo.png') {
    Copy-Item -LiteralPath $logo -Destination (Join-Path $layout "Assets\$name")
}

# Reuse the signing certificate if it exists, so users do not have to trust a new one.
$cert = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object { $_.Subject -eq $publisher -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) } |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1
if (-not $cert) {
    Write-Host "Erzeuge selbstsigniertes Zertifikat $publisher (CurrentUser\My) ..."
    $cert = New-SelfSignedCertificate -Type Custom -Subject $publisher -KeyUsage DigitalSignature `
        -FriendlyName 'Speechy Identity' -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(10) `
        -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3', '2.5.29.19={text}')
}

$msix = Join-Path $here 'speechy-identity.msix'
& $makeappx pack /o /nv /d $layout /p $msix
if ($LASTEXITCODE) { throw 'MakeAppx ist fehlgeschlagen.' }
& $signtool sign /fd SHA256 /sha1 $cert.Thumbprint /s My $msix
if ($LASTEXITCODE) { throw 'SignTool ist fehlgeschlagen.' }
# DER-encoded, so its SHA-1 hash is the thumbprint scripts/win_identity.py checks.
Export-Certificate -Cert $cert -FilePath (Join-Path $here 'speechy-identity.cer') -Type CERT | Out-Null
Remove-Item -LiteralPath $build -Recurse -Force

Write-Host ''
Write-Host 'Fertig: packaging\speechy-identity.msix und packaging\speechy-identity.cer'
Write-Host 'Beide Dateien committen. Das Zertifikat mit privatem Schluessel bleibt in'
Write-Host "CurrentUser\My (Fingerabdruck $($cert.Thumbprint)) und wird nicht committet."
