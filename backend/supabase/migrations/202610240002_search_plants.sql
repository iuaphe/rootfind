-- Static SQL and typed casts: search text never becomes executable SQL.
create or replace function public.search_plants(p_filters jsonb default '{}')
returns jsonb
language sql stable security definer
set search_path = ''
as $$
  with matched as materialized (
    select p.*
    from public.plants p
    where (
      coalesce(p_filters->>'search', '') = ''
      or strpos(lower(p.genus || ' ' || p.species), lower(p_filters->>'search')) > 0
      or exists (
        select 1 from public.common_plant cp
        join public.common_names cn on cn.name = cp.name
        where cp.plant_id = p.plant_id
          and strpos(lower(cn.name), lower(p_filters->>'search')) > 0
      )
    )
    and (not p_filters ? 'isWoody' or p.is_woody = (p_filters->>'isWoody')::boolean)
    and (not p_filters ? 'isTree' or p.is_tree = (p_filters->>'isTree')::boolean)
    and (not p_filters ? 'edible' or p.edible = (p_filters->>'edible')::boolean)
    and (not p_filters ? 'thorns' or p.thorns = (p_filters->>'thorns')::boolean)
    and (not p_filters ? 'lifeCycle' or p.life_cycle::text in (select jsonb_array_elements_text(p_filters->'lifeCycle')))
    and (not p_filters ? 'foliage' or p.foliage::text in (select jsonb_array_elements_text(p_filters->'foliage')))
    and (not p_filters ? 'sun' or p.sun::text in (select jsonb_array_elements_text(p_filters->'sun')))
    and (not p_filters ? 'water' or p.water::text in (select jsonb_array_elements_text(p_filters->'water')))
    and (not p_filters ? 'misc' or p.misc && array(select jsonb_array_elements_text(p_filters->'misc')))
    and (not p_filters ? 'county' or exists (
      select 1 from public.plant_county pc where pc.plant_id = p.plant_id
      and pc.fips in (select value::integer from jsonb_array_elements_text(p_filters->'county'))
    ))
    -- Requested measurement intervals overlap the plant's recorded range.
    and (not p_filters ? 'minHeight' or p.maxheight >= (p_filters->>'minHeight')::integer)
    and (not p_filters ? 'maxHeight' or p.minheight <= (p_filters->>'maxHeight')::integer)
    and (not p_filters ? 'minWidth' or p.maxwidth >= (p_filters->>'minWidth')::integer)
    and (not p_filters ? 'maxWidth' or p.minwidth <= (p_filters->>'maxWidth')::integer)
  ), page as (
    select * from matched order by lower(genus), lower(species), plant_id
    limit greatest(1, least(100, coalesce((p_filters->>'pageSize')::integer, 25)))
    offset (greatest(1, least(10000, coalesce((p_filters->>'page')::integer, 1))) - 1)::bigint
      * greatest(1, least(100, coalesce((p_filters->>'pageSize')::integer, 25)))
  )
  select jsonb_build_object(
    'data', coalesce((select jsonb_agg(to_jsonb(page) order by lower(genus), lower(species), plant_id) from page), '[]'::jsonb),
    'total', (select count(*) from matched),
    'page', greatest(1, least(10000, coalesce((p_filters->>'page')::integer, 1))),
    'pageSize', greatest(1, least(100, coalesce((p_filters->>'pageSize')::integer, 25)))
  );
$$;

revoke execute on function public.search_plants(jsonb) from public, anon;
grant execute on function public.search_plants(jsonb) to authenticated;
