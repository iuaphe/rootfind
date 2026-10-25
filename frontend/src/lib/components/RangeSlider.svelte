<script lang="ts">
	let {
		label,
		limit,
		lower = $bindable(0),
		upper = $bindable(0)
	}: {
		label: string;
		limit: number;
		lower: number;
		upper: number;
	} = $props();
</script>

<fieldset>
	<legend>{label} <span>{lower}–{upper}{upper === limit ? '+' : ''} in</span></legend>
	<div
		class="range"
		style:--start="{(lower / limit) * 100}%"
		style:--end="{(upper / limit) * 100}%"
	>
		<div class="track"></div>
		<input
			type="range"
			min="0"
			max={limit}
			step="1"
			bind:value={() => lower, (value) => (lower = Math.min(value, upper))}
			aria-label={`Minimum ${label.toLowerCase()}`}
			aria-valuetext={`${lower} inches`}
			style:z-index={lower === limit ? 3 : 1}
		/>
		<input
			type="range"
			min="0"
			max={limit}
			step="1"
			bind:value={() => upper, (value) => (upper = Math.max(value, lower))}
			aria-label={`Maximum ${label.toLowerCase()}`}
			aria-valuetext={upper === limit ? 'No maximum' : `${upper} inches`}
		/>
	</div>
	<div class="endpoints"><span>0 in</span><span>{limit}+ in</span></div>
</fieldset>

<style>
	fieldset {
		min-width: 0;
	}
	legend {
		width: 100%;
		font-weight: 600;
	}
	legend span {
		float: right;
		font-size: 0.875rem;
		font-weight: 400;
		color: var(--muted);
	}
	.range {
		position: relative;
		height: 40px;
		margin: 8px 10px 0;
	}
	.track {
		position: absolute;
		top: 18px;
		right: 0;
		left: 0;
		height: 4px;
		border-radius: 4px;
		background: linear-gradient(
			to right,
			#263833 var(--start),
			var(--accent) var(--start),
			var(--accent) var(--end),
			#263833 var(--end)
		);
	}
	input {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 40px;
		margin: 0;
		appearance: none;
		background: transparent;
		pointer-events: none;
	}
	input::-webkit-slider-thumb {
		width: 20px;
		height: 20px;
		appearance: none;
		border: 2px solid var(--accent);
		border-radius: 50%;
		background: var(--surface-raised);
		cursor: ew-resize;
		pointer-events: auto;
	}
	input::-moz-range-thumb {
		width: 16px;
		height: 16px;
		border: 2px solid var(--accent);
		border-radius: 50%;
		background: var(--surface-raised);
		cursor: ew-resize;
		pointer-events: auto;
	}
	input:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 3px;
		border-radius: 4px;
	}
	.endpoints {
		display: flex;
		justify-content: space-between;
		color: var(--muted);
		font-size: 0.75rem;
	}
</style>
