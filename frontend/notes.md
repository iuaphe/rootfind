db password: jkFajULnLbuxLc3n

Let's improve the search bar and add filters. The search bar should take up the width of the screen, and there should be a filter button to the right of it. When you click the filter button, it should open the filter menu. Base the filters off of this snippet from the SQL:

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

min/max properties should be a two-sided slider. booleans should be checkboxes. things with enum values should have buttons for each that you can select. e.g., for life cycle, it'll say "life cycle" and then have three buttons under it, one for each type. holler if you have any questions/clarifications.
