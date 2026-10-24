export class InvalidInput extends Error {}

export function parseInput(body: unknown): number[] {
  if (body === null || typeof body !== 'object' || Array.isArray(body)
    || Object.keys(body).length !== 1 || !Object.hasOwn(body, 'plantIds')) {
    throw new InvalidInput('Expected { "plantIds": [1, 2, 3] }');
  }
  const ids = (body as { plantIds: unknown }).plantIds;
  if (!Array.isArray(ids) || ids.length > 1000 || ids.some((id) =>
    typeof id !== 'number' || !Number.isInteger(id) || id < 1 || id > 2147483647)) {
    throw new InvalidInput('plantIds must contain up to 1000 positive 32-bit integers');
  }
  return ids;
}
