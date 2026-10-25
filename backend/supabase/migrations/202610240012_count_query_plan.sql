-- Evaluate plant filters before joining county links. Inlining the match into
-- the join let the planner scan every county link for selective name searches.
-- Match common names as a set, avoiding repeated alias lookups per plant.
-- Accept both the original scalar sun column and the hosted enum-array column.
begin;

create or replace function rootfind_private.matching_plant_ids(p_filters jsonb)
returns table (plant_id integer)
language sql stable
as $$
    select p.plant_id from public.plants p
    where (
      (p_filters->>'search') = ''
      or strpos(lower(p.genus || ' ' || p.species), lower((p_filters->>'search'))) > 0
      or p.plant_id in (
        select cp.plant_id from public.common_plant cp
        where strpos(lower(cp.name), lower(p_filters->>'search')) > 0
      )
    )
    and (not p_filters ? 'lifeCycle' or p.life_cycle::text in (select jsonb_array_elements_text(p_filters->'lifeCycle')))
    and (not p_filters ? 'foliage' or p.foliage::text in (select jsonb_array_elements_text(p_filters->'foliage')))
    and (not p_filters ? 'sun' or to_jsonb(p.sun) ?| array(select jsonb_array_elements_text(p_filters->'sun')))
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
    with matched as materialized (
      select plant_id from rootfind_private.matching_plant_ids(filters)
    ), county_counts as (
      select pc.fips, count(*) as plant_count
      from matched
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

revoke execute on function rootfind_private.matching_plant_ids(jsonb) from public, anon, authenticated;
revoke execute on function public.count_plants_by_county(jsonb) from public;
grant execute on function public.count_plants_by_county(jsonb) to anon, authenticated;

commit;
