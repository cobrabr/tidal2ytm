fn main() {
    // The only thing this crate needs from a build script is Tauri itself:
    // `tauri_build::build()` resolves the config, the capability ACL, the
    // `externalBin` sidecars, and the Windows resource file, and it exports
    // `TAURI_ENV_TARGET_TRIPLE` with the triple it was invoked with. That last
    // part is why there is no target-triple constant here — reading the triple
    // from a second source would be a second copy of a value that can drift
    // from the one the bundler used, which is the exact failure the name
    // resolution in `sidecar_path` cannot detect.
    tauri_build::build();
}
