# Bundled reference data

- `naics_2022.csv` — full 2022 NAICS 6-digit industry list (1,012 codes), converted from the
  U.S. Census Bureau file `6-digit_2022_Codes.xlsx` (https://www.census.gov/naics/2022NAICS/).
  Columns: `code,title`. Loaded by `app.core.reference.naics_codes()`.
- `sba_size_standards.csv` — SBA Table of Small Business Size Standards effective
  March 17, 2023 (matched to 2022 NAICS), converted from
  `sba-table-of-size-standards_effective-march-17-2023_v0.xlsx` on data.sba.gov.
  Columns: `naics,title,receipts_millions,employees,footnote`; exactly one of
  `receipts_millions` / `employees` is set. 974 industries. Rows the SBA expresses as
  exceptions ("541330 (Exception 1)") or in assets (commercial banking, $850M assets)
  are not included; those codes resolve to `unknown` in `core.eligibility`.
  Loaded by `app.core.reference.sba_size_standards()`.
