from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


_LEGACY_SCHEMA_UPGRADE = (
    """DO $migration$
    DECLARE
        primary_key_name text;
        primary_key_definition text;
    BEGIN
        IF to_regclass('public.listings') IS NOT NULL THEN
            ALTER TABLE public.listings
                ADD COLUMN IF NOT EXISTS mode varchar(8) NOT NULL DEFAULT 'LIVE';

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'listings' AND column_name = 'url'
            ) THEN
                ALTER TABLE public.listings ALTER COLUMN url DROP NOT NULL;
            END IF;

            SELECT conname, pg_get_constraintdef(oid)
              INTO primary_key_name, primary_key_definition
              FROM pg_constraint
             WHERE conrelid = to_regclass('public.listings') AND contype = 'p';

            IF primary_key_definition IS DISTINCT FROM
                'PRIMARY KEY (mode, marketplace, listing_id)' THEN
                IF primary_key_name IS NOT NULL THEN
                    EXECUTE format(
                        'ALTER TABLE public.listings DROP CONSTRAINT %I', primary_key_name
                    );
                END IF;
                ALTER TABLE public.listings
                    ADD CONSTRAINT listings_pkey PRIMARY KEY (mode, marketplace, listing_id);
            END IF;
        END IF;
    END;
    $migration$""",
    """DO $migration$
    BEGIN
        IF to_regclass('public.floor_snapshots') IS NOT NULL THEN
            ALTER TABLE public.floor_snapshots
                ADD COLUMN IF NOT EXISTS mode varchar(8) NOT NULL DEFAULT 'LIVE';
        END IF;
    END;
    $migration$""",
    """DO $migration$
    BEGIN
        IF to_regclass('public.opportunities') IS NOT NULL THEN
            ALTER TABLE public.opportunities
                ADD COLUMN IF NOT EXISTS mode varchar(8) NOT NULL DEFAULT 'LIVE',
                ADD COLUMN IF NOT EXISTS collection varchar(256),
                ADD COLUMN IF NOT EXISTS model varchar(256),
                ADD COLUMN IF NOT EXISTS backdrop varchar(256),
                ADD COLUMN IF NOT EXISTS symbol varchar(256),
                ADD COLUMN IF NOT EXISTS gross_spread numeric(30, 9),
                ADD COLUMN IF NOT EXISTS gross_margin_percent numeric(10, 4),
                ADD COLUMN IF NOT EXISTS estimated_net numeric(30, 9);

            IF to_regclass('public.listings') IS NOT NULL THEN
                UPDATE public.opportunities AS opportunity
                   SET collection = listing.collection
                  FROM public.listings AS listing
                 WHERE opportunity.collection IS NULL
                   AND opportunity.marketplace = listing.marketplace
                   AND opportunity.listing_id = listing.listing_id;
            END IF;

            UPDATE public.opportunities
               SET collection = 'Unknown'
             WHERE collection IS NULL;
            ALTER TABLE public.opportunities ALTER COLUMN collection SET NOT NULL;

            UPDATE public.opportunities AS legacy
               SET key = CASE
                   WHEN left(legacy.key, 6) = 'floor:'
                       THEN 'floor:LIVE:' || substr(legacy.key, 7)
                   WHEN left(legacy.key, 4) = 'arb:'
                       THEN 'arb:LIVE:' || substr(legacy.key, 5)
               END
             WHERE legacy.mode = 'LIVE'
               AND (
                   (left(legacy.key, 6) = 'floor:' AND legacy.key NOT LIKE 'floor:LIVE:%'
                       AND legacy.key NOT LIKE 'floor:DEMO:%')
                   OR
                   (left(legacy.key, 4) = 'arb:' AND legacy.key NOT LIKE 'arb:LIVE:%'
                       AND legacy.key NOT LIKE 'arb:DEMO:%')
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM public.opportunities AS existing_row
                    WHERE existing_row.key = CASE
                        WHEN left(legacy.key, 6) = 'floor:'
                            THEN 'floor:LIVE:' || substr(legacy.key, 7)
                        WHEN left(legacy.key, 4) = 'arb:'
                            THEN 'arb:LIVE:' || substr(legacy.key, 5)
                    END
               );

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'opportunities' AND column_name = 'url'
            ) THEN
                ALTER TABLE public.opportunities ALTER COLUMN url DROP NOT NULL;
            END IF;
        END IF;
    END;
    $migration$""",
)

MIGRATIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("0001_legacy_schema_to_demo_live", _LEGACY_SCHEMA_UPGRADE),
)


async def migrate_schema(connection) -> None:
    """Apply ordered, idempotent PostgreSQL schema migrations in the caller's transaction."""
    await connection.exec_driver_sql(
        """CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version varchar(128) PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    await connection.exec_driver_sql("SELECT pg_advisory_xact_lock(71682419021003)")
    result = await connection.exec_driver_sql(
        "SELECT version FROM public.schema_migrations"
    )
    applied = {row[0] for row in result.fetchall()}

    for version, statements in MIGRATIONS:
        if version in applied:
            continue
        logger.info("Applying database migration %s", version)
        for statement in statements:
            await connection.exec_driver_sql(statement)
        await connection.exec_driver_sql(
            "INSERT INTO public.schema_migrations(version) "
            f"VALUES ('{version}') ON CONFLICT (version) DO NOTHING"
        )
