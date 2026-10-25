import { apiUrl, apikey } from './plant-details.js';
import { isTaxonomyField, type SearchSuggestion } from './plant-search-input.js';

export const COUNTY_PAGE_SIZE = 20;

const COUNTY_COUNT_RETRY_DELAYS_MS = [400, 1200];
const COUNTY_COUNT_ERROR = 'Unable to load the heatmap. Please try again.';

function isRetryableStatus(status: number) {
	return status === 408 || status >= 500;
}

function waitForRetry(delay: number, signal: AbortSignal) {
	return new Promise<void>((resolve, reject) => {
		if (signal.aborted) {
			reject(signal.reason);
			return;
		}

		const finish = () => {
			clearTimeout(timeout);
			signal.removeEventListener('abort', cancel);
		};
		const cancel = () => {
			finish();
			reject(signal.reason);
		};
		const timeout = setTimeout(() => {
			finish();
			resolve();
		}, delay);
		signal.addEventListener('abort', cancel, { once: true });
	});
}

export async function getSearchSuggestions(
	query: string,
	context: URLSearchParams,
	signal: AbortSignal
): Promise<SearchSuggestion[]> {
	const params = new URLSearchParams(context);
	params.delete('search');
	params.set('q', query);
	const response = await fetch(`${apiUrl}/suggest-plants?${params}`, {
		headers: { apikey },
		signal
	});
	if (!response.ok) throw new Error('Suggestions are unavailable. You can still type and search.');
	const rows: unknown = await response.json();
	if (
		!Array.isArray(rows) ||
		rows.length > 12 ||
		!rows.every(
			(row) =>
				row &&
				typeof row.value === 'string' &&
				row.value.length > 0 &&
				row.value.length <= 201 &&
				(row.kind === 'taxonomy'
					? typeof row.field === 'string' && isTaxonomyField(row.field)
					: (row.kind === 'common' || row.kind === 'scientific') && row.field === null)
		)
	) {
		throw new Error('Suggestions are unavailable. You can still type and search.');
	}
	return rows;
}

export async function getCountyCounts(params: URLSearchParams, signal: AbortSignal) {
	let response: Response | undefined;
	for (let attempt = 0; attempt <= COUNTY_COUNT_RETRY_DELAYS_MS.length; attempt++) {
		try {
			response = await fetch(`${apiUrl}/count-plants-by-county?${params}`, {
				headers: { apikey },
				signal
			});
		} catch (cause) {
			if (signal.aborted) throw cause;
			if (!(cause instanceof TypeError) || attempt === COUNTY_COUNT_RETRY_DELAYS_MS.length) {
				throw new Error(COUNTY_COUNT_ERROR, { cause });
			}
			await waitForRetry(COUNTY_COUNT_RETRY_DELAYS_MS[attempt], signal);
			continue;
		}

		if (response.ok) break;
		if (!isRetryableStatus(response.status) || attempt === COUNTY_COUNT_RETRY_DELAYS_MS.length) {
			throw new Error(COUNTY_COUNT_ERROR);
		}
		await waitForRetry(COUNTY_COUNT_RETRY_DELAYS_MS[attempt], signal);
	}
	if (!response?.ok) throw new Error(COUNTY_COUNT_ERROR);
	const rows: unknown = await response.json();
	if (!Array.isArray(rows)) throw new Error('Unexpected county counts. Please try again.');
	const counts: Record<number, number> = {};
	for (const row of rows) {
		if (
			!row ||
			!Number.isSafeInteger(row.fips) ||
			row.fips < 0 ||
			row.fips > 99999 ||
			!Number.isSafeInteger(row.plant_count) ||
			row.plant_count < 0 ||
			Object.hasOwn(counts, row.fips)
		) {
			throw new Error('Unexpected county counts. Please try again.');
		}
		counts[row.fips] = row.plant_count;
	}
	return counts;
}

export async function getCountyPlantIds(
	query: string,
	county: string,
	page: number,
	signal: AbortSignal
) {
	const params = new URLSearchParams(query);
	params.set('county', county);
	params.set('page', String(page));
	params.set('pageSize', String(COUNTY_PAGE_SIZE));
	const response = await fetch(`${apiUrl}/search-plants?${params}`, {
		headers: { apikey },
		signal
	});
	if (!response.ok) throw new Error('Unable to load plants for this county. Please retry.');
	const ids: unknown = await response.json();
	if (
		!Array.isArray(ids) ||
		ids.length > COUNTY_PAGE_SIZE ||
		!ids.every((id) => Number.isSafeInteger(id) && id > 0) ||
		new Set(ids).size !== ids.length
	) {
		throw new Error('Unexpected county search response. Please retry.');
	}
	return ids as number[];
}
