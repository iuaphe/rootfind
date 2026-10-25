-- Dedicated BONAP upserts; existing general upserts remain unchanged.
begin;
create or replace function public.upsert_bonap_plant(p_payload jsonb)
returns integer
language plpgsql security definer
set search_path = ''
as $$
declare
  v_plant_id integer;
  v_plant jsonb;
  v_counties jsonb;
  v_common_names jsonb;
  v_bonap boolean := true;
  v_seasons text[];
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

  v_plant := jsonb_set(v_plant, '{species}', to_jsonb(regexp_replace(btrim(v_plant->>'species'), '\s+(subsp\.|ssp\.|var\.|f\.)\s+.*$', '')));

  if v_bonap then
    if nullif(btrim(p_payload->'bonap'->'raw'->>'Species'),'') is null then
      raise exception 'BONAP raw Species is required' using errcode='22023';
    end if;
    if jsonb_typeof(p_payload->'bonap') is distinct from 'object'
      or jsonb_typeof(p_payload->'bonap'->'raw') is distinct from 'object' then
      raise exception 'bonap.raw must be an object' using errcode='22023';
    end if;
    if p_payload->'bonap' ? 'bloom_seasons' then
      if jsonb_typeof(p_payload->'bonap'->'bloom_seasons') is distinct from 'array' then
        raise exception 'BONAP bloom_seasons must be an array' using errcode='22023';
      end if;
      if exists (select 1 from jsonb_array_elements(p_payload->'bonap'->'bloom_seasons') v
        where jsonb_typeof(v) is distinct from 'string'
          or (v #>> '{}') not in ('Spring','Summer','Fall','Winter')) then
        raise exception 'Invalid BONAP bloom season' using errcode='22023';
      end if;
      select array_agg(distinct v order by v) into v_seasons
        from jsonb_array_elements_text(p_payload->'bonap'->'bloom_seasons') v;
    end if;
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
    life_cycle = case when v_bonap then coalesce(public.plants.life_cycle, excluded.life_cycle) else coalesce(excluded.life_cycle, public.plants.life_cycle) end,
    foliage = coalesce(excluded.foliage, public.plants.foliage),
    minheight = least(excluded.minheight, public.plants.minheight),
    maxheight = greatest(excluded.maxheight, public.plants.maxheight),
    minwidth = least(excluded.minwidth, public.plants.minwidth),
    maxwidth = greatest(excluded.maxwidth, public.plants.maxwidth),
    is_woody = case when excluded.is_woody is null then public.plants.is_woody
      else coalesce(public.plants.is_woody,false) or excluded.is_woody end,
    is_tree = case when excluded.is_tree is null then public.plants.is_tree
      else coalesce(public.plants.is_tree,false) or excluded.is_tree end,
    edible = coalesce(excluded.edible, public.plants.edible),
    thorns = coalesce(excluded.thorns, public.plants.thorns),
    sun = case when excluded.sun is null then public.plants.sun else
      (select array_agg(distinct value order by value) from unnest(
        coalesce(public.plants.sun, '{}'::public.sun_type[]) || excluded.sun) value) end,
    water = case when excluded.water is null then public.plants.water else
      (select array_agg(distinct level order by level) from unnest(
        coalesce(public.plants.water, '{}'::public.water_level_enum[]) || excluded.water) level) end,
    image_link = coalesce(excluded.image_link, public.plants.image_link),
    misc = case when excluded.misc is null then public.plants.misc else
      (select array_agg(distinct v order by v) from unnest(coalesce(public.plants.misc,'{}'::text[]) || excluded.misc) v) end
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
    on conflict (fips) do update set name = case when v_bonap then public.county_info.name else excluded.name end;

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

  if v_bonap then
    if v_seasons is not null then
      update public.plants set bloom_seasons = (select array_agg(distinct v order by v)
        from unnest(coalesce(bloom_seasons, '{}'::text[]) || v_seasons) v)
      where plant_id=v_plant_id;
    end if;
    -- Family is filled only when missing or explicitly unknown. Existing lineage remains intact.
    if nullif(btrim(p_payload->'bonap'->'raw'->>'Family'), '') is not null then
      insert into public.plant_taxonomy(plant_id,family)
        values(v_plant_id, left(btrim(p_payload->'bonap'->'raw'->>'Family'),100))
        on conflict(plant_id) do update set family=case
          when btrim(public.plant_taxonomy.family)='' or lower(public.plant_taxonomy.family)='unknown'
          then excluded.family else public.plant_taxonomy.family end;
    end if;
  end if;
  return v_plant_id;
end;
$$;

create or replace function public.upsert_bonap_plants_batch(p_payload jsonb)
returns jsonb language plpgsql security definer set search_path='' as $$
declare item jsonb; ids integer[] := '{}';
begin
  if jsonb_typeof(p_payload) is distinct from 'array' then
    raise exception 'Expected a batch array' using errcode='22023';
  end if;
  if jsonb_array_length(p_payload) not between 1 and 25 then
    raise exception 'Provide 1 to 25 BONAP records' using errcode='22023';
  end if;
  for item in select value from jsonb_array_elements(p_payload) loop
    ids := array_append(ids, public.upsert_bonap_plant(item - 'counties'));
  end loop;
  -- Resolve all county rows once per batch, then insert only missing junction pairs.
  insert into public.county_info(fips,name)
    select distinct on ((c->>'fips')::integer) (c->>'fips')::integer, left(btrim(c->>'name'),100)
    from jsonb_array_elements(p_payload) r
    cross join lateral jsonb_array_elements(coalesce(r->'counties','[]'::jsonb)) c
    order by (c->>'fips')::integer, c->>'name'
    on conflict(fips) do nothing;
  insert into public.plant_county(plant_id,fips)
    select distinct ids[r.position::integer], (c->>'fips')::integer
    from jsonb_array_elements(p_payload) with ordinality r(payload,position)
    cross join lateral jsonb_array_elements(coalesce(r.payload->'counties','[]'::jsonb)) c
    on conflict(fips,plant_id) do nothing;
  return jsonb_build_object('success',true,'count',cardinality(ids),'plant_ids',ids);
end;
$$;
revoke execute on function public.upsert_bonap_plant(jsonb) from public, anon, authenticated;
revoke execute on function public.upsert_bonap_plants_batch(jsonb) from public, anon, authenticated;
grant execute on function public.upsert_bonap_plant(jsonb) to service_role;
grant execute on function public.upsert_bonap_plants_batch(jsonb) to service_role;
commit;
