"""
theorycraft -- a Deadlock damage-over-time simulator for build theorycrafting.

Library only, no UI. Data comes from the public /v1/assets endpoints (cached),
gaps are filled from deadlock.wiki; see MECHANICS.md.
"""
from .data import Catalog, build_catalog, catalog, refresh_catalog
from .simulate import DEFAULT_SOULS_CURVE, Result, SoulsCurve, Target, evaluate, simulate

__all__ = ["Catalog", "build_catalog", "catalog", "refresh_catalog", "DEFAULT_SOULS_CURVE",
           "Result", "SoulsCurve", "Target", "evaluate", "simulate"]
