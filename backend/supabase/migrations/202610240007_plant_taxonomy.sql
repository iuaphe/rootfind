-- Migration 007: Plant Taxonomy Storage & Retrieval

-- 1. Create the plant_taxonomy table (1-to-1 extension of public.plants)
create table if not exists public.plant_taxonomy (
  plant_id integer primary key references public.plants(plant_id) on delete cascade,
  
  -- Core botanical ranks (indexed for instant filtering and grouping)
  kingdom varchar(50) default 'Plantae',
  subkingdom varchar(100),
  superdivision varchar(100),
  division varchar(100),           -- e.g. 'Magnoliophyta', 'Coniferophyta'
  division_common varchar(100),    -- e.g. 'Flowering plants', 'Conifers'
  class varchar(100),              -- e.g. 'Magnoliopsida', 'Pinopsida'
  class_common varchar(100),       -- e.g. 'Dicotyledons'
  subclass varchar(100),           -- e.g. 'Asteridae'
  order_name varchar(100),         -- e.g. 'Asterales', 'Pinales'
  family varchar(100) not null,    -- e.g. 'Asteraceae', 'Pinaceae'
  family_common varchar(100),      -- e.g. 'Aster family', 'Pine family'

  -- Full ordered breadcrumb lineage as structured JSONB:
  -- [
  --   {"rank": "Kingdom", "name": "Plantae", "common": "Plants", "symbol": "Plantae"},
  --   {"rank": "Division", "name": "Magnoliophyta", "common": "Flowering plants", "symbol": "Magnoliophyta"},
  --   ...
  --   {"rank": "Family", "name": "Asteraceae", "common": "Aster family", "symbol": "ASTERA"}
  -- ]
  lineage jsonb not null default '[]'::jsonb
);

-- 2. Indexes for fast retrieval & filtering
create index if not exists plant_taxonomy_family_idx on public.plant_taxonomy(family);
create index if not exists plant_taxonomy_family_common_idx on public.plant_taxonomy(lower(family_common));
create index if not exists plant_taxonomy_order_idx on public.plant_taxonomy(order_name);
create index if not exists plant_taxonomy_class_idx on public.plant_taxonomy(class);
create index if not exists plant_taxonomy_division_idx on public.plant_taxonomy(division);
create index if not exists plant_taxonomy_lineage_gin on public.plant_taxonomy using gin (lineage);

-- 3. Row Level Security
alter table public.plant_taxonomy enable row level security;
grant select on public.plant_taxonomy to anon, authenticated;
create policy plant_taxonomy_read on public.plant_taxonomy for select to anon, authenticated using (true);

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant all on public.plant_taxonomy to service_role;
  end if;
end $$;

-- 4. Convenient view combining plant traits with taxonomic details
create or replace view public.plants_with_taxonomy as
select
  p.*,
  t.division,
  t.division_common,
  t.class,
  t.class_common,
  t.order_name,
  t.family,
  t.family_common,
  t.lineage as taxonomy_lineage
from public.plants p
left join public.plant_taxonomy t on t.plant_id = p.plant_id;

grant select on public.plants_with_taxonomy to anon, authenticated;

-- 5. Stored procedure to upsert taxonomy in real-time
create or replace function public.upsert_plant_taxonomy(
  p_plant_id integer,
  p_taxonomy jsonb
)
returns void
language plpgsql security definer
set search_path = ''
as $$
begin
  if p_taxonomy is null or jsonb_typeof(p_taxonomy) <> 'object' then
    return;
  end if;

  insert into public.plant_taxonomy (
    plant_id, kingdom, subkingdom, superdivision,
    division, division_common, class, class_common,
    subclass, order_name, family, family_common, lineage
  ) values (
    p_plant_id,
    coalesce(p_taxonomy->>'kingdom', 'Plantae'),
    p_taxonomy->>'subkingdom',
    p_taxonomy->>'superdivision',
    p_taxonomy->>'division',
    p_taxonomy->>'division_common',
    p_taxonomy->>'class',
    p_taxonomy->>'class_common',
    p_taxonomy->>'subclass',
    p_taxonomy->>'order_name',
    coalesce(p_taxonomy->>'family', 'Unknown'),
    p_taxonomy->>'family_common',
    coalesce(p_taxonomy->'lineage', '[]'::jsonb)
  )
  on conflict (plant_id) do update set
    kingdom = coalesce(excluded.kingdom, public.plant_taxonomy.kingdom),
    subkingdom = coalesce(excluded.subkingdom, public.plant_taxonomy.subkingdom),
    superdivision = coalesce(excluded.superdivision, public.plant_taxonomy.superdivision),
    division = coalesce(excluded.division, public.plant_taxonomy.division),
    division_common = coalesce(excluded.division_common, public.plant_taxonomy.division_common),
    class = coalesce(excluded.class, public.plant_taxonomy.class),
    class_common = coalesce(excluded.class_common, public.plant_taxonomy.class_common),
    subclass = coalesce(excluded.subclass, public.plant_taxonomy.subclass),
    order_name = coalesce(excluded.order_name, public.plant_taxonomy.order_name),
    family = coalesce(excluded.family, public.plant_taxonomy.family),
    family_common = coalesce(excluded.family_common, public.plant_taxonomy.family_common),
    lineage = coalesce(excluded.lineage, public.plant_taxonomy.lineage);
end;
$$;

grant execute on function public.upsert_plant_taxonomy(integer, jsonb) to authenticated;

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function public.upsert_plant_taxonomy(integer, jsonb) to service_role;
  end if;
end $$;
