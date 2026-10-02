import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// Tauri serves the built assets from disk, so asset URLs must be relative.
export default defineConfig({
	plugins: [svelte()],
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
});
