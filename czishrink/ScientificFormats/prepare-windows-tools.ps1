# SPDX-FileCopyrightText: 2026 czicompress contributors
# SPDX-License-Identifier: GPL-3.0-or-later
param([string]$ToolsDirectory = (Join-Path $PSScriptRoot '../../artifacts/format-tools'))
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ToolsDirectory = [IO.Path]::GetFullPath($ToolsDirectory)
New-Item -ItemType Directory -Path $ToolsDirectory -Force | Out-Null
$downloadDirectory = Join-Path $ToolsDirectory 'downloads'
$pythonDirectory = Join-Path $ToolsDirectory 'python'
$hdfDirectory = Join-Path $ToolsDirectory 'hdf5'
New-Item -ItemType Directory -Path $downloadDirectory, $pythonDirectory, $hdfDirectory -Force | Out-Null

if (-not (Test-Path -LiteralPath (Join-Path $pythonDirectory 'python.exe'))) {
    $pythonArchive = Join-Path $downloadDirectory 'python-3.13.17-embed-amd64.zip'
    Invoke-WebRequest 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip' -OutFile $pythonArchive
    Expand-Archive -LiteralPath $pythonArchive -DestinationPath $pythonDirectory -Force
    @('python313.zip', '.', 'Lib/site-packages', '..', 'import site') | Set-Content -LiteralPath (Join-Path $pythonDirectory 'python313._pth') -Encoding ASCII
}

if (-not (Test-Path -LiteralPath (Join-Path $hdfDirectory 'h5repack.exe'))) {
    $hdfArchive = Join-Path $downloadDirectory 'hdf5-1.14.6-win-vs2022_cl.zip'
    $checksumsPath = Join-Path $downloadDirectory 'hdf5-1.14.6.sha256sums.txt'
    $releaseUrl = 'https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.6'
    Invoke-WebRequest "$releaseUrl/hdf5-1.14.6-win-vs2022_cl.zip" -OutFile $hdfArchive
    Invoke-WebRequest "$releaseUrl/hdf5-1.14.6.sha256sums.txt" -OutFile $checksumsPath
    $checksumLine = Get-Content -LiteralPath $checksumsPath | Where-Object { $_ -match 'hdf5-1\.14\.6-win-vs2022_cl\.zip$' } | Select-Object -First 1
    if (-not $checksumLine -or (Get-FileHash -LiteralPath $hdfArchive -Algorithm SHA256).Hash -ne ($checksumLine -split '\s+')[0]) {
        throw 'Official HDF5 archive checksum does not match.'
    }
    $unpackDirectory = Join-Path $downloadDirectory 'hdf5-unpack'
    Expand-Archive -LiteralPath $hdfArchive -DestinationPath $unpackDirectory -Force
    $nested = Get-ChildItem -LiteralPath $unpackDirectory -Recurse -File -Filter 'HDF5-*-win64.zip' | Select-Object -First 1
    if ($null -ne $nested) { Expand-Archive -LiteralPath $nested.FullName -DestinationPath (Join-Path $unpackDirectory 'distribution') -Force }
    $repack = Get-ChildItem -LiteralPath $unpackDirectory -Recurse -File -Filter h5repack.exe | Select-Object -First 1
    if ($null -eq $repack) { throw 'Official HDF5 package has no h5repack executable.' }
    Get-ChildItem -LiteralPath $repack.DirectoryName -File | Where-Object { $_.Extension -eq '.dll' -or $_.Name -in @('h5repack.exe','h5diff.exe','h5dump.exe') } | Copy-Item -Destination $hdfDirectory
    Get-ChildItem -LiteralPath $unpackDirectory -Recurse -File | Where-Object { $_.Name -match '^(COPYING|LICENSE|COPYRIGHT)' } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $hdfDirectory ($_.Directory.Name + '-' + $_.Name)) }
}

$python = Join-Path $pythonDirectory 'python.exe'
if (-not (Test-Path -LiteralPath (Join-Path $pythonDirectory 'Lib/site-packages/pip'))) {
    $bootstrap = Join-Path $downloadDirectory 'get-pip.py'
    Invoke-WebRequest 'https://bootstrap.pypa.io/get-pip.py' -OutFile $bootstrap
    & $python $bootstrap --no-cache-dir --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw 'Portable pip setup failed.' }
}
& $python -m pip --isolated install --only-binary=:all: --no-cache-dir --no-warn-script-location -r (Join-Path $PSScriptRoot 'requirements-lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Portable scientific codec installation failed.' }
Copy-Item -Path (Join-Path $PSScriptRoot '*.py') -Destination $ToolsDirectory
Copy-Item -Path (Join-Path $PSScriptRoot 'requirements*.txt') -Destination $ToolsDirectory
Write-Output "Prepared scientific tools in $ToolsDirectory"
