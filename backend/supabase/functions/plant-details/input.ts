export class InvalidInput extends Error {}

export function parseQuery(params: URLSearchParams): number {
  for (const name of params.keys()) {
    if (name !== 'plantId') throw new InvalidInput(`Unknown parameter: ${name}`);
  }
  const ids = params.getAll('plantId');
  if (ids.length !== 1 || !/^\d+$/.test(ids[0])
    || Number(ids[0]) < 1 || Number(ids[0]) > 2147483647) {
    throw new InvalidInput('Provide plantId exactly once as a positive 32-bit integer');
  }
  return Number(ids[0]);
}
