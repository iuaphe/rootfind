export class InvalidQuery extends Error {}

export function parseQuery(params: URLSearchParams): { search: string } {
  for (const name of params.keys()) {
    if (name !== 'search') throw new InvalidQuery(`Unknown parameter: ${name}`);
  }
  const values = params.getAll('search');
  if (values.length !== 1) throw new InvalidQuery('Provide search exactly once');
  const search = values[0].trim().replace(/\s+/g, ' ');
  if (!search || search.length > 200) {
    throw new InvalidQuery('search must be between 1 and 200 characters');
  }
  return { search };
}
