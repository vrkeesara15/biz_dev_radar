"""Company profiles API (SPEC 10.3): /profiles plus nested sub-resources.

Region gating: fields (or kinds) exclusive to the other region answer 422 with the
offending names. Encrypted fields are masked in every response; a masked value written
back is a no-op. Sub-resource routers are built by subresources.crud_router.
"""

from __future__ import annotations

from app.api.v1.profiles import base, certifications

router = base.router
router.include_router(certifications.router)
