"""OLAP layer: the analytical Parquet lake (downstream of the OLTP store).

``materialize`` exports the OLTP tables to Parquet (via DuckDB's sqlite/postgres
scanner — no ORM); ``lake`` provides DuckDB/Polars read helpers over the Parquet
for analytics, ML training, and the demo's history browse/search.
"""
