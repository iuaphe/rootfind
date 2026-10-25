-- Share validation and matching predicates between paginated search and county counts.
-- Requires migration 008 (inches and water arrays) and 009 (paginated search).
begin;

create or replace function rootfind_private.validate_plant_filters(p_filters jsonb)
returns jsonb
language plpgsql immutable
set search_path = ''
as $$
declare
  search_text text := '';
  filter_key text;
  allowed_options text[];
begin
  if p_filters is null or jsonb_typeof(p_filters) <> 'object' then
    raise exception 'Expected a search filter object' using errcode = '22023';
  end if;
  for filter_key in select jsonb_object_keys(p_filters) loop
    if filter_key not in ('search', 'lifeCycle', 'foliage', 'sun', 'water',
      'isWoody', 'isTree', 'edible', 'thorns', 'minHeight', 'maxHeight', 'minWidth', 'maxWidth') then
      raise exception 'Unknown search filter: %', filter_key using errcode = '22023';
    end if;
  end loop;
  if p_filters ? 'search' then
    if jsonb_typeof(p_filters->'search') is distinct from 'string' then
      raise exception 'search must be a string' using errcode = '22023';
    end if;
    search_text := btrim(regexp_replace(p_filters->>'search', '\s+', ' ', 'g'));
    if length(search_text) > 200 then
      raise exception 'search must not exceed 200 characters' using errcode = '22023';
    end if;
  end if;

  foreach filter_key in array array['isWoody', 'isTree', 'edible', 'thorns'] loop
    if p_filters ? filter_key and jsonb_typeof(p_filters->filter_key) is distinct from 'boolean' then
      raise exception '% must be a boolean', filter_key using errcode = '22023';
    end if;
  end loop;
  foreach filter_key in array array['lifeCycle', 'foliage', 'sun', 'water'] loop
    if not p_filters ? filter_key then continue; end if;
    if jsonb_typeof(p_filters->filter_key) is distinct from 'array' then
      raise exception '% must be an array', filter_key using errcode = '22023';
    end if;
    allowed_options := case filter_key
      when 'lifeCycle' then array['perennial', 'biennial', 'annual']
      when 'foliage' then array['deciduous', 'evergreen']
      when 'sun' then array['full sun', 'part shade', 'full shade']
      when 'water' then array['high', 'med', 'medium', 'low'] end;
    if jsonb_array_length(p_filters->filter_key) = 0 or exists (
      select 1 from jsonb_array_elements(p_filters->filter_key) as item(value)
      where jsonb_typeof(value) is distinct from 'string' or not (value #>> '{}') = any(allowed_options)
    ) then
      raise exception 'Invalid % option', filter_key using errcode = '22023';
    end if;
  end loop;
  foreach filter_key in array array['minHeight', 'maxHeight', 'minWidth', 'maxWidth'] loop
    if not p_filters ? filter_key then continue; end if;
    if jsonb_typeof(p_filters->filter_key) is distinct from 'number'
      or (p_filters->>filter_key) !~ '^(0|[1-9][0-9]{0,4})$' then
      raise exception '% must be an integer between 0 and 32767', filter_key using errcode = '22023';
    end if;
    if (p_filters->>filter_key)::integer > 32767 then
      raise exception '% must be between 0 and 32767', filter_key using errcode = '22023';
    end if;
  end loop;
  if (p_filters->>'minHeight')::integer > (p_filters->>'maxHeight')::integer
    or (p_filters->>'minWidth')::integer > (p_filters->>'maxWidth')::integer then
    raise exception 'Minimum measurement must not exceed maximum' using errcode = '22023';
  end if;

  return p_filters || jsonb_build_object('search', search_text);
end;
$$;

-- Keep this SQL helper invoker-only and without SET clauses so Postgres can
-- inline it into either caller and plan the joins, ordering, and limit together.
-- The public wrappers validate input and run with an empty search path.
create or replace function rootfind_private.matching_plant_ids(p_filters jsonb)
returns table (plant_id integer)
language sql stable
as $$
    select p.plant_id from public.plants p
    where (
      (p_filters->>'search') = ''
      or strpos(lower(p.genus || ' ' || p.species), lower((p_filters->>'search'))) > 0
      or exists (
        select 1 from public.common_plant cp
        join public.common_names cn on cn.name = cp.name
        where cp.plant_id = p.plant_id and strpos(lower(cn.name), lower((p_filters->>'search'))) > 0
      )
    )
    and (not p_filters ? 'lifeCycle' or p.life_cycle::text in (select jsonb_array_elements_text(p_filters->'lifeCycle')))
    and (not p_filters ? 'foliage' or p.foliage::text in (select jsonb_array_elements_text(p_filters->'foliage')))
    and (not p_filters ? 'sun' or p.sun::text in (select jsonb_array_elements_text(p_filters->'sun')))
    and (not p_filters ? 'water' or p.water && array(
      select (case value when 'med' then 'medium' else value end)::public.water_level_enum
      from jsonb_array_elements_text(p_filters->'water') as levels(value)
    ))
    and (not p_filters ? 'isWoody' or p.is_woody = (p_filters->>'isWoody')::boolean)
    and (not p_filters ? 'isTree' or p.is_tree = (p_filters->>'isTree')::boolean)
    and (not p_filters ? 'edible' or p.edible = (p_filters->>'edible')::boolean)
    and (not p_filters ? 'thorns' or p.thorns = (p_filters->>'thorns')::boolean)
    and (not p_filters ? 'minHeight' or p.maxheight >= (p_filters->>'minHeight')::integer)
    and (not p_filters ? 'maxHeight' or p.minheight <= (p_filters->>'maxHeight')::integer)
    and (not p_filters ? 'minWidth' or p.maxwidth >= (p_filters->>'minWidth')::integer)
    and (not p_filters ? 'maxWidth' or p.minwidth <= (p_filters->>'maxWidth')::integer)
;
$$;

revoke execute on function rootfind_private.validate_plant_filters(jsonb) from public, anon, authenticated;
revoke execute on function rootfind_private.matching_plant_ids(jsonb) from public, anon, authenticated;

-- Preserve the existing search contract and apply pagination after filtering.
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

  filters := rootfind_private.validate_plant_filters(p_filters - 'page' - 'pageSize');
  return (
    select coalesce(jsonb_agg(page.plant_id order by page.plant_id), '[]'::jsonb)
    from (
      select matched.plant_id
      from rootfind_private.matching_plant_ids(filters) matched
      order by matched.plant_id
      limit page_size offset (page_number - 1) * page_size
    ) page
  );
end;
$$;

create or replace function public.count_plants_by_county(p_filters jsonb default '{}')
returns jsonb
language plpgsql stable security definer
set search_path = ''
as $$
declare
  filters jsonb;
begin
  filters := rootfind_private.validate_plant_filters(p_filters);
  return (
    with county_counts as (
      select pc.fips, count(*) as plant_count
      from rootfind_private.matching_plant_ids(filters) matched
      join public.plant_county pc on pc.plant_id = matched.plant_id
      group by pc.fips
    )
    select coalesce(jsonb_agg(
      jsonb_build_object('fips', c.fips, 'plant_count', coalesce(cc.plant_count, 0))
      order by c.fips
    ), '[]'::jsonb)
    from public.county_info c
    left join county_counts cc on cc.fips = c.fips
  );
end;
$$;

revoke execute on function public.search_plants(jsonb) from public;
revoke execute on function public.count_plants_by_county(jsonb) from public;
grant execute on function public.search_plants(jsonb) to anon, authenticated;
grant execute on function public.count_plants_by_county(jsonb) to anon, authenticated;

commit;
