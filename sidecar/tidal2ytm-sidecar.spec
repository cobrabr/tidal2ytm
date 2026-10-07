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

# `SPECPATH` is this spec's own directory (`sidecar/`), not the repository root,
# and it is what makes the script path below relative to `sidecar/`. Only that
# path resolution is spec-relative: `build_main.py` joins a relative script onto
# `spec_dir`, so written as `sidecar/...` the path is looked for at
# `sidecar/sidecar/...` and the build fails. The working directory is *not*
# changed — PyInstaller never chdirs — so `distpath` and `workpath` still resolve
# against the invoking directory, which is why build_sidecar.ps1 passes them
# explicitly and where it puts them matters.
# `tidal2ytm_sidecar` resolves from this directory; `tidal2ytm` resolves from the
# editable install in the uv-managed venv, which is what the `hiddenimports`
# entries below rely on too.
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
        # The sidecar answers questions about the outside world and nothing
        # else, so nothing in the frozen graph reaches a terminal UI today.
        # This is a belt-and-braces assertion of that, and it stays cheap: the
        # build already excludes these, so the assertion costs a few lines
        # rather than a few megabytes. It is here because the *cost* of being
        # wrong is not cheap — a onefile build excludes modules a larger import
        # graph would have pulled in, so if a later op reaches `cli.py` the
        # build stays green and the missing import surfaces at sidecar runtime
        # on the user's machine. PyInstaller does follow function-level
        # imports, so this is not about hiding imports from the analyser.
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
    # Load-bearing for Phase 2, and not a leftover from the CLI. `state::spawn`
    # hands the sidecar's stderr to the log facade `run()` installed, and that
    # drain is the only route a forwarded diagnostic has to the host's log
    # file -- which Phase 4 creates; until then a release build installs no sink
    # at all (`logging.rs` returns early unless `debug_assertions`) and drops the
    # lines. The route is what has to survive the change, not the destination.
    # The host drains a pipe, not a console, so `False` costs it nothing by
    # itself -- but `False` swaps in the windowed bootloader, which diverts an
    # unhandled exception's traceback into a window instead of stderr
    # (`disable_windowed_traceback` defaults to False, so the dump is on). So
    # "tidying" this to False for a desktop app would move the sidecar's crash
    # diagnostics off the pipe while every gate stayed green -- no gate reads a
    # .spec file, so flipping this one exercises nothing anywhere. Phase 4
    # revisits it once the host writes its own log file.
    console=True,
    exclude_binaries=False,
    upx=False,
)
