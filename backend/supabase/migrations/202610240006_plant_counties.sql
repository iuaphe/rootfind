-- Each output element corresponds to the input element at the same position.
create or replace function public.plant_counties(p_plant_ids integer[])
returns jsonb
language plpgsql stable security definer
set search_path = ''
as $$
begin
  if p_plant_ids is null or cardinality(p_plant_ids) > 1000
    or coalesce(array_ndims(p_plant_ids), 1) <> 1
    or exists (select 1 from unnest(p_plant_ids) id where id is null or id <= 0)
  then
    raise exception 'Provide up to 1000 positive integer plant IDs' using errcode = '22023';
  end if;

  return (
    select coalesce(jsonb_agg(counties.fips order by requested.position), '[]'::jsonb)
    from unnest(p_plant_ids) with ordinality as requested(plant_id, position)
    cross join lateral (
      select coalesce(jsonb_agg(pc.fips order by pc.fips), '[]'::jsonb) as fips
      from public.plant_county pc
      where pc.plant_id = requested.plant_id
    ) counties
  );
end;
$$;

revoke execute on function public.plant_counties(integer[]) from public;
grant execute on function public.plant_counties(integer[]) to anon, authenticated;
