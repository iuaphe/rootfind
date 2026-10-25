import { InvalidQuery, parseQuery, taxonomyFields } from '../_shared/plant-search-query.ts';
export { InvalidQuery } from '../_shared/plant-search-query.ts';

export function parseSuggestionsQuery(params: URLSearchParams) {
  if (params.getAll('q').length !== 1) throw new InvalidQuery('Provide q exactly once');
  const query = params.get('q')!.trim().replace(/\s+/g, ' ');
  if (query.length > 200) throw new InvalidQuery('q must not exceed 200 characters');
  if (query.includes('=')) {
    const field = query.slice(0, query.indexOf('=')).trim().toLowerCase();
    if (!(taxonomyFields as readonly string[]).includes(field)) {
      throw new InvalidQuery(`Unknown taxonomy field: ${field}`);
    }
  }
  const rest = new URLSearchParams(params);
  rest.delete('q');
  // q is the name fragment; the other filters provide suggestion context.
  if (rest.has('search')) throw new InvalidQuery('Use q for suggestions');
  const filters = parseQuery(rest, { allowPagination: false });
  return { query, filters };
}
