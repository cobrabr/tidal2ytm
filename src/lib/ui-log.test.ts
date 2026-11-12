import { describe, expect, it, vi, beforeEach } from "vitest";
import { uiLog, uiLogFile, UI_LOG_PATH } from "./ui-log";

vi.mock("@tauri-apps/plugin-fs", () => ({
	writeTextFile: vi.fn(),
	exists: vi.fn(),
	stat: vi.fn(),
	remove: vi.fn(),
	rename: vi.fn(),
	mkdir: vi.fn(),
	BaseDirectory: { AppData: 1 },
}));

vi.mock("@tauri-apps/api/path", () => ({
	appDataDir: vi.fn(),
}));

import * as fs from "@tauri-apps/plugin-fs";
import * as path from "@tauri-apps/api/path";
import type { FileInfo } from "@tauri-apps/plugin-fs";

const stat = vi.mocked(fs.stat);
const writeTextFile = vi.mocked(fs.writeTextFile);
const mkdir = vi.mocked(fs.mkdir);
const appDataDir = vi.mocked(path.appDataDir);

beforeEach(() => {
	vi.clearAllMocks();
	stat.mockRejectedValue(new Error("not found"));
	mkdir.mockResolvedValue(undefined);
	writeTextFile.mockResolvedValue(undefined);
	appDataDir.mockResolvedValue("/appdata");
});

describe("uiLog", () => {
	it("exports the log path", () => {
		expect(UI_LOG_PATH).toBe("logs/ui.log");
	});

	it("appends a line", async () => {
		uiLog("info", "hello");
		// wait for async
		await new Promise((r) => setTimeout(r, 50));
		expect(writeTextFile).toHaveBeenCalled();
	});
});

describe("uiLogFile", () => {
	it("reports the log file and the size it has", async () => {
		stat.mockResolvedValue({ size: 2048 } as FileInfo);

		expect(await uiLogFile()).toEqual({ path: "/appdata/logs/ui.log", bytes: 2048 });
	});

	// A first run has written nothing yet, and the panel must keep its row
	// rather than lose it to a failure the person cannot act on.
	it("reports zero bytes for a file that does not exist yet", async () => {
		stat.mockRejectedValue(new Error("not found"));

		expect(await uiLogFile()).toEqual({ path: "/appdata/logs/ui.log", bytes: 0 });
	});
});
