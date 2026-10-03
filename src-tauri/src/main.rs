// Hide the console window on Windows release builds; debug keeps it so the
// sidecar's forwarded stderr is visible while developing.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // The log sink is installed by `run()`, not here: `run()` is what starts the
    // sidecar whose stderr it drains, and a second entry point must not be able
    // to forget the call.
    tidal2ytm_host::run();
}
