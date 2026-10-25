-- Filter county results before pagination, retaining the shared name/trait predicates.
-- County counts remain nationwide; only search_plants accepts a county filter.
begin;

create or replace function public.search_plants(p_filters jsonb default '{}')
returns jsonb
language plpgsql stable security definer
set search_path = ''
as $$
declare
  filter_key text;
  page_number integer;
  page_size integer;
  filters jsonb;
  county_fips integer;
begin
  if p_filters is null or jsonb_typeof(p_filters) <> 'object' then
    raise exception 'Expected a search filter object' using errcode = '22023';
  end if;
  foreach filter_key in array array['page', 'pageSize'] loop
    if not p_filters ? filter_key then continue; end if;
    if jsonb_typeof(p_filters->filter_key) is distinct from 'number'
      or (p_filters->>filter_key) !~ '^[1-9][0-9]{0,3}$' then
      raise exception '% must be a positive integer', filter_key using errcode = '22023';
    end if;
    if (p_filters->>filter_key)::integer > (case when filter_key = 'page' then 1000 else 100 end) then
      raise exception '% exceeds its limit', filter_key using errcode = '22023';
    end if;
  end loop;
  page_number := coalesce((p_filters->>'page')::integer, 1);
  page_size := coalesce((p_filters->>'pageSize')::integer, 50);

  if p_filters ? 'county' then
    if jsonb_typeof(p_filters->'county') is distinct from 'number'
      or (p_filters->>'county') !~ '^[1-9][0-9]{0,4}$' then
      raise exception 'county must be an integer between 1 and 99999' using errcode = '22023';
    end if;
    county_fips := (p_filters->>'county')::integer;
  end if;
  filters := rootfind_private.validate_plant_filters(p_filters - 'page' - 'pageSize' - 'county');
  return (
    select coalesce(jsonb_agg(page.plant_id order by page.plant_id), '[]'::jsonb)
    from (
      select matched.plant_id
      from rootfind_private.matching_plant_ids(filters) matched
      where county_fips is null or exists (
        select 1 from public.plant_county pc
        where pc.fips = county_fips and pc.plant_id = matched.plant_id
      )
      order by matched.plant_id
      limit page_size offset (page_number - 1) * page_size
    ) page
  );
end;
$$;

revoke execute on function public.search_plants(jsonb) from public;
grant execute on function public.search_plants(jsonb) to anon, authenticated;

commit;
