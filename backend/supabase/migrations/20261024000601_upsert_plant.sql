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
    (v_plant->>'minheight')::smallint,
    (v_plant->>'maxheight')::smallint,
    (v_plant->>'minwidth')::smallint,
    (v_plant->>'maxwidth')::smallint,
    (v_plant->>'is_woody')::boolean,
    (v_plant->>'is_tree')::boolean,
    (v_plant->>'edible')::boolean,
    (v_plant->>'thorns')::boolean,
    case when v_plant->>'sun' in ('full sun', 'part shade', 'full shade')
         then (v_plant->>'sun')::public.sun_type else null end,
    case when v_plant->>'water' in ('high', 'med', 'low')
         then (v_plant->>'water')::public.water_type else null end,
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
    minheight = coalesce(excluded.minheight, public.plants.minheight),
    maxheight = coalesce(excluded.maxheight, public.plants.maxheight),
    minwidth = coalesce(excluded.minwidth, public.plants.minwidth),
    maxwidth = coalesce(excluded.maxwidth, public.plants.maxwidth),
    is_woody = coalesce(excluded.is_woody, public.plants.is_woody),
    is_tree = coalesce(excluded.is_tree, public.plants.is_tree),
    edible = coalesce(excluded.edible, public.plants.edible),
    thorns = coalesce(excluded.thorns, public.plants.thorns),
    sun = coalesce(excluded.sun, public.plants.sun),
    water = coalesce(excluded.water, public.plants.water),
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
