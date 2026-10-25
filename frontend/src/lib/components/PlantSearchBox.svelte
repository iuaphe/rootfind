<script lang="ts">
	import { onDestroy } from 'svelte';
	import { getSearchSuggestions } from '../plant-search.js';
	import {
		applySearchInput,
		normalizeSearchText,
		parseSearchInput,
		setSpecifier,
		tokenizeSearchInput,
		type SearchSuggestion,
		type TaxonomyField,
		type TaxonomySpecifier
	} from '../plant-search-input.js';

	let {
		text = $bindable(''),
		specifiers = $bindable<TaxonomySpecifier[]>([]),
		context = ''
	}: { text?: string; specifiers?: TaxonomySpecifier[]; context?: string } = $props();

	let inputElement: HTMLInputElement | undefined;
	let suggestions = $state<SearchSuggestion[]>([]);
	let active = $state(0);
	let focused = $state(false);
	let open = $state(false);
	let loading = $state(false);
	let error = $state('');
	let suggestionError = $state('');
	let timer: ReturnType<typeof setTimeout> | undefined;
	let controller: AbortController | undefined;
	const expanded = $derived(focused && open);
	const activeId = $derived(
		expanded && suggestions[active] ? `plant-suggestion-${active}` : undefined
	);
	const suggestionTypes = {
		kingdom: { label: 'Kingdom', color: '#72500b' },
		subkingdom: { label: 'Subkingdom', color: '#8a3f19' },
		superdivision: { label: 'Superdivision', color: '#96394b' },
		division: { label: 'Division', color: '#9b286c' },
		division_common: { label: 'Common division name', color: '#7e3e91' },
		class: { label: 'Class', color: '#5d479f' },
		class_common: { label: 'Common class name', color: '#3f579b' },
		subclass: { label: 'Subclass', color: '#285e86' },
		order_name: { label: 'Order', color: '#146674' },
		family: { label: 'Family', color: '#1e6b50' },
		family_common: { label: 'Common family name', color: '#526c21' },
		common: { label: 'Common species name', color: '#78562e' },
		scientific: { label: 'Species', color: '#566171' }
	} satisfies Record<TaxonomyField | 'common' | 'scientific', { label: string; color: string }>;

	function cancelSuggestions() {
		clearTimeout(timer);
		controller?.abort();
		loading = false;
	}

	function requestSuggestions() {
		cancelSuggestions();
		suggestions = [];
		active = 0;
		suggestionError = '';
		const fragment = normalizeSearchText(text.slice(text.lastIndexOf(',') + 1));
		open = focused && fragment.length > 0;
		if (!open || fragment.length > 200) return;
		const params = applySearchInput(new URLSearchParams(context), '', specifiers);
		controller = new AbortController();
		const { signal } = controller;
		loading = true;
		timer = setTimeout(async () => {
			try {
				const result = await getSearchSuggestions(fragment, params, signal);
				if (!signal.aborted) suggestions = result;
			} catch (cause) {
				if (!signal.aborted) {
					suggestionError = cause instanceof Error ? cause.message : 'Unable to load suggestions.';
				}
			} finally {
				if (!signal.aborted) loading = false;
			}
		}, 200);
	}

	function updateInput(value: string, composing = false) {
		text = value;
		error = '';
		if (composing) {
			cancelSuggestions();
			open = false;
			return;
		}
		try {
			const parsed = tokenizeSearchInput(value, specifiers);
			specifiers = parsed.specifiers;
			text = parsed.text;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Check the taxonomy specifier.';
			cancelSuggestions();
			open = false;
			return;
		}
		requestSuggestions();
	}

	function commit() {
		try {
			const parsed = parseSearchInput(text, specifiers);
			text = parsed.text;
			specifiers = parsed.specifiers;
			error = '';
			return true;
		} catch (cause) {
			error = cause instanceof Error ? cause.message : 'Check the taxonomy specifier.';
			return false;
		}
	}

	function accept(suggestion: SearchSuggestion) {
		cancelSuggestions();
		const prefix = text.slice(0, text.lastIndexOf(',') + 1);
		if (suggestion.kind === 'taxonomy') {
			const parsed = parseSearchInput(prefix, specifiers);
			specifiers = setSpecifier(parsed.specifiers, suggestion);
			text = parsed.text;
		} else {
			text = `${prefix}${suggestion.value}`;
			commit();
		}
		error = '';
		open = false;
		suggestions = [];
		inputElement?.focus();
	}

	function remove(field: string) {
		specifiers = specifiers.filter((item) => item.field !== field);
		inputElement?.focus();
		requestSuggestions();
	}

	function keydown(event: KeyboardEvent) {
		if (event.isComposing) return;
		if (event.key === 'Escape' && expanded) {
			event.preventDefault();
			event.stopPropagation();
			cancelSuggestions();
			open = false;
		} else if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && suggestions.length) {
			event.preventDefault();
			open = true;
			active =
				(active + (event.key === 'ArrowDown' ? 1 : -1) + suggestions.length) % suggestions.length;
			document.getElementById(`plant-suggestion-${active}`)?.scrollIntoView({ block: 'nearest' });
		} else if (event.key === 'Enter') {
			if (expanded && suggestions[active]) {
				event.preventDefault();
				accept(suggestions[active]);
			} else if (text.includes('=')) {
				event.preventDefault();
				if (commit()) {
					cancelSuggestions();
					open = false;
				}
			} else {
				cancelSuggestions();
				open = false;
			}
		} else if (event.key === 'Backspace' && !text && specifiers.length) {
			event.preventDefault();
			const last = specifiers[specifiers.length - 1];
			specifiers = specifiers.slice(0, -1);
			text = `${last.field}=${last.value}`;
			requestSuggestions();
		}
	}

	onDestroy(cancelSuggestions);
