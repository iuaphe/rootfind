export class InvalidQuery extends Error {}

const enumOptions = {
  lifeCycle: ['perennial', 'biennial', 'annual'],
  foliage: ['deciduous', 'evergreen'],
  sun: ['full sun', 'part shade', 'full shade'],
  water: ['high', 'med', 'medium', 'low'],
} as const;
const booleans = ['isWoody', 'isTree', 'edible', 'thorns'];
const measurements = ['minHeight', 'maxHeight', 'minWidth', 'maxWidth'];
export type SearchFilters = Record<string, string | string[] | boolean | number>;

export function parseQuery(params: URLSearchParams): SearchFilters {
  const filters: SearchFilters = {};
  for (const name of params.keys()) {
    if (params.getAll(name).length !== 1) throw new InvalidQuery(`Provide ${name} exactly once`);
    const value = params.get(name)!;
    if (name === 'search') {
      const search = value.trim().replace(/\s+/g, ' ');
      if (search.length > 200) throw new InvalidQuery('search must not exceed 200 characters');
      filters.search = search;
    } else if (Object.hasOwn(enumOptions, name)) {
      const selected = value.split(',');
      const allowed: readonly string[] = enumOptions[name as keyof typeof enumOptions];
      if (!selected.length || selected.some(option => !allowed.includes(option))) {
        throw new InvalidQuery(`Invalid ${name} option`);
      }
      filters[name] = [...new Set(selected.map(option => name === 'water' && option === 'med' ? 'medium' : option))];
    } else if (booleans.includes(name)) {
      if (value !== 'true' && value !== 'false') throw new InvalidQuery(`${name} must be true or false`);
      filters[name] = value === 'true';
    } else if (name === 'page' || name === 'pageSize') {
      const max = name === 'page' ? 1000 : 100;
      if (!/^[1-9]\d{0,3}$/.test(value) || Number(value) > max) {
        throw new InvalidQuery(`${name} must be an integer between 1 and ${max}`);
      }
      filters[name] = Number(value);
    } else if (measurements.includes(name)) {
      if (!/^(0|[1-9]\d{0,4})$/.test(value) || Number(value) > 32767) {
        throw new InvalidQuery(`${name} must be an integer between 0 and 32767`);
      }
      filters[name] = Number(value);
    } else {
      throw new InvalidQuery(`Unknown parameter: ${name}`);
    }
  }
  for (const dimension of ['Height', 'Width']) {
    const lower = filters[`min${dimension}`];
    const upper = filters[`max${dimension}`];
    if (typeof lower === 'number' && typeof upper === 'number' && lower > upper) {
      throw new InvalidQuery(`Minimum ${dimension.toLowerCase()} must not exceed maximum`);
    }
  }
  return filters;
}
