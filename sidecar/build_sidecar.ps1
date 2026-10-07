# Build the sidecar and stage it where Tauri expects to find it.
#
# Usage: pwsh sidecar/build_sidecar.ps1
$ErrorActionPreference = 'Stop'

$repo = Resolve-Path (Join-Path $PSScriptRoot '..')
# The host triple, because `tauri_build` reads cargo's TARGET and no --target
# is passed; the staged name has to be the triple Tauri looks for.
$triple = (rustc --print host-tuple).Trim()
if (-not $triple) { throw 'could not determine the host target triple' }
$target = Join-Path $repo 'src-tauri\binaries'
# PyInstaller resolves the spec's script path against the spec file's own
# directory, but resolves distpath and workpath against the invoking working
# directory — left to itself it wrote the sidecar into the repository root's
# `dist/`, which is the frontend's build output and Tauri's `frontendDist`. Both
# go under `sidecar/`, which is what `.gitignore` already ignores.
$dist = Join-Path $repo 'sidecar\dist'
$work = Join-Path $repo 'sidecar\build'
# A onefile build writes one executable at the distpath root. A onedir build
# would write it to `dist\tidal2ytm-sidecar\tidal2ytm-sidecar.exe` and leave
# nothing at $built, which is what the `Test-Path` below is for.
$built = Join-Path $dist 'tidal2ytm-sidecar.exe'

# Clear both output directories before building. `--noconfirm` removes only the
# output PyInstaller is about to write, so on a onedir regression the previous
# good `dist\tidal2ytm-sidecar.exe` would survive untouched and the `Test-Path`
# below would pass on a stale binary, staging a sidecar that no longer matches
# the source. `-PathType Leaf` is in that check too: plain `Test-Path` returns
# true for a directory, which would accept a `COLLECT`'s output directory as the
# executable.
Remove-Item -Recurse -Force $dist, $work -ErrorAction SilentlyContinue

Push-Location $repo
try {
    uv run --group build pyinstaller sidecar\tidal2ytm-sidecar.spec --noconfirm --distpath $dist --workpath $work
}
finally {
    Pop-Location
}

if (-not (Test-Path -PathType Leaf $built)) {
    throw "PyInstaller did not produce $built"
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Force $built (Join-Path $target "tidal2ytm-sidecar-$triple.exe")
Write-Host "staged $target\tidal2ytm-sidecar-$triple.exe"
