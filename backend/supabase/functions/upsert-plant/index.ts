import { createHandler } from './handler.ts';

// @ts-ignore: Deno is defined in Supabase Edge Runtime
Deno.serve(createHandler({
  // @ts-ignore
  url: Deno.env.get('SUPABASE_URL') ?? '',
  // @ts-ignore
  serviceRoleKey: Deno.env.get('SUPABASE_SERVICE_ROLE_KEY') ?? '',
}));
