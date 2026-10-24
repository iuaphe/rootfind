-- Run only after the existing feet-based import has finished.
-- Existing dimensions are converted to integer INCHES exactly once.
begin;
select pg_advisory_xact_lock(hashtext('rootfind_mobot_enrichment_v1'));
create schema if not exists rootfind_private;
revoke all on schema rootfind_private from public, anon, authenticated;

create table if not exists rootfind_private.data_settings (
  name text primary key,
  value text not null
);
create table if not exists rootfind_private.mobot_changes (
  run_id text not null,
  plant_id integer not null,
  source_url text not null,
  old_row jsonb not null,
  new_row jsonb not null,
  applied_at timestamptz not null default now(),
  primary key (run_id, plant_id)
);

do $$ begin
  create type public.month_enum as enum (
    'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'
  );
exception when duplicate_object then null;
end $$;
do $$ begin
  create type public.water_level_enum as enum ('low', 'medium', 'high');
exception when duplicate_object then null;
end $$;

alter table public.plants
  add column if not exists min_zone smallint,
  add column if not exists max_zone smallint,
  add column if not exists bloom_months public.month_enum[];

do $$
declare water_column_type text;
begin
  select udt_name into water_column_type from information_schema.columns
  where table_schema = 'public' and table_name = 'plants' and column_name = 'water';
  if water_column_type = 'water_type' then
    alter table public.plants alter column water type public.water_level_enum[]
    using case when water is null then null
      else array[(case water::text when 'med' then 'medium' else water::text end)::public.water_level_enum] end;
  elsif water_column_type <> '_water_level_enum' then
    raise exception 'Unexpected plants.water type: %', water_column_type;
  end if;

  if not exists (select 1 from rootfind_private.data_settings where name = 'plant_dimension_unit') then
    if exists (select 1 from public.plants
      where minheight < 0 or maxheight < 0 or minwidth < 0 or maxwidth < 0
      or minheight::integer * 12 > 32767 or maxheight::integer * 12 > 32767
      or minwidth::integer * 12 > 32767 or maxwidth::integer * 12 > 32767) then
      raise exception 'Existing feet values cannot fit nonnegative SMALLINT inches; migration cancelled';
    end if;
    update public.plants set
      minheight = minheight::integer * 12, maxheight = maxheight::integer * 12,
      minwidth = minwidth::integer * 12, maxwidth = maxwidth::integer * 12;
    insert into rootfind_private.data_settings values ('plant_dimension_unit', 'inches');
  elsif (select value from rootfind_private.data_settings where name = 'plant_dimension_unit') <> 'inches' then
    raise exception 'Unexpected measurement-unit marker';
  end if;
end $$;

comment on column public.plants.minheight is 'Unit: inches (MOBOT enrichment v1)';
comment on column public.plants.maxheight is 'Unit: inches (MOBOT enrichment v1)';
comment on column public.plants.minwidth is 'Unit: inches (MOBOT enrichment v1)';
comment on column public.plants.maxwidth is 'Unit: inches (MOBOT enrichment v1)';
comment on column public.plants.water is 'Supported water levels; med is normalized to medium';

-- These helpers keep existing USDA RPC clients working with scalar water and
-- feet. New clients can send arrays and measurement_units: inches explicitly.
create or replace function public.normalize_water_levels(p_water jsonb)
returns public.water_level_enum[]
language plpgsql immutable set search_path = '' as $$
declare normalized jsonb; result public.water_level_enum[];
begin
  if p_water is null or p_water = 'null'::jsonb then return null; end if;
  if jsonb_typeof(p_water) = 'string' then normalized := jsonb_build_array(p_water);
  elsif jsonb_typeof(p_water) = 'array' then normalized := p_water;
  else raise exception 'water must be a string or array' using errcode = '22023'; end if;
  if exists (select 1 from jsonb_array_elements(normalized) v
    where jsonb_typeof(v) <> 'string' or (v #>> '{}') not in ('low','med','medium','high')) then
    raise exception 'Invalid water level' using errcode = '22023';
  end if;
  select coalesce(array_agg(distinct level order by level), '{}'::public.water_level_enum[])
  into result from (
    select (case v when 'med' then 'medium' else v end)::public.water_level_enum as level
    from jsonb_array_elements_text(normalized) v
  ) levels;
  return result;
end $$;

create or replace function public.plant_measurement_inches(p_value text, p_units text, p_maximum boolean)
returns smallint language plpgsql immutable set search_path = '' as $$
declare inches numeric;
begin
  if p_value is null then return null; end if;
  if p_units not in ('feet', 'inches') then
    raise exception 'measurement_units must be feet or inches' using errcode = '22023';
  end if;
  inches := p_value::numeric * case when p_units = 'feet' then 12 else 1 end;
  if inches < 0 or inches > 32767 then
    raise exception 'Measurement outside SMALLINT range' using errcode = '22023';
  end if;
  return (case when p_maximum then ceil(inches) else floor(inches) end)::smallint;
end $$;

revoke execute on function public.normalize_water_levels(jsonb) from public, anon;
revoke execute on function public.plant_measurement_inches(text,text,boolean) from public, anon;
grant execute on function public.normalize_water_levels(jsonb) to authenticated, service_role;
grant execute on function public.plant_measurement_inches(text,text,boolean) to authenticated, service_role;

-- The compatible upsert definitions follow; the migration ends with COMMIT.

-- Migration 006: Add functions to upsert plant records, county links, and common names.

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
    case when v_plant->>'sun' in ('full sun', 'part shade', 'full shade')
         then (v_plant->>'sun')::public.sun_type else null end,
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
    sun = coalesce(excluded.sun, public.plants.sun),
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

commit;
