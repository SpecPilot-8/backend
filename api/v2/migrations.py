"""Additive migration; never rewrites v1 or CLI data."""
from sqlalchemy import Column, MetaData, String, Table, select, inspect, text
from api.database import Base
from api.v2 import storage  # noqa: F401

metadata = MetaData()
ledger = Table('api_schema_migration', metadata, Column('version', String(100), primary_key=True))


def upgrade(engine):
    with engine.begin() as connection:
        metadata.create_all(connection)
        Base.metadata.create_all(connection, tables=[t for t in Base.metadata.sorted_tables if t.name.startswith('api_v2_')])
        if connection.scalar(select(ledger.c.version).where(ledger.c.version == '0001_v2_resources')) is None:
            connection.execute(ledger.insert().values(version='0001_v2_resources'))

        if 'tech_stack' not in {c['name'] for c in inspect(connection).get_columns('api_v2_project')}:
            connection.execute(text("ALTER TABLE api_v2_project ADD COLUMN tech_stack VARCHAR(32) NOT NULL DEFAULT 'auto'"))
        if connection.scalar(select(ledger.c.version).where(ledger.c.version == '0002_project_tech_stack')) is None:
            connection.execute(ledger.insert().values(version='0002_project_tech_stack'))
