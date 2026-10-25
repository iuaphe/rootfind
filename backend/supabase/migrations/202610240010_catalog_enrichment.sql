-- NC State/PFAF enrichment. Existing measurement units remain inches.
begin;
select pg_advisory_xact_lock(hashtext('rootfind_catalog_enrichment_v1'));
drop view if exists public.plants_with_taxonomy;
do $$
declare kind text;
begin
  select udt_name into kind from information_schema.columns
    where table_schema='public' and table_name='plants' and column_name='sun';
  if kind = 'sun_type' then
    alter table public.plants alter column sun type public.sun_type[]
      using case when sun is null then null else array[sun] end;
  elsif kind is distinct from '_sun_type' then
    raise exception 'Unexpected plants.sun type: %', kind;
  end if;
end $$;
alter table public.plants
  add column if not exists bloom_seasons text[],
  add column if not exists drainage text[];
comment on column public.plants.sun is 'Supported sun levels; enum array, merged across sources';
comment on column public.plants.bloom_seasons is 'Source seasons; do not infer exact months';
comment on column public.plants.drainage is 'Source drainage/occasional moisture conditions, distinct from watering frequency';
create or replace function public.normalize_sun_levels(p_sun jsonb)
returns public.sun_type[] language plpgsql immutable set search_path='' as $$
declare normalized jsonb; result public.sun_type[];
begin
  if p_sun is null or p_sun = 'null'::jsonb then return null; end if;
  if jsonb_typeof(p_sun) = 'string' then normalized := jsonb_build_array(p_sun);
  elsif jsonb_typeof(p_sun) = 'array' then normalized := p_sun;
  else raise exception 'sun must be a string or array' using errcode='22023'; end if;
  if exists (select 1 from jsonb_array_elements(normalized) value
    where jsonb_typeof(value) is distinct from 'string'
       or (value #>> '{}') not in ('full sun','part shade','full shade')) then
    raise exception 'Invalid sun level' using errcode='22023';
  end if;
  select coalesce(array_agg(distinct value order by value), '{}'::public.sun_type[])
    into result from (select value::public.sun_type as value from jsonb_array_elements_text(normalized) value) levels;
  return result;
end $$;
revoke execute on function public.normalize_sun_levels(jsonb) from public, anon;
grant execute on function public.normalize_sun_levels(jsonb) to authenticated, service_role;

create or replace function public.upsert_plant(p_payload jsonb)
returns integer
language plpgsql security definer
set search_path = ''
as $$
declare
  v_plant_id integer;
  v_plant jsonb;
  v_counties jsonb;
  v_common_names jsonb;
begin
  if p_payload is null or jsonb_typeof(p_payload) <> 'object' then
    raise exception 'Payload must be an object' using errcode = '22023';
  end if;

  v_plant := p_payload->'plant';
  if v_plant is null or jsonb_typeof(v_plant) <> 'object' then
    raise exception 'Payload must contain a plant object' using errcode = '22023';
  end if;

  if v_plant->>'genus' is null or length(btrim(v_plant->>'genus')) = 0 then
    raise exception 'Plant genus is required' using errcode = '22023';
  end if;

  if v_plant->>'species' is null or length(btrim(v_plant->>'species')) = 0 then
    raise exception 'Plant species is required' using errcode = '22023';
  end if;

  -- 1. Insert or update the plant
  insert into public.plants (
    genus, species, life_cycle, foliage,
    minheight, maxheight, minwidth, maxwidth,
    is_woody, is_tree, edible, thorns,
    sun, water, image_link, misc
  ) values (
    btrim(v_plant->>'genus'),
    btrim(v_plant->>'species'),
    case when v_plant->>'life_cycle' in ('perennial', 'biennial', 'annual')
         then (v_plant->>'life_cycle')::public.life_cycle_enum else null end,
    case when v_plant->>'foliage' in ('deciduous', 'evergreen')
         then (v_plant->>'foliage')::public.foliage_type else null end,
    public.plant_measurement_inches(v_plant->>'minheight', coalesce(v_plant->>'measurement_units', 'feet'), false),
    public.plant_measurement_inches(v_plant->>'maxheight', coalesce(v_plant->>'measurement_units', 'feet'), true),
    public.plant_measurement_inches(v_plant->>'minwidth', coalesce(v_plant->>'measurement_units', 'feet'), false),
    public.plant_measurement_inches(v_plant->>'maxwidth', coalesce(v_plant->>'measurement_units', 'feet'), true),
    (v_plant->>'is_woody')::boolean,
    (v_plant->>'is_tree')::boolean,
    (v_plant->>'edible')::boolean,
    (v_plant->>'thorns')::boolean,
    public.normalize_sun_levels(v_plant->'sun'),
    public.normalize_water_levels(v_plant->'water'),
    v_plant->>'image_link',
    case
      when v_plant->'misc' is not null and jsonb_typeof(v_plant->'misc') = 'array'
      then (select array_agg(x) from jsonb_array_elements_text(v_plant->'misc') as x)
      else null
    end
  )
  on conflict (genus, species) do update set
    life_cycle = coalesce(excluded.life_cycle, public.plants.life_cycle),
    foliage = coalesce(excluded.foliage, public.plants.foliage),
    minheight = least(excluded.minheight, public.plants.minheight),
    maxheight = greatest(excluded.maxheight, public.plants.maxheight),
    minwidth = least(excluded.minwidth, public.plants.minwidth),
    maxwidth = greatest(excluded.maxwidth, public.plants.maxwidth),
    is_woody = coalesce(excluded.is_woody, public.plants.is_woody),
    is_tree = coalesce(excluded.is_tree, public.plants.is_tree),
    edible = coalesce(excluded.edible, public.plants.edible),
    thorns = coalesce(excluded.thorns, public.plants.thorns),
    sun = case when excluded.sun is null then public.plants.sun else
      (select array_agg(distinct value order by value) from unnest(
        coalesce(public.plants.sun, '{}'::public.sun_type[]) || excluded.sun) value) end,
    water = case when excluded.water is null then public.plants.water else
      (select array_agg(distinct level order by level) from unnest(
        coalesce(public.plants.water, '{}'::public.water_level_enum[]) || excluded.water) level) end,
    image_link = coalesce(excluded.image_link, public.plants.image_link),
    misc = coalesce(excluded.misc, public.plants.misc)
  returning plant_id into v_plant_id;

  -- 2. Upsert county info and link plant_county
  v_counties := p_payload->'counties';
  if v_counties is not null and jsonb_typeof(v_counties) = 'array' and jsonb_array_length(v_counties) > 0 then
    insert into public.county_info (fips, name)
    select distinct (c->>'fips')::integer, substring(btrim(c->>'name') from 1 for 100)
    from jsonb_array_elements(v_counties) as c
    where (c->>'fips') is not null
      and (c->>'fips')::integer between 0 and 99999
      and c->>'name' is not null
      and length(btrim(c->>'name')) > 0
    on conflict (fips) do update set name = excluded.name;

    insert into public.plant_county (fips, plant_id)
    select distinct (c->>'fips')::integer, v_plant_id
    from jsonb_array_elements(v_counties) as c
    where (c->>'fips') is not null
      and (c->>'fips')::integer between 0 and 99999
    on conflict (fips, plant_id) do nothing;
  end if;

  -- 3. Upsert common names and link common_plant
  v_common_names := p_payload->'common_names';
  if v_common_names is not null and jsonb_typeof(v_common_names) = 'array' and jsonb_array_length(v_common_names) > 0 then
    insert into public.common_names (name)
    select distinct substring(btrim(cn) from 1 for 100)
    from jsonb_array_elements_text(v_common_names) as cn
    where cn is not null and length(btrim(cn)) > 0
    on conflict (name) do nothing;

    insert into public.common_plant (name, plant_id)
    select distinct substring(btrim(cn) from 1 for 100), v_plant_id
    from jsonb_array_elements_text(v_common_names) as cn
    where cn is not null and length(btrim(cn)) > 0
    on conflict (name, plant_id) do nothing;
  end if;

  return v_plant_id;
end;
$$;

create or replace function public.upsert_plants_batch(p_payload jsonb)
returns jsonb
language plpgsql security definer
set search_path = ''
as $$
declare
  item jsonb;
  v_inserted integer := 0;
  v_ids integer[] := array[]::integer[];
  v_id integer;
begin
  if p_payload is null or jsonb_typeof(p_payload) <> 'array' then
    raise exception 'Batch payload must be a JSON array' using errcode = '22023';
  end if;

  for item in select * from jsonb_array_elements(p_payload) loop
    v_id := public.upsert_plant(item);
    v_inserted := v_inserted + 1;
    v_ids := array_append(v_ids, v_id);
  end loop;

  return jsonb_build_object('success', true, 'count', v_inserted, 'plant_ids', v_ids);
end;
$$;

-- Revoke public access, grant execute to service_role and authenticated
revoke execute on function public.upsert_plant(jsonb) from public, anon;
revoke execute on function public.upsert_plants_batch(jsonb) from public, anon;
grant execute on function public.upsert_plant(jsonb) to authenticated, service_role;
grant execute on function public.upsert_plants_batch(jsonb) to authenticated, service_role;


-- Bounded ID-array search after migration 008: array water filters and pagination.
-- Measurements are expressed in inches. A requested interval overlaps a plant's range.
create or replace function public.search_plants(p_filters jsonb default '{}')
returns jsonb
language plpgsql stable security definer
set search_path = ''
as $$
declare
  search_text text := '';
  filter_key text;
  allowed_options text[];
  page_number integer := 1;
  page_size integer := 50;
begin
  if p_filters is null or jsonb_typeof(p_filters) <> 'object' then
    raise exception 'Expected a search filter object' using errcode = '22023';
  end if;
  for filter_key in select jsonb_object_keys(p_filters) loop
    if filter_key not in ('search', 'lifeCycle', 'foliage', 'sun', 'water',
      'isWoody', 'isTree', 'edible', 'thorns', 'minHeight', 'maxHeight', 'minWidth', 'maxWidth', 'page', 'pageSize') then
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

  return (
    select coalesce(jsonb_agg(page.plant_id order by page.plant_id), '[]'::jsonb)
    from (
    select p.plant_id from public.plants p
    where (
      search_text = ''
      or strpos(lower(p.genus || ' ' || p.species), lower(search_text)) > 0
      or exists (
        select 1 from public.common_plant cp
        join public.common_names cn on cn.name = cp.name
        where cp.plant_id = p.plant_id and strpos(lower(cn.name), lower(search_text)) > 0
      )
    )
    and (not p_filters ? 'lifeCycle' or p.life_cycle::text in (select jsonb_array_elements_text(p_filters->'lifeCycle')))
    and (not p_filters ? 'foliage' or p.foliage::text in (select jsonb_array_elements_text(p_filters->'foliage')))
    and (not p_filters ? 'sun' or p.sun && array(select value::public.sun_type from jsonb_array_elements_text(p_filters->'sun') levels(value)))
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
    order by p.plant_id
    limit page_size offset (page_number - 1) * page_size
    ) page
  );
end;
$$;

revoke execute on function public.search_plants(jsonb) from public;
grant execute on function public.search_plants(jsonb) to anon, authenticated;


do $$ begin
  if to_regclass('public.plant_taxonomy') is not null then
    create view public.plants_with_taxonomy as
    select p.*, t.division, t.division_common, t.class, t.class_common,
      t.order_name, t.family, t.family_common, t.lineage as taxonomy_lineage
    from public.plants p left join public.plant_taxonomy t on t.plant_id=p.plant_id;
    grant select on public.plants_with_taxonomy to anon, authenticated;
  end if;
end $$;
commit;
