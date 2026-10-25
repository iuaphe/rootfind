export const taxonomyFields = [
	'kingdom',
	'subkingdom',
	'superdivision',
	'division',
	'division_common',
	'class',
	'class_common',
	'subclass',
	'order_name',
	'family',
	'family_common'
] as const;

export type TaxonomyField = (typeof taxonomyFields)[number];
export type TaxonomySpecifier = { field: TaxonomyField; value: string };
export type SearchSuggestion =
	| { kind: 'taxonomy'; field: TaxonomyField; value: string }
	| { kind: 'common' | 'scientific'; field: null; value: string };

export function isTaxonomyField(field: string): field is TaxonomyField {
	return (taxonomyFields as readonly string[]).includes(field);
}

export function normalizeSearchText(text: string): string {
	return text.trim().replace(/\s+/g, ' ');
}

/** One value per rank; entering that rank again replaces its previous chip. */
export function setSpecifier(
	specifiers: TaxonomySpecifier[],
	specifier: TaxonomySpecifier
): TaxonomySpecifier[] {
	const index = specifiers.findIndex(({ field }) => field === specifier.field);
	return index < 0
		? [...specifiers, specifier]
		: specifiers.map((item, i) => (i === index ? specifier : item));
}

function parseSpecifier(text: string): TaxonomySpecifier | null {
	const equals = text.indexOf('=');
	if (equals < 0) return null;
	const field = text.slice(0, equals).trim().toLowerCase();
	if (!isTaxonomyField(field)) throw new Error(`Unknown taxonomy field “${field}”.`);
	const value = normalizeSearchText(text.slice(equals + 1));
	const limit = field === 'kingdom' ? 50 : 100;
	if (!value) throw new Error(`Enter a value after ${field}=.`);
	if (value.length > limit) throw new Error(`${field} must not exceed ${limit} characters.`);
	return { field, value };
}

/** Commit comma-delimited specifiers, leaving the unfinished segment editable. */
export function tokenizeSearchInput(input: string, existing: TaxonomySpecifier[]) {
	const parts = input.split(',');
	const tail = parts.pop() ?? '';
	let specifiers = existing;
	const text: string[] = [];
	for (const part of parts) {
		const specifier = parseSpecifier(part);
		if (specifier) specifiers = setSpecifier(specifiers, specifier);
		else if (part.trim()) text.push(part.trim());
	}
	return { specifiers, text: [...text, tail].join(', ') };
}

/** Finalize the last segment on blur/submit, keeping free text as a name query. */
export function parseSearchInput(input: string, existing: TaxonomySpecifier[]) {
	const parsed = tokenizeSearchInput(`${input},`, existing);
	const text = normalizeSearchText(
		parsed.text
			.split(',')
			.map((part) => part.trim())
			.filter(Boolean)
			.join(', ')
	);
	if (text.length > 200) throw new Error('The plant name search must not exceed 200 characters.');
	return { specifiers: parsed.specifiers, text };
}

export function applySearchInput(
	params: URLSearchParams,
	text: string,
	specifiers: TaxonomySpecifier[]
): URLSearchParams {
	const parsed = parseSearchInput(text, specifiers);
	if (parsed.text) params.set('search', parsed.text);
	for (const { field, value } of parsed.specifiers) params.set(field, value);
	return params;
}