</script>

<div class="search-box">
	<div class={['input-shell', { invalid: !!error }]}>
		{#each specifiers as specifier (specifier.field)}
			<span class="chip">
				<span>{specifier.field}={specifier.value}</span>
				<button
					type="button"
					aria-label={`Remove ${specifier.field}=${specifier.value}`}
					onclick={() => remove(specifier.field)}>×</button
				>
			</span>
		{/each}
		<input
			{@attach (node) => {
				inputElement = node;
				return () => {
					inputElement = undefined;
				};
			}}
			id="plant-search"
			name="search"
			type="text"
			role="combobox"
			autocomplete="off"
			spellcheck="false"
			value={text}
			placeholder={specifiers.length
				? 'Add a plant name or another filter'
				: 'Plant name or family=Aceraceae'}
			aria-autocomplete="list"
			aria-expanded={expanded}
			aria-controls="plant-suggestions"
			aria-activedescendant={activeId}
			aria-invalid={!!error}
			aria-describedby={`search-help search-status${error ? ' search-input-error' : ''}`}
			maxlength="1500"
			oninput={(event) =>
				updateInput(event.currentTarget.value, event instanceof InputEvent && event.isComposing)}
			oncompositionend={(event) => updateInput(event.currentTarget.value)}
			onfocus={() => {
				focused = true;
				requestSuggestions();
			}}
			onblur={() => {
				focused = false;
				open = false;
				cancelSuggestions();
				commit();
			}}
			onkeydown={keydown}
		/>
	</div>
	{#if error}<p id="search-input-error" class="input-error" role="alert">{error}</p>{/if}
	{#if expanded}
		<div class="dropdown">
			<ul
				id="plant-suggestions"
				role="listbox"
				aria-label="Plant search suggestions"
				aria-busy={loading}
			>
				{#each suggestions as suggestion, index (`${suggestion.kind}:${suggestion.field}:${suggestion.value}`)}
					{@const type =
						suggestionTypes[suggestion.kind === 'taxonomy' ? suggestion.field : suggestion.kind]}
					<li role="presentation">
						<button
							id={`plant-suggestion-${index}`}
							type="button"
							role="option"
							tabindex="-1"
							aria-selected={active === index}
							onpointerdown={(event) => event.preventDefault()}
							onclick={() => accept(suggestion)}
							style:--suggestion-color={type.color}
						>
							<span class={{ scientific: suggestion.kind === 'scientific' }}>
								{suggestion.value}
							</span>
							<small>{type.label}</small>
						</button>
					</li>
				{/each}
			</ul>
			{#if loading}
				<p role="status">Finding suggestions…</p>
			{:else if suggestionError}
				<p role="status">{suggestionError}</p>
			{:else if !suggestions.length}
				<p role="status">No suggestions. Press Enter to search your text.</p>
			{/if}
		</div>
	{/if}
</div>

<style>
	.search-box {
		position: relative;
		flex: 1;
		min-width: 0;
	}
	.input-shell {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 6px;
		min-height: 49px;
		padding: 6px 10px;
		border: 1px solid #809c90;
		border-radius: 6px;
		background: #f7faf8;
	}
	.input-shell:focus-within {
		outline: 2px solid #254d46;
		outline-offset: 2px;
	}
	.input-shell.invalid {
		border-color: #922f2f;
	}
	input {
		flex: 1;
		min-width: 140px;
		width: 100%;
		padding: 5px 3px;
		border: 0;
		background: transparent;
		outline: none;
	}
	.chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		max-width: 100%;
		padding: 3px 4px 3px 9px;
		border: 1px solid #a8c8b4;
		border-radius: 5px;
		background: #e7f1e9;
		color: #1c5038;
		font-size: 0.875rem;
	}
	.chip > span {
		overflow-wrap: anywhere;
		min-width: 0;
	}
	.chip button {
		flex-shrink: 0;
		width: 28px;
		height: 28px;
		border-radius: 3px;
		cursor: pointer;
		font-size: 1.25rem;
	}
	.chip button:hover {
		background: #d3e7d9;
	}
	button:focus-visible {
		outline: 2px solid #254d46;
		outline-offset: 2px;
	}
	.dropdown {
		position: absolute;
		top: calc(100% + 8px);
		left: 0;
		right: 0;
		z-index: 5;
		max-height: min(360px, 50dvh);
		overflow-y: auto;
		border: 1px solid #cbd9d3;
		border-radius: 8px;
		background: white;
		box-shadow: 0 8px 24px #254d4626;
	}
	ul {
		margin: 0;
		padding: 4px;
		list-style: none;
	}
	li button {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 2px;
		width: 100%;
		padding: 10px;
		border-radius: 4px;
		text-align: left;
		cursor: pointer;
		overflow-wrap: anywhere;
	}
	li button[aria-selected='true'],
	li button:hover {
		background: #e7f1e9;
	}
	small {
		color: var(--suggestion-color);
		font-size: 0.75rem;
		font-style: italic;
	}
	.scientific {
		font-style: italic;
	}
	.dropdown p {
		padding: 10px 14px;
		font-size: 0.8125rem;
		color: #60776c;
	}
	.input-error {
		margin-top: 6px;
		color: #922f2f;
		font-size: 0.8125rem;
	}
	@media (max-width: 600px) {
		input {
			min-width: min(140px, 100%);
		}
	}
</style>
