import { svelte } from "@sveltejs/vite-plugin-svelte";
import { svelteTesting } from "@testing-library/svelte/vite";
import { defineConfig } from "vitest/config";

// `test` lives here, not in a second config file. Vitest reads
// `vitest.config.*` *or* `vite.config.*`, never both, so while a standalone
// `vitest.config.ts` existed it shadowed everything below: the plugins and any
// alias declared here were absent from every test run. That is the shape of
// defect this Tauri boundary is most likely to grow — the values a test needs
// are exactly the ones declared in this file — and it is invisible, because a
// shadowed config still runs a green suite.
//
// `defineConfig` comes from `vitest/config` so the `test` key is typed rather
// than passed off as an unknown Vite option. `svelteTesting()` is inert unless
// Vitest is running, so it costs a production build nothing.
export default defineConfig({
	plugins: [svelte(), svelteTesting()],
	// Tauri serves the built assets from disk, so asset URLs must be relative.
	base: "./",
	build: {
		outDir: "dist",
		emptyOutDir: true,
		target: "es2022",
	},
	server: {
		port: 5173,
		strictPort: true,
	},
	clearScreen: false,
	test: {
		environment: "jsdom",
		setupFiles: ["./vitest.setup.ts"],
		include: ["src/**/*.test.ts"],
	},
});
