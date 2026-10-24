-- Replace the existing RPC without changing its argument or return type.
-- Name lookup returns a JSON array of IDs; no filters, pagination, or plant fields.
create or replace function public.search_plants(p_filters jsonb default '{}')
returns jsonb
language plpgsql stable security definer
set search_path = ''
as $$
declare
  search_text text;
begin
  -- Validate direct RPC requests as well as requests through the Edge Function.
  if p_filters is null or jsonb_typeof(p_filters) <> 'object' then
    raise exception 'Expected an object containing search' using errcode = '22023';
  end if;
  if exists (select 1 from jsonb_object_keys(p_filters) as k where k <> 'search') then
    raise exception 'Only search is supported' using errcode = '22023';
  end if;
  if jsonb_typeof(p_filters->'search') is distinct from 'string' then
    raise exception 'search must be a string' using errcode = '22023';
  end if;
  search_text := btrim(regexp_replace(p_filters->>'search', '\s+', ' ', 'g'));
  if length(search_text) < 1 or length(search_text) > 200 then
    raise exception 'search must be between 1 and 200 characters' using errcode = '22023';
  end if;

  return (
    select coalesce(jsonb_agg(p.plant_id order by p.plant_id), '[]'::jsonb)
    from public.plants p
    where strpos(lower(p.genus || ' ' || p.species), lower(search_text)) > 0
      or exists (
        select 1 from public.common_plant cp
        join public.common_names cn on cn.name = cp.name
        where cp.plant_id = p.plant_id
          and strpos(lower(cn.name), lower(search_text)) > 0
      )
  );
end;
$$;

revoke execute on function public.search_plants(jsonb) from public;
grant execute on function public.search_plants(jsonb) to anon, authenticated;
