<script lang="ts">
	import { onMount } from "svelte";
	import Home from "./routes/Home.svelte";
	import Diagnostics from "./routes/Diagnostics.svelte";
	import { ROUTES, currentRoute, navigate, type RouteId } from "./lib/router";
	import { getAppInfo } from "./lib/ipc";

	const titles: Record<RouteId, string> = {
		home: "Home",
		library: "Library",
		match: "Match",
		review: "Review",
		transfer: "Transfer",
		diagnostics: "Diagnostics",
	};

	/* The rail's own row: a full-width line, quiet corners, no chrome of its
	   own. The hover tint is the one the shell has always had. */
	const itemBase = "w-full cursor-pointer rounded-lg px-2 py-1 text-left hover:bg-surface-subtle";

	/* The section being shown. The accent's inset edge is the marker, and
	   `aria-current` says the same thing to a screen reader. */
	const itemActive = `${itemBase} bg-surface-subtle text-text-primary shadow-[inset_2px_0_0_var(--brand)]`;

	/* The sections not being shown: present, and quiet. */
	const itemMuted = `${itemBase} text-text-muted`;

	let version = $state<string | null>(null);

	onMount(async () => {
		try {
			version = (await getAppInfo()).version;
		} catch {
			// A version number is not worth an error a person cannot act on, so
			// the rail foot simply carries none. The database path the host
			// also returns belongs to Diagnostics, not here.
		}
	});
</script>

<!--
	The app floats in the window's shell, a gutter on all four sides, with the
	rail and the content panel as two inset surfaces inside it.
-->
<div class="flex gap-3 p-3 h-screen bg-surface-shell">
	<nav aria-label="Sections" class="w-44 shrink-0 flex flex-col rounded-lg border border-line bg-surface-panel p-3">
		<span class="text-brand font-bold">tidal2ytm</span>
		<ul class="mt-3 flex flex-col gap-1">
			{#each ROUTES as route (route)}
				{@const active = $currentRoute === route}
				<li>
					<button
						type="button"
						aria-current={active ? "page" : undefined}
						onclick={() => navigate(route)}
						class={active ? itemActive : itemMuted}
					>
						{titles[route]}
					</button>
				</li>
			{/each}
		</ul>
		{#if version}
			<span class="mt-auto text-text-faint text-xs font-mono tabular-nums">v{version}</span>
		{/if}
	</nav>
	<!--
		Only the content scrolls: the rail is a fixed frame around the app, and a
		rail that scrolled would take its own sections out of reach.
	-->
	<main class="flex-1 min-w-0 overflow-auto rounded-lg border border-line bg-surface-content p-6">
		<!--
			Home and Diagnostics are built; the other four are placeholders whose
			real content lands in Phase 4.
		-->
		{#if $currentRoute === "home"}
			<Home />
		{:else if $currentRoute === "diagnostics"}
			<Diagnostics />
		{:else}
			<section>
				<h1 class="mb-4 text-lg font-semibold">{titles[$currentRoute]}</h1>
				<p class="text-text-faint">Not built yet.</p>
			</section>
		{/if}
	</main>
</div>
