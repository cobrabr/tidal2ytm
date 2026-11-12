import { writeTextFile, stat, remove, rename, mkdir } from "@tauri-apps/plugin-fs";
import { BaseDirectory } from "@tauri-apps/plugin-fs";
import { appDataDir } from "@tauri-apps/api/path";

const MAX_BYTES = 5 * 1024 * 1024;
const KEEP = 3;
const LOG_PATH = "logs/ui.log";

export const UI_LOG_PATH = LOG_PATH;

/**
 * The frontend's own log file and its size, for the diagnostics screen.
 *
 * A file that does not exist yet reports zero bytes rather than failing: the
 * panel must keep its row, and a first run has written nothing to report.
 */
export async function uiLogFile(): Promise<{ path: string; bytes: number }> {
	const dir = await appDataDir();
	const path = `${dir}/logs/ui.log`;
	try {
		const info = await stat(LOG_PATH, { baseDir: BaseDirectory.AppData });
		return { path, bytes: info.size };
	} catch {
		return { path, bytes: 0 };
	}
}

export function uiLog(level: "info" | "error", message: string): void {
	const line = `${new Date().toISOString()} ${level} ${message}\n`;
	void appendLine(line);
}

async function appendLine(line: string): Promise<void> {
	try {
		await mkdir("logs", { baseDir: BaseDirectory.AppData, recursive: true });
	} catch {
		// directory already exists
	}

	// Rotate if the current file has grown past the limit.
	try {
		const info = await stat(LOG_PATH, { baseDir: BaseDirectory.AppData });
		if (info.size >= MAX_BYTES) {
			await rotate();
		}
	} catch {
		// file does not exist yet
	}

	await writeTextFile(LOG_PATH, line, { baseDir: BaseDirectory.AppData, append: true });
}

async function rotate(): Promise<void> {
	// Drop the oldest rotated file.
	try {
		await remove(`logs/ui.${KEEP}.log`, { baseDir: BaseDirectory.AppData });
	} catch {
		// does not exist
	}
	// Shift the remaining ones up.
	for (let i = KEEP - 1; i >= 1; i--) {
		try {
			await rename(`logs/ui.${i}.log`, `logs/ui.${i + 1}.log`, {
				oldPathBaseDir: BaseDirectory.AppData,
				newPathBaseDir: BaseDirectory.AppData,
			});
		} catch {
			// does not exist
		}
	}
	// Rename the current file to the first rotated slot.
	try {
		await rename(LOG_PATH, "logs/ui.1.log", {
			oldPathBaseDir: BaseDirectory.AppData,
			newPathBaseDir: BaseDirectory.AppData,
		});
	} catch {
		// does not exist
	}
}
