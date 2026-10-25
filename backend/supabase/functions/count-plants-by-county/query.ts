import { parseQuery as parseSearchQuery } from '../_shared/plant-search-query.ts';
export { InvalidQuery } from '../_shared/plant-search-query.ts';

export function parseQuery(params: URLSearchParams) {
  return parseSearchQuery(params, { allowPagination: false });
}
