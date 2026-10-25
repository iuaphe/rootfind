


SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE EXTENSION IF NOT EXISTS "pg_stat_statements" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";






CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";






CREATE TYPE "public"."foliage_type" AS ENUM (
    'deciduous',
    'evergreen'
);


ALTER TYPE "public"."foliage_type" OWNER TO "postgres";


CREATE TYPE "public"."life_cycle_enum" AS ENUM (
    'perennial',
    'biennial',
    'annual'
);


ALTER TYPE "public"."life_cycle_enum" OWNER TO "postgres";


CREATE TYPE "public"."sun_type" AS ENUM (
    'full sun',
    'part shade',
    'full shade'
);


ALTER TYPE "public"."sun_type" OWNER TO "postgres";


CREATE TYPE "public"."water_type" AS ENUM (
    'high',
    'med',
    'low'
);


ALTER TYPE "public"."water_type" OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."plant_counties"("p_plant_ids" integer[]) RETURNS "jsonb"
    LANGUAGE "plpgsql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
begin
  if p_plant_ids is null or cardinality(p_plant_ids) > 1000
    or coalesce(array_ndims(p_plant_ids), 1) <> 1
    or exists (select 1 from unnest(p_plant_ids) id where id is null or id <= 0)
  then
    raise exception 'Provide up to 1000 positive integer plant IDs' using errcode = '22023';
  end if;

  return (
    select coalesce(jsonb_agg(counties.fips order by requested.position), '[]'::jsonb)
    from unnest(p_plant_ids) with ordinality as requested(plant_id, position)
    cross join lateral (
      select coalesce(jsonb_agg(pc.fips order by pc.fips), '[]'::jsonb) as fips
      from public.plant_county pc
      where pc.plant_id = requested.plant_id
    ) counties
  );
end;
$$;


ALTER FUNCTION "public"."plant_counties"("p_plant_ids" integer[]) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."plant_details"("p_plant_id" integer) RETURNS "jsonb"
    LANGUAGE "plpgsql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
begin
  if p_plant_id is null or p_plant_id <= 0 then
    raise exception 'plantId must be a positive integer' using errcode = '22023';
  end if;

  return (
    select to_jsonb(p) || jsonb_build_object(
      'common_names', (
        select coalesce(jsonb_agg(cn.name order by cn.name), '[]'::jsonb)
        from public.common_plant cp
        join public.common_names cn on cn.name = cp.name
        where cp.plant_id = p.plant_id
      ),
      'county_fips', (
        select coalesce(jsonb_agg(pc.fips order by pc.fips), '[]'::jsonb)
        from public.plant_county pc where pc.plant_id = p.plant_id
      )
    )
    from public.plants p where p.plant_id = p_plant_id
  );
end;
$$;


