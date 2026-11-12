<script lang="ts">
	import { Card, CardContent } from "$lib/components/ui/card";
	import type { Snippet } from "svelte";

	interface Props {
		label: string;
		children: Snippet;
	}

	let { label, children }: Props = $props();
</script>

<section>
	<!-- The label is the one place uppercase is allowed, and it is the quietest
	     tier: it names the panel without competing with what the panel holds. -->
	<h2 class="text-[10.5px] tracking-[0.09em] text-text-faint uppercase">{label}</h2>
	<Card>
		<CardContent>
			{@render children()}
		</CardContent>
	</Card>
</section>
