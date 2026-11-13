<script lang="ts">
	import { planSegments } from "../plan-status";

	interface Props {
		counts: Record<string, number>;
	}

	let { counts }: Props = $props();

	let segments = $derived(planSegments(counts));
</script>

<!--
	A definition list, so each count is a definition a reader (and a test) can
	find and sum. The count is the definition and the label is its term; the
	tiles are laid out with the number on top, because the number is what the
	eye goes to first.

	The row flows rather than holding a fixed number of columns: the number of
	tiles is whatever the plan holds, and a sixth status must not leave a
	quarter-wide tile alone on a row of its own. The tiles share the width
	evenly and never fall below a readable width, so a label never has to
	wrap to stay legible.
-->
<dl class="flex flex-wrap gap-2">
	{#each segments as segment (segment.key)}
		<!--
			The width cap stops a tile that wraps onto a row of its own from
			stretching across the whole panel.
		-->
		<div class="flex min-w-28 max-w-64 flex-1 flex-col-reverse rounded-md border border-line bg-surface-content p-2.5">
			<dt class="text-[10px] tracking-[0.07em] text-text-muted uppercase">{segment.label}</dt>
			<dd class={`font-mono text-[15px] font-semibold tabular-nums ${segment.text}`}>{segment.count}</dd>
		</div>
	{/each}
</dl>
