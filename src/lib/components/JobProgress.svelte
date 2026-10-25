<script lang="ts">
	import type { Progress } from "../jobs";

	interface Props {
		progress: Progress;
		onCancel?: () => void;
	}

	let { progress, onCancel }: Props = $props();
</script>

<div class="job" role="status" data-state={progress.state}>
	<span
		class="state"
		style:color={progress.state === "running" || progress.state === "done"
			? "var(--ok)"
			: progress.state === "cancelling"
				? "var(--warn)"
				: "var(--bad)"}>{progress.state}</span
	>
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
</style>
