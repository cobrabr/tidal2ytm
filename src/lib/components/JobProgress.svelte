<script lang="ts">
	import type { Progress } from "../jobs";

	interface Props {
		progress: Progress;
		onCancel?: () => void;
	}

	let { progress, onCancel }: Props = $props();
</script>

<div class="job" role="status" data-state={progress.state}>
	<span class="state">{progress.state}</span>
	<span class="count"
		>{progress.done}{#if progress.total !== null}
			/ {progress.total}{/if}</span
	>
	{#if progress.state === "running"}
		<button type="button" onclick={() => onCancel?.()}>stop</button>
	{:else if progress.state === "cancelling"}
		<button type="button" disabled>stopping</button>
	{/if}
</div>

<style>
	.job {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
	}
	.state[data-state="running"] {
		color: var(--ok);
	}
	.state[data-state="cancelling"] {
		color: var(--warn);
	}
	.state[data-state="cancelled"] {
		color: var(--bad);
	}
	.state[data-state="failed"] {
		color: var(--bad);
	}
</style>