ALTER FUNCTION "public"."plant_details"("p_plant_id" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."search_plants"("p_filters" "jsonb" DEFAULT '{}'::"jsonb") RETURNS "jsonb"
    LANGUAGE "plpgsql" STABLE SECURITY DEFINER
    SET "search_path" TO ''
    AS $_$
declare
  search_text text := '';
  filter_key text;
  allowed_options text[];
begin
  if p_filters is null or jsonb_typeof(p_filters) <> 'object' then
    raise exception 'Expected a search filter object' using errcode = '22023';
  end if;
  for filter_key in select jsonb_object_keys(p_filters) loop
    if filter_key not in ('search', 'lifeCycle', 'foliage', 'sun', 'water',
      'isWoody', 'isTree', 'edible', 'thorns', 'minHeight', 'maxHeight', 'minWidth', 'maxWidth') then
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
      when 'water' then array['high', 'med', 'low'] end;
    if jsonb_array_length(p_filters->filter_key) = 0 or exists (
      select 1 from jsonb_array_elements(p_filters->filter_key) as item(value)
      where jsonb_typeof(value) <> 'string' or not (value #>> '{}') = any(allowed_options)
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
    select coalesce(jsonb_agg(p.plant_id order by p.plant_id), '[]'::jsonb)
    from public.plants p
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
    and (not p_filters ? 'sun' or p.sun::text in (select jsonb_array_elements_text(p_filters->'sun')))
    and (not p_filters ? 'water' or p.water::text in (select jsonb_array_elements_text(p_filters->'water')))
    and (not p_filters ? 'isWoody' or p.is_woody = (p_filters->>'isWoody')::boolean)
    and (not p_filters ? 'isTree' or p.is_tree = (p_filters->>'isTree')::boolean)
    and (not p_filters ? 'edible' or p.edible = (p_filters->>'edible')::boolean)
    and (not p_filters ? 'thorns' or p.thorns = (p_filters->>'thorns')::boolean)
    and (not p_filters ? 'minHeight' or p.maxheight >= (p_filters->>'minHeight')::integer)
    and (not p_filters ? 'maxHeight' or p.minheight <= (p_filters->>'maxHeight')::integer)
    and (not p_filters ? 'minWidth' or p.maxwidth >= (p_filters->>'minWidth')::integer)
    and (not p_filters ? 'maxWidth' or p.minwidth <= (p_filters->>'maxWidth')::integer)
  );
end;
$_$;


ALTER FUNCTION "public"."search_plants"("p_filters" "jsonb") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."upsert_plant"("p_payload" "jsonb") RETURNS integer
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
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


ALTER FUNCTION "public"."upsert_plant"("p_payload" "jsonb") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."upsert_plant_taxonomy"("p_plant_id" integer, "p_taxonomy" "jsonb") RETURNS "void"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
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


ALTER FUNCTION "public"."upsert_plant_taxonomy"("p_plant_id" integer, "p_taxonomy" "jsonb") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."upsert_plants_batch"("p_payload" "jsonb") RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    SET "search_path" TO ''
    AS $$
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


ALTER FUNCTION "public"."upsert_plants_batch"("p_payload" "jsonb") OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."common_names" (
    "name" character varying(100) NOT NULL
);


ALTER TABLE "public"."common_names" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."common_plant" (
    "name" character varying(100) NOT NULL,
    "plant_id" integer NOT NULL
);


ALTER TABLE "public"."common_plant" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."county_info" (
    "fips" integer NOT NULL,
    "name" character varying(100) NOT NULL
);


ALTER TABLE "public"."county_info" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."plant_county" (
    "fips" integer NOT NULL,
    "plant_id" integer NOT NULL
);


ALTER TABLE "public"."plant_county" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."plant_taxonomy" (
    "plant_id" integer NOT NULL,
    "kingdom" character varying(50) DEFAULT 'Plantae'::character varying,
    "subkingdom" character varying(100),
    "superdivision" character varying(100),
    "division" character varying(100),
    "division_common" character varying(100),
    "class" character varying(100),
    "class_common" character varying(100),
    "subclass" character varying(100),
    "order_name" character varying(100),
    "family" character varying(100) NOT NULL,
    "family_common" character varying(100),
    "lineage" "jsonb" DEFAULT '[]'::"jsonb" NOT NULL
);


ALTER TABLE "public"."plant_taxonomy" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."plants" (
    "plant_id" integer NOT NULL,
    "genus" character varying(100) NOT NULL,
    "species" character varying(100) NOT NULL,
    "life_cycle" "public"."life_cycle_enum",
    "foliage" "public"."foliage_type",
    "minheight" smallint,
    "maxheight" smallint,
    "minwidth" smallint,
    "maxwidth" smallint,
    "is_woody" boolean,
    "is_tree" boolean,
    "edible" boolean,
    "thorns" boolean,
    "sun" "public"."sun_type",
    "water" "public"."water_type",
    "image_link" "text",
    "misc" "text"[]
);


ALTER TABLE "public"."plants" OWNER TO "postgres";


ALTER TABLE "public"."plants" ALTER COLUMN "plant_id" ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME "public"."plants_plant_id_seq"
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);



CREATE OR REPLACE VIEW "public"."plants_with_taxonomy" AS
 SELECT "p"."plant_id",
    "p"."genus",
    "p"."species",
    "p"."life_cycle",
    "p"."foliage",
    "p"."minheight",
    "p"."maxheight",
    "p"."minwidth",
    "p"."maxwidth",
    "p"."is_woody",
    "p"."is_tree",
    "p"."edible",
    "p"."thorns",
    "p"."sun",
    "p"."water",
    "p"."image_link",
    "p"."misc",
    "t"."division",
    "t"."division_common",
    "t"."class",
    "t"."class_common",
    "t"."order_name",
    "t"."family",
    "t"."family_common",
    "t"."lineage" AS "taxonomy_lineage"
   FROM ("public"."plants" "p"
     LEFT JOIN "public"."plant_taxonomy" "t" ON (("t"."plant_id" = "p"."plant_id")));


ALTER VIEW "public"."plants_with_taxonomy" OWNER TO "postgres";


ALTER TABLE ONLY "public"."common_names"
    ADD CONSTRAINT "common_names_pkey" PRIMARY KEY ("name");



ALTER TABLE ONLY "public"."common_plant"
    ADD CONSTRAINT "common_plant_pkey" PRIMARY KEY ("name", "plant_id");



ALTER TABLE ONLY "public"."county_info"
    ADD CONSTRAINT "county_info_pkey" PRIMARY KEY ("fips");



ALTER TABLE ONLY "public"."plant_county"
    ADD CONSTRAINT "plant_county_pkey" PRIMARY KEY ("fips", "plant_id");



ALTER TABLE ONLY "public"."plant_taxonomy"
    ADD CONSTRAINT "plant_taxonomy_pkey" PRIMARY KEY ("plant_id");



ALTER TABLE ONLY "public"."plants"
    ADD CONSTRAINT "plants_pkey" PRIMARY KEY ("plant_id");



ALTER TABLE ONLY "public"."plants"
    ADD CONSTRAINT "uq_genus_species" UNIQUE ("genus", "species");



CREATE INDEX "common_plant_plant_id_idx" ON "public"."common_plant" USING "btree" ("plant_id");



CREATE INDEX "plant_county_plant_id_idx" ON "public"."plant_county" USING "btree" ("plant_id");



CREATE INDEX "plant_taxonomy_class_idx" ON "public"."plant_taxonomy" USING "btree" ("class");



CREATE INDEX "plant_taxonomy_division_idx" ON "public"."plant_taxonomy" USING "btree" ("division");



CREATE INDEX "plant_taxonomy_family_common_idx" ON "public"."plant_taxonomy" USING "btree" ("lower"(("family_common")::"text"));



CREATE INDEX "plant_taxonomy_family_idx" ON "public"."plant_taxonomy" USING "btree" ("family");



CREATE INDEX "plant_taxonomy_lineage_gin" ON "public"."plant_taxonomy" USING "gin" ("lineage");



CREATE INDEX "plant_taxonomy_order_idx" ON "public"."plant_taxonomy" USING "btree" ("order_name");



ALTER TABLE ONLY "public"."common_plant"
    ADD CONSTRAINT "common_plant_name_fkey" FOREIGN KEY ("name") REFERENCES "public"."common_names"("name") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."common_plant"
    ADD CONSTRAINT "common_plant_plant_id_fkey" FOREIGN KEY ("plant_id") REFERENCES "public"."plants"("plant_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."plant_county"
    ADD CONSTRAINT "plant_county_fips_fkey" FOREIGN KEY ("fips") REFERENCES "public"."county_info"("fips") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."plant_county"
    ADD CONSTRAINT "plant_county_plant_id_fkey" FOREIGN KEY ("plant_id") REFERENCES "public"."plants"("plant_id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."plant_taxonomy"
    ADD CONSTRAINT "plant_taxonomy_plant_id_fkey" FOREIGN KEY ("plant_id") REFERENCES "public"."plants"("plant_id") ON DELETE CASCADE;



ALTER TABLE "public"."plant_taxonomy" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "plant_taxonomy_read" ON "public"."plant_taxonomy" FOR SELECT TO "authenticated", "anon" USING (true);





ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";


GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";






















































































































































REVOKE ALL ON FUNCTION "public"."plant_counties"("p_plant_ids" integer[]) FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."plant_counties"("p_plant_ids" integer[]) TO "anon";
GRANT ALL ON FUNCTION "public"."plant_counties"("p_plant_ids" integer[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."plant_counties"("p_plant_ids" integer[]) TO "service_role";



REVOKE ALL ON FUNCTION "public"."plant_details"("p_plant_id" integer) FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."plant_details"("p_plant_id" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."plant_details"("p_plant_id" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."plant_details"("p_plant_id" integer) TO "service_role";



REVOKE ALL ON FUNCTION "public"."search_plants"("p_filters" "jsonb") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."search_plants"("p_filters" "jsonb") TO "authenticated";
GRANT ALL ON FUNCTION "public"."search_plants"("p_filters" "jsonb") TO "service_role";
GRANT ALL ON FUNCTION "public"."search_plants"("p_filters" "jsonb") TO "anon";



REVOKE ALL ON FUNCTION "public"."upsert_plant"("p_payload" "jsonb") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."upsert_plant"("p_payload" "jsonb") TO "authenticated";
GRANT ALL ON FUNCTION "public"."upsert_plant"("p_payload" "jsonb") TO "service_role";



GRANT ALL ON FUNCTION "public"."upsert_plant_taxonomy"("p_plant_id" integer, "p_taxonomy" "jsonb") TO "anon";
GRANT ALL ON FUNCTION "public"."upsert_plant_taxonomy"("p_plant_id" integer, "p_taxonomy" "jsonb") TO "authenticated";
GRANT ALL ON FUNCTION "public"."upsert_plant_taxonomy"("p_plant_id" integer, "p_taxonomy" "jsonb") TO "service_role";



REVOKE ALL ON FUNCTION "public"."upsert_plants_batch"("p_payload" "jsonb") FROM PUBLIC;
GRANT ALL ON FUNCTION "public"."upsert_plants_batch"("p_payload" "jsonb") TO "authenticated";
GRANT ALL ON FUNCTION "public"."upsert_plants_batch"("p_payload" "jsonb") TO "service_role";


















GRANT ALL ON TABLE "public"."common_names" TO "anon";
GRANT ALL ON TABLE "public"."common_names" TO "authenticated";
GRANT ALL ON TABLE "public"."common_names" TO "service_role";



GRANT ALL ON TABLE "public"."common_plant" TO "anon";
GRANT ALL ON TABLE "public"."common_plant" TO "authenticated";
GRANT ALL ON TABLE "public"."common_plant" TO "service_role";



GRANT ALL ON TABLE "public"."county_info" TO "anon";
GRANT ALL ON TABLE "public"."county_info" TO "authenticated";
GRANT ALL ON TABLE "public"."county_info" TO "service_role";



GRANT ALL ON TABLE "public"."plant_county" TO "anon";
GRANT ALL ON TABLE "public"."plant_county" TO "authenticated";
GRANT ALL ON TABLE "public"."plant_county" TO "service_role";



GRANT ALL ON TABLE "public"."plant_taxonomy" TO "anon";
GRANT ALL ON TABLE "public"."plant_taxonomy" TO "authenticated";
GRANT ALL ON TABLE "public"."plant_taxonomy" TO "service_role";



GRANT ALL ON TABLE "public"."plants" TO "anon";
GRANT ALL ON TABLE "public"."plants" TO "authenticated";
GRANT ALL ON TABLE "public"."plants" TO "service_role";



GRANT ALL ON SEQUENCE "public"."plants_plant_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."plants_plant_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."plants_plant_id_seq" TO "service_role";



GRANT ALL ON TABLE "public"."plants_with_taxonomy" TO "anon";
GRANT ALL ON TABLE "public"."plants_with_taxonomy" TO "authenticated";
GRANT ALL ON TABLE "public"."plants_with_taxonomy" TO "service_role";









ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";































