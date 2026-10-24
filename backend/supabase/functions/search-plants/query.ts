export class InvalidQuery extends Error {}

const options: Record<string, readonly string[]> = {
  lifeCycle: ['perennial', 'biennial', 'annual'],
  foliage: ['deciduous', 'evergreen'],
  sun: ['full sun', 'part shade', 'full shade'],
  water: ['high', 'med', 'low'],
};
const booleans = ['isWoody', 'isTree', 'edible', 'thorns'];
const numbers = ['minHeight', 'maxHeight', 'minWidth', 'maxWidth', 'page', 'pageSize'];
const allowed = new Set(['search', 'county', 'misc', ...Object.keys(options), ...booleans, ...numbers]);

export function parseQuery(params: URLSearchParams) {
  const filters: Record<string, string | boolean | number | string[]> = {};
  for (const name of new Set(params.keys())) {
    if (!allowed.has(name)) throw new InvalidQuery(`Unknown filter: ${name}`);
    const values = params.getAll(name).map((value) => value.trim());
    const multi = name in options || name === 'county' || name === 'misc';
    if (!multi && values.length !== 1) throw new InvalidQuery(`${name} may only appear once`);
    if (multi) {
      if (values.length > 100 || values.some((v) => !v || v.length > 100)) {
        throw new InvalidQuery(`${name} requires 1–100 nonempty values of at most 100 characters`);
      }
      if (name in options && values.some((v) => !options[name].includes(v))) {
        throw new InvalidQuery(`${name} must be one of: ${options[name].join(', ')}`);
      }
      if (name === 'county' && values.some((v) => !/^\d{1,5}$/.test(v))) {
        throw new InvalidQuery('county must be a numeric FIPS code of up to 5 digits');
      }
      filters[name] = [...new Set(values)];
    } else if (booleans.includes(name)) {
      if (!['true', 'false'].includes(values[0])) throw new InvalidQuery(`${name} must be true or false`);
      filters[name] = values[0] === 'true';
    } else if (numbers.includes(name)) {
      const min = name === 'page' || name === 'pageSize' ? 1 : 0;
      const max = name === 'page' ? 10000 : name === 'pageSize' ? 100 : 32767;
      if (!/^\d+$/.test(values[0]) || Number(values[0]) < min || Number(values[0]) > max) {
        throw new InvalidQuery(`${name} must be an integer between ${min} and ${max}`);
      }
      filters[name] = Number(values[0]);
    } else {
      if (values[0].length > 200) throw new InvalidQuery('search must be at most 200 characters');
      filters.search = values[0].replace(/\s+/g, ' ');
    }
  }
  for (const dimension of ['Height', 'Width']) {
    const min = filters[`min${dimension}`];
    const max = filters[`max${dimension}`];
    if (typeof min === 'number' && typeof max === 'number' && min > max) {
      throw new InvalidQuery(`min${dimension} must not exceed max${dimension}`);
    }
  }
  return { page: 1, pageSize: 25, ...filters };
}
