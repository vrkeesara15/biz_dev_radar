"""Company profiles API (SPEC 10.3): /profiles plus nested sub-resources.

Region gating: fields (or kinds) exclusive to the other region answer 422 with the
offending names. Encrypted fields are masked in every response; a masked value written
back is a no-op. Sub-resource routers are built by subresources.crud_router.
"""

from __future__ import annotations

from app.api.v1.profiles import (
    autofill,
    base,
    certifications,
    codes,
    keyword_suggestions,
    proof,
    teaming,
)

router = base.router
router.include_router(autofill.router)
router.include_router(codes.codes_router)
router.include_router(codes.keywords_router)
router.include_router(codes.service_lines_router)
router.include_router(certifications.router)
router.include_router(teaming.router)
router.include_router(keyword_suggestions.router)
for _proof_router in proof.ROUTERS:
    router.include_router(_proof_router)
