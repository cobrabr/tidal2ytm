<script lang="ts">
	import type { Progress } from "../jobs";

	interface Props {
		progress: Progress;
		onCancel?: () => void;
	}

	let { progress, onCancel }: Props = $props();
</script>

<div class="job" role="status" data-state={progress.state}>
	<div class="line">
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
	{#if progress.state === "running" || progress.state === "cancelling" || progress.state === "done"}
		<!-- No value until the fetch has finished: Tidal's favourites endpoint
		     reports no count, and a bar that guessed one would be measuring the
		     guess rather than the sync. -->
		<progress
			aria-label="job progress"
			value={progress.total === null ? undefined : progress.done}
			max={progress.total ?? undefined}
		></progress>
	{/if}
</div>

<style>
	.job {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
	}
	.line {
		display: flex;
		align-items: baseline;
		gap: var(--space-2);
	}
	progress {
		width: 100%;
		height: 6px;
	}
</style>
