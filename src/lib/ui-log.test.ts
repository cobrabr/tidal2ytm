import { describe, expect, it, vi, beforeEach } from "vitest";
import { uiLog, UI_LOG_PATH } from "./ui-log";

vi.mock("@tauri-apps/plugin-fs", () => ({
	writeTextFile: vi.fn(),
	exists: vi.fn(),
	stat: vi.fn(),
	remove: vi.fn(),
	rename: vi.fn(),
	mkdir: vi.fn(),
	BaseDirectory: { AppData: 1 },
}));

import * as fs from "@tauri-apps/plugin-fs";

const stat = vi.mocked(fs.stat);
const writeTextFile = vi.mocked(fs.writeTextFile);
const mkdir = vi.mocked(fs.mkdir);

beforeEach(() => {
	vi.clearAllMocks();
	stat.mockRejectedValue(new Error("not found"));
	mkdir.mockResolvedValue(undefined);
	writeTextFile.mockResolvedValue(undefined);
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
