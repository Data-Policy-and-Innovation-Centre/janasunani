"""Rules-first grievance routing.

The learned router is deliberately deferred; this package provides the
deterministic backbone the serving wire-up can use for the demo.
"""

from janasunani.routing.rules import DEFAULT_ROUTER, RouteInput, RouteRule, RuleRouter

__all__ = ["DEFAULT_ROUTER", "RouteInput", "RouteRule", "RuleRouter"]
