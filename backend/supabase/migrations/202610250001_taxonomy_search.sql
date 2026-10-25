-- Taxonomy filters share exact, case-insensitive semantics across counts and search.
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
  taxonomy_value text;
begin
  if p_filters is null or jsonb_typeof(p_filters) <> 'object' then
    raise exception 'Expected a search filter object' using errcode = '22023';
  end if;
  for filter_key in select jsonb_object_keys(p_filters) loop
    if filter_key not in ('search', 'lifeCycle', 'foliage', 'sun', 'water',
      'isWoody', 'isTree', 'edible', 'thorns', 'minHeight', 'maxHeight', 'minWidth', 'maxWidth',
      'kingdom', 'subkingdom', 'superdivision', 'division', 'division_common',
      'class', 'class_common', 'subclass', 'order_name', 'family', 'family_common') then
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

  foreach filter_key in array array['kingdom', 'subkingdom', 'superdivision',
    'division', 'division_common', 'class', 'class_common', 'subclass',
    'order_name', 'family', 'family_common'] loop
    if not p_filters ? filter_key then continue; end if;
    if jsonb_typeof(p_filters->filter_key) is distinct from 'string' then
      raise exception '% must be a string', filter_key using errcode = '22023';
    end if;
    taxonomy_value := btrim(regexp_replace(p_filters->>filter_key, '\s+', ' ', 'g'));
    if length(taxonomy_value) < 1 or length(taxonomy_value) >
      (case when filter_key = 'kingdom' then 50 else 100 end) then
      raise exception 'Invalid % length', filter_key using errcode = '22023';
    end if;
    p_filters := jsonb_set(p_filters, array[filter_key], to_jsonb(taxonomy_value));
  end loop;

  return p_filters || jsonb_build_object('search', search_text);
end;
$$;

create or replace function rootfind_private.matching_plant_ids(p_filters jsonb)
returns table (plant_id integer)
language sql stable
as $$
    select p.plant_id from public.plants p
    left join public.plant_taxonomy t on t.plant_id = p.plant_id
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
    and (not p_filters ? 'kingdom' or lower(t.kingdom) = lower(p_filters->>'kingdom'))
    and (not p_filters ? 'subkingdom' or lower(t.subkingdom) = lower(p_filters->>'subkingdom'))
    and (not p_filters ? 'superdivision' or lower(t.superdivision) = lower(p_filters->>'superdivision'))
    and (not p_filters ? 'division' or lower(t.division) = lower(p_filters->>'division'))
    and (not p_filters ? 'division_common' or lower(t.division_common) = lower(p_filters->>'division_common'))
    and (not p_filters ? 'class' or lower(t.class) = lower(p_filters->>'class'))
    and (not p_filters ? 'class_common' or lower(t.class_common) = lower(p_filters->>'class_common'))
    and (not p_filters ? 'subclass' or lower(t.subclass) = lower(p_filters->>'subclass'))
    and (not p_filters ? 'order_name' or lower(t.order_name) = lower(p_filters->>'order_name'))
    and (not p_filters ? 'family' or lower(t.family) = lower(p_filters->>'family'))
    and (not p_filters ? 'family_common' or lower(t.family_common) = lower(p_filters->>'family_common'))
;
$$;

-- These expressions also support exact filters when the planner starts with taxonomy.
create index if not exists plant_taxonomy_family_lower_idx on public.plant_taxonomy (lower(family));
create index if not exists plant_taxonomy_division_lower_idx on public.plant_taxonomy (lower(division));
create index if not exists plant_taxonomy_class_lower_idx on public.plant_taxonomy (lower(class));
create index if not exists plant_taxonomy_order_lower_idx on public.plant_taxonomy (lower(order_name));

create or replace function public.suggest_plants(p_query text, p_filters jsonb default '{}')
returns jsonb
language plpgsql stable security definer
set search_path = ''
as $$
declare
  query_text text;
  query_field text;
  filters jsonb;
begin
  if p_query is null or length(p_query) > 200 then
    raise exception 'q must be a string of at most 200 characters' using errcode = '22023';
  end if;
  if p_filters ? 'search' then
    raise exception 'Use q for suggestions' using errcode = '22023';
  end if;
  filters := rootfind_private.validate_plant_filters(p_filters);
  query_text := lower(btrim(regexp_replace(p_query, '\s+', ' ', 'g')));
  if query_text = '' then return '[]'::jsonb; end if;
  if strpos(query_text, '=') > 0 then
    query_field := btrim(split_part(query_text, '=', 1));
    query_text := btrim(substr(query_text, strpos(query_text, '=') + 1));
    if query_field not in ('kingdom', 'subkingdom', 'superdivision', 'division',
      'division_common', 'class', 'class_common', 'subclass', 'order_name', 'family', 'family_common') then
      raise exception 'Unknown taxonomy field: %', query_field using errcode = '22023';
    end if;
    -- Typing an existing field offers replacement values rather than filtering
    -- its suggestions to the value already selected in the chip.
    filters := filters - query_field;
  end if;

  return (
    with matched as materialized (
      select plant_id from rootfind_private.matching_plant_ids(filters)
    ), candidates as (
      select 'taxonomy'::text as kind, v.field, v.value, m.plant_id
      from matched m
      join public.plant_taxonomy t on t.plant_id = m.plant_id
      cross join lateral (values
        ('kingdom', t.kingdom), ('subkingdom', t.subkingdom),
        ('superdivision', t.superdivision), ('division', t.division),
        ('division_common', t.division_common), ('class', t.class),
        ('class_common', t.class_common), ('subclass', t.subclass),
        ('order_name', t.order_name), ('family', t.family), ('family_common', t.family_common)
      ) v(field, value)
      where v.value is not null and btrim(v.value) <> ''
        and (query_field is null or v.field = query_field)
        and (strpos(lower(v.value), query_text) > 0
          or (query_field is null and starts_with(v.field, query_text)))
      union all
      select 'common', null, cp.name, m.plant_id
      from matched m join public.common_plant cp on cp.plant_id = m.plant_id
      where query_field is null and strpos(lower(cp.name), query_text) > 0
      union all
      select 'scientific', null, p.genus || ' ' || p.species, m.plant_id
      from matched m join public.plants p on p.plant_id = m.plant_id
      where query_field is null and strpos(lower(p.genus || ' ' || p.species), query_text) > 0
    ), unique_suggestions as (
      select kind, field, min(value) as value, count(distinct plant_id) as frequency,
        case kind when 'taxonomy' then 0 when 'common' then 1 else 2 end as kind_rank,
        case when lower(value) = query_text then 0
          when starts_with(lower(value), query_text) then 1 else 2 end as match_rank
      from candidates
      group by kind, field, lower(value)
    ), suggestions as (
      select * from unique_suggestions
      order by kind_rank, match_rank, frequency desc, lower(value), field
      limit 12
    )
    select coalesce(jsonb_agg(
      jsonb_build_object('kind', kind, 'field', field, 'value', value)
      order by kind_rank, match_rank, frequency desc, lower(value), field
    ), '[]'::jsonb) from suggestions
  );
end;
$$;

revoke execute on function rootfind_private.validate_plant_filters(jsonb) from public, anon, authenticated;
revoke execute on function rootfind_private.matching_plant_ids(jsonb) from public, anon, authenticated;
revoke execute on function public.suggest_plants(text, jsonb) from public;
grant execute on function public.suggest_plants(text, jsonb) to anon, authenticated;

commit;
