import { describe, expect, it } from "vitest";
import { formatBytes } from "./format";

describe("formatBytes", () => {
	it("reads a count below a kilobyte in bytes", () => {
		expect(formatBytes(0)).toBe("0 B");
	});

	it("reads a count below a megabyte in kilobytes", () => {
		expect(formatBytes(1024)).toBe("1.0 KB");
	});

	it("reads a count from a megabyte up in megabytes", () => {
		expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
	});
});
