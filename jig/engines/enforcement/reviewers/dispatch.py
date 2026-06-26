"""Reviewer federation dispatch — the new home for reviewer selection/dispatch.

Bones: re-exports the FULL public surface of ``jig.reviewers.dispatch`` so the
Enforcement package owns this surface and code can migrate imports to the new
home without ``ImportError``. The physical move is deferred to MVP — moving it
now would create a ``jig.reviewers/__init__`` <-> ``dispatch`` import cycle (the
package init imports from dispatch, and dispatch imports ``jig.reviewers.comment``).
MVP relocates the federation here and wires Build to invoke it through ``Review``.

The star re-export is driven by the canonical ``__all__``, so the surface stays
in lockstep automatically.
"""

from __future__ import annotations

from jig.reviewers import dispatch as _canonical
from jig.reviewers.dispatch import *  # noqa: F401,F403

__all__ = list(_canonical.__all__)
