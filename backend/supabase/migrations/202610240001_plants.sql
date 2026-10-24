-- Provisional schema. Measurements must all use the same unit (to be decided).
create type public.life_cycle_enum as enum ('perennial', 'biennial', 'annual');
create type public.foliage_type as enum ('deciduous', 'evergreen');
create type public.sun_type as enum ('full sun', 'part shade', 'full shade');
create type public.water_type as enum ('high', 'med', 'low');

create table public.plants (
  plant_id integer generated always as identity primary key,
  genus varchar(100) not null,
  species varchar(100) not null,
  life_cycle public.life_cycle_enum,
  foliage public.foliage_type,
  minheight smallint,
  maxheight smallint,
  minwidth smallint,
  maxwidth smallint,
  is_woody boolean,
  is_tree boolean,
  edible boolean,
  thorns boolean,
  sun public.sun_type,
  water public.water_type,
  image_link text,
  misc text[],
  constraint uq_genus_species unique (genus, species)
);

create table public.county_info (
  fips integer primary key check (fips between 0 and 99999),
  name varchar(100) not null
);

create table public.plant_county (
  fips integer references public.county_info(fips) on delete cascade,
  plant_id integer references public.plants(plant_id) on delete cascade,
  primary key (fips, plant_id)
);
create index plant_county_plant_id_idx on public.plant_county(plant_id);

create table public.common_names (
  name varchar(100) primary key
);
create table public.common_plant (
  name varchar(100) references public.common_names(name) on delete cascade,
  plant_id integer references public.plants(plant_id) on delete cascade,
  primary key (name, plant_id)
);
create index common_plant_plant_id_idx on public.common_plant(plant_id);

alter table public.plants enable row level security;
alter table public.county_info enable row level security;
alter table public.plant_county enable row level security;
alter table public.common_names enable row level security;
alter table public.common_plant enable row level security;
grant select on public.plants, public.county_info, public.plant_county,
  public.common_names, public.common_plant to authenticated;
create policy plants_read on public.plants for select to authenticated using (true);
create policy counties_read on public.county_info for select to authenticated using (true);
create policy plant_counties_read on public.plant_county for select to authenticated using (true);
create policy common_names_read on public.common_names for select to authenticated using (true);
create policy common_plants_read on public.common_plant for select to authenticated using (true);
