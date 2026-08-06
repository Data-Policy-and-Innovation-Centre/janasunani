"""The analytical layer over the Parquet lake.

Two halves, kept apart on purpose:

* ``sql/`` — the **marts**: governed derived tables, written as portable SQL
  views over ``complaints`` / ``action_history``. These are the artifacts we
  hand to the department, so they must run unmodified against their own
  PostgreSQL, not just against our DuckDB lake.
* ``findings/`` — the **presentations** built on a mart: the aggregate tables,
  the reconciliation, and the Markdown fragment that goes in front of an
  audience, with the caveats that must travel with each number.

Everything here reads the lake (``janasunani.olap.lake``), never OLTP.
"""
