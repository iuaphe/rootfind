import { apiUrl, apikey } from './plant-details.js';

export const COUNTY_PAGE_SIZE = 20;

export async function getCountyCounts(params: URLSearchParams, signal: AbortSignal) {
	const response = await fetch(`${apiUrl}/count-plants-by-county?${params}`, {
		headers: { apikey },
		signal
	});
	if (!response.ok) throw new Error('Unable to load the heatmap. Please try again.');
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
