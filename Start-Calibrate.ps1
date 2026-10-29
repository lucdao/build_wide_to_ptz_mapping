param(
    [switch]$Configure,
    [switch]$ReuseLastCorners,
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot

function Get-VerifiedArchive($Specification, [string]$Destination) {
    if ((Test-Path -LiteralPath $Destination) -and
        (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Specification.sha256) {
        return
    }
    $downloadPath = $Destination + '.download'
    Invoke-WebRequest -UseBasicParsing -Uri $Specification.url -OutFile $downloadPath
    if ((Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Specification.sha256) {
        throw 'Tep tai ve sai SHA256. Hay chay lai khi ket noi on dinh.'
    }
    Move-Item -LiteralPath $downloadPath -Destination $Destination -Force
}

try {
    $architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    if ($architecture -ne 'AMD64') { throw 'Bo khoi chay nay danh cho Windows x64.' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'bootstrap-windows.json') -Raw | ConvertFrom-Json
    $runtimeRoot = Join-Path $PSScriptRoot '.runtime'
    $runtimeDirectory = Join-Path $runtimeRoot 'windows-x64'
    $cacheDirectory = Join-Path $runtimeRoot 'cache'
    New-Item -ItemType Directory -Path $runtimeDirectory, $cacheDirectory -Force | Out-Null
    $runtimePython = Join-Path $runtimeDirectory 'python.exe'
    $pythonStamp = Join-Path $runtimeDirectory '.python.sha256'
    $savedPythonHash = if (Test-Path -LiteralPath $pythonStamp) { (Get-Content -LiteralPath $pythonStamp -Raw).Trim() } else { '' }
    if (-not (Test-Path -LiteralPath $runtimePython) -or $savedPythonHash -ne $manifest.python.sha256) {
        Write-Host 'Dang chuan bi Python rieng trong thu muc chuong trinh...'
        $archive = Join-Path $cacheDirectory 'python-3.12.10.zip'
        Get-VerifiedArchive $manifest.python $archive
        Expand-Archive -LiteralPath $archive -DestinationPath $runtimeDirectory -Force
        [IO.File]::WriteAllText($pythonStamp, $manifest.python.sha256)
    }
    # All paths are relative to the embedded interpreter: a relocated clone
    # must never retain the developer machine's Python/source paths.
    [IO.File]::WriteAllLines((Join-Path $runtimeDirectory 'python312._pth'), @(
        'python312.zip', '.', 'Lib\site-packages', '..\..', 'import site'
    ))
    $sitePackages = Join-Path $runtimeDirectory 'Lib\site-packages'
    if (-not (Test-Path -LiteralPath (Join-Path $sitePackages 'pip\__main__.py'))) {
        $pipArchive = Join-Path $cacheDirectory 'pip-26.2.2.zip'
        Get-VerifiedArchive $manifest.pip $pipArchive
        New-Item -ItemType Directory -Path $sitePackages -Force | Out-Null
        Expand-Archive -LiteralPath $pipArchive -DestinationPath $sitePackages -Force
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PIP_CACHE_DIR = Join-Path $cacheDirectory 'pip'
    $env:TORCH_HOME = Join-Path $runtimeRoot 'model-cache\torch'

    $requirements = Join-Path $PSScriptRoot 'requirements-portable.lock'
    $stamp = Join-Path $runtimeDirectory '.requirements.sha256'
    $wantedHash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $savedHash = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
    $dependenciesAvailable = $false
    try {
        & $runtimePython -c 'import cv2, numpy, scipy, torch, kornia, onvif, zeep, requests' 2>$null
        $dependenciesAvailable = $LASTEXITCODE -eq 0
    } catch { }
    if ($savedHash -ne $wantedHash -or -not $dependenciesAvailable) {
        Write-Host 'Dang tai thu vien CPU. Chi lan dau can Internet va co the mat nhieu phut...'
        & $runtimePython -m pip install --disable-pip-version-check --no-warn-script-location setuptools==84.2.1 wheel==0.48.0
        if ($LASTEXITCODE -ne 0) { throw 'Khong cap nhat duoc pip.' }
        & $runtimePython -m pip install --disable-pip-version-check --no-warn-script-location --no-build-isolation -r $requirements
        if ($LASTEXITCODE -ne 0) { throw 'Khong cai duoc thu vien trong requirements.txt.' }
        [IO.File]::WriteAllText($stamp, $wantedHash)
    }

    & $runtimePython (Join-Path $PSScriptRoot 'prepare_models.py')
    if ($LASTEXITCODE -ne 0) { throw 'Khong chuan bi duoc ALIKED/LightGlue. Hay kiem tra ket noi Internet va mo lai cong cu.' }
    if ($PrepareOnly) { Write-Host 'MOI TRUONG VA MODEL DA SAN SANG.'; exit 0 }

    $runArguments = @((Join-Path $PSScriptRoot 'easy_calibration.py'))
    if ($Configure) { $runArguments += '--configure' }
    if ($ReuseLastCorners) { $runArguments += '--reuse-last-corners' }
    & $runtimePython @runArguments
    exit $LASTEXITCODE
} catch {
    Write-Host ('LOI: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
