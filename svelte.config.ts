import { vitePreprocess, type SvelteConfig } from "@sveltejs/vite-plugin-svelte";

// `{ script: true }` is required: without it, TypeScript in <script> blocks is
// not processed. This is the documented requirement for a Vite SPA with no
// SvelteKit. The file is `.ts` rather than `.js` because a config module should
// be a config module, and `SvelteConfig` gives it something to check against.
const config: SvelteConfig = {
	preprocess: vitePreprocess({ script: true }),
};

export default config;
