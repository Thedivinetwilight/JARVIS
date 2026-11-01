"""J.A.R.V.I.S. product server (application area, separate from the evaluator).

This package is the **server foundation**: configuration and startup validation, client
pairing and revocation, scoped manifests, local persistence with migrations, a
default-deny egress policy, health/readiness/limitation reporting, the voice-first audio
session protocol, and one real everyday-assistance capability (tasks and reminders).

It is deliberately not an assistant: no model is selected (gate G-O1 is blocked), no
speech adapter exists, and nothing here claims a product capability from
``docs/product/capability-specification.md``. See
``docs/product/implementation-status.md`` for the boundary.
"""

__all__ = ["__version__"]

#: Server implementation version. Not a product spec version and not a capability claim.
__version__ = "0.1.0"
