"""Strategy layer: Iron Condor + Vertical spread.

Phase 1 focuses on Iron Condor (鐵兀鷹) as primary, with Vertical spread
reserved purely as an IC adjustment/roll tool (not traded standalone).

Modules:
  base          -- Strategy abstract base class
  iron_condor   -- IC open / close / adjust rules
  vertical      -- Vertical spread (Bull Put / Bear Call) primitives for IC rolls

Phase 2 will add ``calendar.py`` for term-structure plays.
"""
