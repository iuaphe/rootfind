create or replace function public.plant_details(p_plant_id integer)
returns jsonb
language plpgsql stable security definer
set search_path = ''
as $$
begin
  if p_plant_id is null or p_plant_id <= 0 then
    raise exception 'plantId must be a positive integer' using errcode = '22023';
  end if;

  return (
    select to_jsonb(p) || jsonb_build_object(
      'common_names', (
        select coalesce(jsonb_agg(cn.name order by cn.name), '[]'::jsonb)
        from public.common_plant cp
        join public.common_names cn on cn.name = cp.name
        where cp.plant_id = p.plant_id
      ),
      'county_fips', (
        select coalesce(jsonb_agg(pc.fips order by pc.fips), '[]'::jsonb)
        from public.plant_county pc where pc.plant_id = p.plant_id
      )
    )
    from public.plants p where p.plant_id = p_plant_id
  );
end;
$$;

revoke execute on function public.plant_details(integer) from public;
grant execute on function public.plant_details(integer) to anon, authenticated;
