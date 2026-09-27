# SAM.gov Entity Management API v3 fixtures

`entity_ALPHA1234567.json` is SYNTHESIZED from the public Entity Management API v3
documentation (GET `/entity-information/v3/entities?ueiSAM=...&api_key=...`): the field
names and nesting (`entityRegistration`, `coreData.entityInformation`,
`coreData.physicalAddress`, `coreData.generalInformation`, `coreData.businessTypes`,
`assertions.goodsAndServices`) follow the documented response; every value is invented.
No SAM key was available at build time (PROGRESS.md OQ-3/OQ-35); replace with a redacted
live capture when the nightly smoke has a key.

The `999999` NAICS row is deliberate: it exercises the "invalid code is dropped" rule.
