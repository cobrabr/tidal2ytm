<script lang="ts">
	import Home from "./routes/Home.svelte";
	import Diagnostics from "./routes/Diagnostics.svelte";
	import { ROUTES, currentRoute, navigate, type RouteId } from "./lib/router";

	const titles: Record<RouteId, string> = {
		home: "Home",
		library: "Library",
		match: "Match",
		review: "Review",
		transfer: "Transfer",
		diagnostics: "Diagnostics",
	};
</script>

<div class="shell">
	<nav aria-label="Sections">
		<span class="brand">tidal2ytm</span>
		<ul>
			{#each ROUTES as route (route)}
				<li>
					<button
						type="button"
						aria-current={$currentRoute === route ? "page" : undefined}
						onclick={() => navigate(route)}
					>
						{titles[route]}
					</button>
				</li>
			{/each}
		</ul>
	</nav>
	<main>
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
				<h1>{titles[$currentRoute]}</h1>
				<p class="placeholder">Not built yet.</p>
			</section>
		{/if}
	</main>
</div>

<style>
	.shell {
		display: grid;
		grid-template-columns: 200px 1fr;
		height: 100vh;
	}
	nav {
		background: var(--surface-1);
		border-right: 1px solid var(--border);
		padding: var(--space-3);
	}
	.brand {
		display: block;
		font-weight: 600;
		margin-bottom: var(--space-4);
	}
	ul {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	button {
		width: 100%;
		text-align: left;
		background: transparent;
		border: 0;
		border-radius: var(--radius);
		color: var(--text-1);
		cursor: pointer;
		font: inherit;
		padding: var(--space-1) var(--space-2);
	}
	button:hover {
		background: var(--surface-2);
	}
	button[aria-current="page"] {
		background: var(--surface-2);
		color: var(--text-0);
	}
	main {
		overflow: auto;
		padding: var(--space-4);
	}
	h1 {
		font-size: 18px;
		font-weight: 600;
		margin: 0 0 var(--space-3);
	}
	.placeholder {
		color: var(--text-2);
	}
</style>
