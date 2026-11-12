import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { svelteTesting } from "@testing-library/svelte/vite";
import { fileURLToPath } from "node:url";
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
//
// `resolve.alias` is the runtime half of `$lib`. The vendored shadcn-svelte
// components import each other and the `cn` helper as `$lib/utils.js`, and
// TypeScript resolves the same specifier through `compilerOptions.paths`.
// Without the alias Vite would read `$lib` as a bare package name and the
// build would fail on the first vendored component.
//
// `tailwindcss()` is registered ahead of `svelte()` so Tailwind's source scan
// sees the component sources and the classes the components carry.
export default defineConfig({
	plugins: [tailwindcss(), svelte(), svelteTesting()],
	resolve: {
		alias: {
			$lib: fileURLToPath(new URL("./src/lib", import.meta.url)),
		},
	},
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
