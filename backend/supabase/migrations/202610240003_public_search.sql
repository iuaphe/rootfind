-- Public catalog access is provided through the search RPC only.
grant usage on schema public to anon;
grant execute on function public.search_plants(jsonb) to anon;
