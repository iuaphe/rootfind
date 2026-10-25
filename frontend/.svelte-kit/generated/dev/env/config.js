import { variables } from "../../../../src/env.ts";
import { validate, handle_issues } from '@sveltejs/kit/internal/env';

const issues = {};

export { variables }

export const dynamic_private_env = {};

export const explicit_public_env = {};

export const rendered_env = {};

handle_issues(issues);

export function set_env(env) {
	const issues = {};
	const DATABASE_URL = validate(variables, env.DATABASE_URL, "DATABASE_URL", issues);
	dynamic_private_env.DATABASE_URL = DATABASE_URL;
	handle_issues(issues);
}

set_env({});