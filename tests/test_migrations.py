from __future__ import annotations

import asyncio
import unittest

from app.storage.migrations import MIGRATIONS, migrate_schema


class _Result:
    def __init__(self, rows: list[tuple[str]]):
        self._rows = rows

    def fetchall(self) -> list[tuple[str]]:
        return self._rows


class _RecordingConnection:
    def __init__(self, applied: set[str] | None = None):
        self.applied = applied or set()
        self.statements: list[str] = []

    async def exec_driver_sql(self, statement: str):
        self.statements.append(statement)
        if statement == "SELECT version FROM public.schema_migrations":
            return _Result([(version,) for version in self.applied])
        return _Result([])


class MigrationRunnerTests(unittest.TestCase):
    def test_migration_runner_records_and_runs_unapplied_migration_once(self) -> None:
        async def inspect() -> None:
            connection = _RecordingConnection()
            await migrate_schema(connection)
            first_run = list(connection.statements)
            self.assertTrue(any("PRIMARY KEY (mode, marketplace, listing_id)" in sql for sql in first_run))
            self.assertTrue(any("gross_margin_percent" in sql for sql in first_run))
            self.assertTrue(any("floor:LIVE:" in sql and "arb:LIVE:" in sql for sql in first_run))
            self.assertIn(
                "INSERT INTO public.schema_migrations(version) "
                "VALUES ('0001_legacy_schema_to_demo_live') ON CONFLICT (version) DO NOTHING",
                first_run,
            )

            connection = _RecordingConnection({version for version, _ in MIGRATIONS})
            await migrate_schema(connection)
            self.assertEqual(
                connection.statements,
                [
                    """CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version varchar(128) PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
                    "SELECT pg_advisory_xact_lock(71682419021003)",
                    "SELECT version FROM public.schema_migrations",
                ],
            )

        asyncio.run(inspect())


if __name__ == "__main__":
    unittest.main()
