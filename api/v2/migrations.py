"""Additive migration; never rewrites v1 or CLI data."""
from sqlalchemy import Column, MetaData, String, Table, select
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
