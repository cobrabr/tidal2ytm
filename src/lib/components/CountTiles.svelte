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
-->
<dl class="grid grid-cols-5 gap-2">
	{#each segments as segment (segment.key)}
		<div class="flex flex-col-reverse rounded-md border border-line bg-surface-content p-2.5">
			<dt class="text-[10px] tracking-[0.07em] text-text-muted uppercase">{segment.label}</dt>
			<dd class={`font-mono text-[15px] font-semibold tabular-nums ${segment.text}`}>{segment.count}</dd>
		</div>
	{/each}
</dl>
