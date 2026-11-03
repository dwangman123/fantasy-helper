"""Copy every table from one database into another: e.g. the local SQLite file into the
deployed Postgres, or back. Entry point: `fh-copy-db SOURCE_URL` copies into the configured
database (FH_DATABASE_URL), which must be empty and migrated to the same revision."""

import argparse
import sys

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, func, select, text

from fantasy_helper.config import get_settings, psycopg_url
from fantasy_helper.db.models import Base
from fantasy_helper.db.session import make_engine

BATCH = 1000


def revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def copy_database(source: Engine, target: Engine) -> dict[str, int]:
    """Rows copied per table, parents before children, in one transaction on the target."""
    counts: dict[str, int] = {}
    with source.connect() as src, target.begin() as dst:
        for table in Base.metadata.sorted_tables:
            if dst.execute(select(func.count()).select_from(table)).scalar():
                raise ValueError(f"target table {table.name} is not empty")
        for table in Base.metadata.sorted_tables:
            rows = [dict(r._mapping) for r in src.execute(select(table))]
            for i in range(0, len(rows), BATCH):
                dst.execute(table.insert(), rows[i : i + BATCH])
            counts[table.name] = len(rows)
        if target.dialect.name == "postgresql":
            # Rows came with their ids, so move each id sequence past them.
            for table in Base.metadata.sorted_tables:
                for col in table.primary_key.columns:
                    if col.autoincrement is True:
                        dst.execute(
                            text(
                                f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col.name}'), "
                                f"coalesce(max({col.name}), 1), max({col.name}) IS NOT NULL) FROM {table.name}"
                            )
                        )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy every table into the configured (empty) database.")
    parser.add_argument("source", help="SQLAlchemy URL to copy from, e.g. sqlite:///fantasy.db")
    args = parser.parse_args()
    source, target = make_engine(psycopg_url(args.source)), make_engine(get_settings().database_url)
    if source.url == target.url:
        sys.exit("source and target are the same database")
    if (src_rev := revision(source)) != (dst_rev := revision(target)):
        sys.exit(f"revisions differ (source {src_rev}, target {dst_rev}): run `alembic upgrade head` on both")
    try:
        counts = copy_database(source, target)
    except ValueError as exc:
        sys.exit(str(exc))
    for name, n in counts.items():
        print(f"{name:28s} {n:7d}")
    print(f"copied into {target.url.render_as_string(hide_password=True)}")


if __name__ == "__main__":
    main()
