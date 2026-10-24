-- Keep public access on the validated search RPC, not direct table queries.
alter function public.search_plants(jsonb) security definer;

revoke all privileges on public.plants, public.county_info, public.plant_county,
  public.common_names, public.common_plant from anon, public;

drop policy if exists plants_public_read on public.plants;
drop policy if exists counties_public_read on public.county_info;
drop policy if exists plant_counties_public_read on public.plant_county;
drop policy if exists common_names_public_read on public.common_names;
drop policy if exists common_plants_public_read on public.common_plant;

grant usage on schema public to anon;
grant execute on function public.search_plants(jsonb) to anon;
