"""Rules-first grievance routing.

The learned router is deliberately deferred; this package provides the
deterministic backbone the serving wire-up can use for the demo.

``DEFAULT_ROUTER`` sources routing from the real ORTPSA master tables
(``data/raw/janasunani-mappings/``) where a mapping is derivable, falling back
to the illustrative ``RuleRouter`` rules and then a generic fallback -- see
``janasunani.routing.mappings`` and ``janasunani.routing.rules.MappingRouter``.
"""

from janasunani.routing.mappings import MappingTables, load_mapping_tables
from janasunani.routing.rules import (
    DEFAULT_ROUTER,
    MappingRouter,
    RouteInput,
    RouteRule,
    RuleRouter,
)

__all__ = [
    "DEFAULT_ROUTER",
    "MappingRouter",
    "MappingTables",
    "RouteInput",
    "RouteRule",
    "RuleRouter",
    "load_mapping_tables",
]
