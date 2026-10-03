// Hide the console window on Windows release builds; debug keeps it so the
// sidecar's forwarded stderr is visible while developing.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tidal2ytm_host::logging::install_debug_logger();
    tidal2ytm_host::run();
}
