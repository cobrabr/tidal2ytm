# PyInstaller spec for the tidal2ytm sidecar.
#
# Build through: pwsh sidecar/build_sidecar.ps1
#
# One file, not one directory. Tauri stages a sidecar by copying the single
# binary named in `bundle.externalBin`, so a onedir build would reach the bundle
# without the `_internal` directory its launcher imports from and would die on
# startup. The copy step in build_sidecar.ps1 adds the target triple, because
# that is what Tauri looks for.
"""PyInstaller build for the tidal2ytm sidecar."""

from pathlib import Path

# `SPECPATH` is this spec's own directory (`sidecar/`), not the repository root.
# PyInstaller runs a spec with the working directory set there, so the script
# path below is relative to `sidecar/` too: written as `sidecar/...` it is
# looked for at `sidecar/sidecar/...` and the build fails. `tidal2ytm_sidecar`
# resolves from this directory; `tidal2ytm` resolves from the editable install in
# the uv-managed venv, which is what the `hiddenimports` entries below rely on
# too.
#
# The entry script is `tidal2ytm_sidecar_entry.py` rather than the package's own
# `__main__.py`: PyInstaller freezes the entry script as a bare `__main__` with no
# `__package__`, and `__main__.py`'s relative import fails only in the built
# artefact. See that module's docstring.
analysis = Analysis(  # noqa: F821 - injected by PyInstaller
    ["tidal2ytm_sidecar_entry.py"],
    pathex=[str(Path(SPECPATH).resolve())],
    binaries=[],
    datas=[],
    hiddenimports=[
        "tidal2ytm.auth",
        "tidal2ytm.logging_setup",
        "tidal2ytm.paths",
        "tidalapi",
        "ytmusicapi",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The sidecar never touches a terminal UI, and PyInstaller cannot see
        # that these are dead because the CLI imports them behind a flag.
        "rich",
        "readchar",
        "tkinter",
        "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)  # noqa: F821
# No COLLECT, and `exclude_binaries` stays False: that pair is what makes the
# output one self-contained file instead of a launcher plus a sibling directory.
executable = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="tidal2ytm-sidecar",
    console=True,
    exclude_binaries=False,
    upx=False,
)
