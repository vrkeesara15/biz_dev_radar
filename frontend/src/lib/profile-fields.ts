/**
 * Single source of truth for which company-profile fields exist, which
 * onboarding step they belong to and which region may see them.
 *
 * Mirrors the "Region" column of SPEC.md section 4 (and the backend's
 * US_ONLY_FIELDS / IN_ONLY_FIELDS, code_scheme_allowed, certification_allowed,
 * registration_allowed). Every step renders only `fieldsForRegion(...)`, so a
 * region-foreign field is never shown; `stripRegionForeign` is the last line
 * of defence before a payload leaves the browser.
 */
import type { Region } from "@/lib/region";

export type { Region };
export type FieldRegion = "both" | "US" | "IN";
export type StepId = 1 | 2 | 3 | 4 | 5 | 6 | 7;

export type FieldKind =
  | "text"
  | "email"
  | "url"
  | "tel"
  | "number"
  | "percent"
  | "date"
  | "enum"
  | "multi-enum"
  | "money"
  | "boolean"
  | "tags"
  | "list"
  | "masked";

export type Option<T extends string = string> = { value: T; label: string };

export type FieldMeta = {
  /** Profile column or sub-resource name (dotted for nested scopes). */
  key: string;
  label: string;
  step: StepId;
  region: FieldRegion;
  kind: FieldKind;
  /** `true` = required everywhere; a region = required there only. */
  required?: boolean | Region;
  /** Optional grouping shown as a sub-heading inside the step. */
  group?: string;
  help?: string;
  /** Stored encrypted and returned masked ("•••••1234"). */
  sensitive?: boolean;
};

export const STEPS: readonly { id: StepId; title: string; short: string; description: string }[] = [
  { id: 1, title: "Identity & registrations", short: "Identity", description: "Who you are and how you are registered." },
  { id: 2, title: "Size & status", short: "Size", description: "Headcount, revenue and socio-economic status." },
  { id: 3, title: "What we sell", short: "Offerings", description: "Codes, keywords and service lines used for matching." },
  { id: 4, title: "Where & how big", short: "Scope", description: "Geography, buyers, value range and teaming." },
  { id: 5, title: "Proof", short: "Proof", description: "Past performance, people, certifications and files for drafting." },
  { id: 6, title: "Preferences", short: "Preferences", description: "Alerts, thresholds, weights and languages." },
  { id: 7, title: "Review", short: "Review", description: "Check completeness and finish." },
];

export const toApiRegion = (region: Region): "us" | "in" => (region === "IN" ? "in" : "us");
export const fromApiRegion = (region: string | null | undefined): Region =>
  region?.toLowerCase() === "in" ? "IN" : "US";

/** Region-aware option: shown only where `region` allows it. */
export type RegionOption<T extends string = string> = Option<T> & { region: FieldRegion };

export function optionsForRegion<T extends string>(
  options: readonly RegionOption<T>[],
  region: Region,
): RegionOption<T>[] {
  return options.filter((o) => o.region === "both" || o.region === region);
}

export function isAllowedInRegion(fieldRegion: FieldRegion, region: Region): boolean {
  return fieldRegion === "both" || fieldRegion === region;
}

// ---------------------------------------------------------------------------
// Enumerations (values match the backend enums exactly)
// ---------------------------------------------------------------------------

export const LEGAL_STRUCTURES: readonly RegionOption[] = [
  { value: "llc", label: "LLC", region: "US" },
  { value: "corporation", label: "Corporation", region: "US" },
  { value: "pvt_ltd", label: "Private Limited", region: "IN" },
  { value: "llp", label: "LLP", region: "both" },
  { value: "partnership", label: "Partnership", region: "both" },
  { value: "proprietorship", label: "Proprietorship", region: "both" },
  { value: "other", label: "Other", region: "both" },
];

export const ADDRESS_KINDS: readonly Option[] = [
  { value: "registered", label: "Registered" },
  { value: "hq", label: "Headquarters" },
  { value: "branch", label: "Branch office" },
];

export const SAM_STATUSES: readonly Option[] = [
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "expired", label: "Expired" },
  { value: "pending", label: "Pending" },
];

export const UDYAM_CATEGORIES: readonly Option[] = [
  { value: "micro", label: "Micro" },
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
];

export const LOCAL_SUPPLIER_CLASSES: readonly Option[] = [
  { value: "class_1", label: "Class-I local supplier" },
  { value: "class_2", label: "Class-II local supplier" },
  { value: "non_local", label: "Non-local supplier" },
];

export const MSE_OWNERSHIP: readonly Option[] = [
  { value: "none", label: "None" },
  { value: "sc_st", label: "SC/ST-owned MSE" },
  { value: "women", label: "Women-owned MSE" },
  { value: "sc_st_women", label: "SC/ST and women-owned MSE" },
];

export const CODE_SCHEMES: readonly RegionOption[] = [
  { value: "naics", label: "NAICS", region: "US" },
  { value: "psc", label: "PSC", region: "US" },
  { value: "aln", label: "ALN / CFDA", region: "US" },
  { value: "gem", label: "GeM category", region: "IN" },
  { value: "india_category", label: "Indian product category", region: "IN" },
];

export const KEYWORD_KINDS: readonly Option[] = [
  { value: "include", label: "Include" },
  { value: "exclude", label: "Exclude" },
];

export const DELIVERY_MODELS: readonly Option[] = [
  { value: "onsite", label: "On-site" },
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
  { value: "offshore", label: "Offshore" },
];

export const NOTICE_TYPES: readonly RegionOption[] = [
  { value: "sources_sought", label: "Sources sought", region: "US" },
  { value: "rfi", label: "RFI", region: "both" },
  { value: "presolicitation", label: "Presolicitation", region: "US" },
  { value: "rfp", label: "RFP", region: "both" },
  { value: "rfq", label: "RFQ", region: "both" },
  { value: "combined", label: "Combined synopsis/solicitation", region: "US" },
  { value: "grant", label: "Grant", region: "both" },
  { value: "forecast", label: "Forecast", region: "both" },
  { value: "award", label: "Award (recompete)", region: "both" },
  { value: "eoi", label: "EOI", region: "IN" },
  { value: "gem_bid", label: "GeM bid", region: "IN" },
  { value: "reverse_auction", label: "Reverse auction", region: "IN" },
  { value: "corrigendum", label: "Corrigendum", region: "IN" },
  { value: "special", label: "Special notice", region: "both" },
];

export const CONTRACT_TYPES: readonly RegionOption[] = [
  { value: "ffp", label: "Firm fixed price", region: "both" },
  { value: "tm", label: "Time & materials", region: "both" },
  { value: "cost_plus", label: "Cost-plus", region: "US" },
  { value: "idiq_task_order", label: "IDIQ task order", region: "US" },
  { value: "rate_contract", label: "Rate contract", region: "IN" },
];

export const TEAMING_ROLES: readonly Option[] = [
  { value: "prime", label: "Prime" },
  { value: "sub", label: "Subcontractor" },
  { value: "jv", label: "Joint venture" },
];

export const AGENCY_TYPES: readonly RegionOption[] = [
  { value: "federal", label: "Federal", region: "US" },
  { value: "state", label: "State", region: "US" },
  { value: "local", label: "Local", region: "US" },
  { value: "central_ministry", label: "Central ministry", region: "IN" },
  { value: "state_government", label: "State government", region: "IN" },
  { value: "psu", label: "PSU", region: "IN" },
  { value: "commercial", label: "Commercial", region: "both" },
  { value: "international", label: "International", region: "both" },
  { value: "other", label: "Other", region: "both" },
];

export const PERFORMANCE_ROLES: readonly Option[] = [
  { value: "prime", label: "Prime" },
  { value: "sub", label: "Subcontractor" },
];

export const CPARS_RATINGS: readonly Option[] = [
  { value: "exceptional", label: "Exceptional" },
  { value: "very_good", label: "Very good" },
  { value: "satisfactory", label: "Satisfactory" },
  { value: "marginal", label: "Marginal" },
  { value: "unsatisfactory", label: "Unsatisfactory" },
  { value: "not_rated", label: "Not rated" },
];

/** Socio-economic (4.2, US) and security/quality (4.5) certification kinds. */
export const CERTIFICATION_KINDS: readonly (RegionOption & { family: "socio_economic" | "security" })[] = [
  { value: "8a", label: "8(a)", region: "US", family: "socio_economic" },
  { value: "hubzone", label: "HUBZone", region: "US", family: "socio_economic" },
  { value: "wosb", label: "WOSB", region: "US", family: "socio_economic" },
  { value: "edwosb", label: "EDWOSB", region: "US", family: "socio_economic" },
  { value: "sdvosb", label: "SDVOSB", region: "US", family: "socio_economic" },
  { value: "vosb", label: "VOSB", region: "US", family: "socio_economic" },
  { value: "sdb", label: "SDB", region: "US", family: "socio_economic" },
  { value: "fcl", label: "Facility clearance (FCL)", region: "US", family: "security" },
  { value: "cmmc", label: "CMMC", region: "US", family: "security" },
  { value: "fedramp", label: "FedRAMP", region: "US", family: "security" },
  { value: "soc2", label: "SOC 2", region: "both", family: "security" },
  { value: "iso_27001", label: "ISO 27001", region: "both", family: "security" },
  { value: "iso_9001", label: "ISO 9001", region: "both", family: "security" },
  { value: "iso_20000", label: "ISO 20000", region: "both", family: "security" },
  { value: "cmmi", label: "CMMI", region: "both", family: "security" },
  { value: "stqc", label: "STQC empanelment", region: "IN", family: "security" },
  { value: "cert_in", label: "CERT-In empanelment", region: "IN", family: "security" },
];

export const REGISTRATION_KINDS: readonly RegionOption[] = [
  { value: "sam", label: "SAM.gov", region: "US" },
  { value: "dsc", label: "Class 3 DSC", region: "IN" },
  { value: "gem", label: "GeM", region: "IN" },
  { value: "cppp", label: "CPPP", region: "IN" },
  { value: "state_portal", label: "State portal", region: "IN" },
];

export const INSURANCE_KINDS: readonly Option[] = [
  { value: "general_liability", label: "General liability" },
  { value: "professional_liability", label: "Professional liability" },
  { value: "cyber", label: "Cyber" },
  { value: "workers_comp", label: "Workers' compensation" },
  { value: "auto", label: "Auto" },
  { value: "umbrella", label: "Umbrella" },
  { value: "other", label: "Other" },
];

export const BOILERPLATE_KINDS: readonly Option[] = [
  { value: "company_overview", label: "Company overview" },
  { value: "management_approach", label: "Management approach" },
  { value: "qa_plan", label: "QA plan" },
  { value: "transition_plan", label: "Transition plan" },
  { value: "security_approach", label: "Security approach" },
  { value: "diversity", label: "Diversity" },
  { value: "sustainability", label: "Sustainability" },
  { value: "other", label: "Other" },
];

export const PROFILE_FILE_KINDS: readonly Option[] = [
  { value: "capability_statement", label: "Capability statement" },
  { value: "brochure", label: "Brochure" },
  { value: "case_study", label: "Case study" },
  { value: "past_proposal", label: "Past proposal" },
  { value: "brand", label: "Brand asset" },
  { value: "template", label: "Proposal template" },
];

export const RATE_UNITS: readonly RegionOption[] = [
  { value: "hour", label: "Per hour", region: "both" },
  { value: "day", label: "Per day", region: "both" },
  { value: "month", label: "Per month (man-month)", region: "both" },
];

export const APPROVER_ROLES: readonly Option[] = [
  { value: "tenant_owner", label: "Owner" },
  { value: "bid_manager", label: "Bid manager" },
  { value: "writer", label: "Writer" },
  { value: "reviewer", label: "Reviewer" },
];

export const OUTPUT_LANGUAGES: readonly RegionOption[] = [
  { value: "en", label: "English", region: "both" },
  { value: "hi", label: "Hindi summaries", region: "IN" },
];

export const SCORING_WEIGHT_KEYS: readonly Option[] = [
  { value: "code_match", label: "Code match" },
  { value: "semantic_similarity", label: "Semantic similarity" },
  { value: "keyword_match", label: "Keyword match" },
  { value: "eligibility", label: "Eligibility" },
  { value: "value_fit", label: "Value fit" },
  { value: "geography", label: "Geography" },
  { value: "buyer_affinity", label: "Buyer affinity" },
  { value: "past_performance_relevance", label: "Past-performance relevance" },
];

export const BID_NO_BID_WEIGHT_KEYS: readonly Option[] = [
  { value: "fit", label: "Fit" },
  { value: "eligibility", label: "Eligibility" },
  { value: "capacity", label: "Capacity" },
  { value: "competition", label: "Competition" },
  { value: "value", label: "Value" },
  { value: "win_probability", label: "Win probability" },
];

export const NOTIFICATION_EVENTS: readonly Option[] = [
  { value: "high_fit_match", label: "High-fit match" },
  { value: "digest", label: "Daily digest" },
  { value: "amendment", label: "Amendment" },
  { value: "deadline_reminder", label: "Deadline reminder" },
  { value: "pursuit_update", label: "Pursuit update" },
  { value: "agent_question", label: "Agent question" },
  { value: "approval_request", label: "Approval request" },
  { value: "registration_expiry", label: "Registration expiry" },
];

export const NOTIFICATION_CHANNELS: readonly Option[] = [
  { value: "email", label: "Email" },
  { value: "slack", label: "Slack" },
  { value: "teams", label: "Teams" },
  { value: "whatsapp", label: "WhatsApp" },
  { value: "web_push", label: "Web push" },
];

export const CURRENCY_BY_REGION: Record<Region, "USD" | "INR"> = { US: "USD", IN: "INR" };

// ---------------------------------------------------------------------------
// Field table (SPEC 4.1 – 4.6)
// ---------------------------------------------------------------------------

export const PROFILE_FIELDS: readonly FieldMeta[] = [
  // 4.1 Identity
  { key: "legal_name", label: "Legal name", step: 1, region: "both", kind: "text", required: true, group: "Identity" },
  { key: "dba_names", label: "DBA / trade names", step: 1, region: "both", kind: "tags", group: "Identity" },
  { key: "addresses", label: "Addresses", step: 1, region: "both", kind: "list", required: true, group: "Identity" },
  { key: "website", label: "Website", step: 1, region: "both", kind: "url", required: true, group: "Identity" },
  { key: "phone", label: "Main phone", step: 1, region: "both", kind: "tel", required: true, group: "Identity" },
  { key: "bid_inbox_email", label: "Bid-inbox email", step: 1, region: "both", kind: "email", required: true, group: "Identity" },
  { key: "year_founded", label: "Year founded", step: 1, region: "both", kind: "number", required: true, group: "Identity" },
  { key: "legal_structure", label: "Legal structure", step: 1, region: "both", kind: "enum", required: true, group: "Identity" },
  // 4.1 US registrations
  { key: "uei", label: "UEI (SAM Unique Entity ID)", step: 1, region: "US", kind: "text", required: "US", group: "US registrations", help: "12 characters; validated against the SAM.gov Entity API." },
  { key: "cage_code", label: "CAGE code", step: 1, region: "US", kind: "text", group: "US registrations" },
  { key: "sam_status", label: "SAM registration status", step: 1, region: "US", kind: "enum", required: "US", group: "US registrations" },
  { key: "sam_expires_on", label: "SAM expiry date", step: 1, region: "US", kind: "date", required: "US", group: "US registrations", help: "Renewal reminders at 60/30/7 days." },
  { key: "ein", label: "EIN", step: 1, region: "US", kind: "masked", sensitive: true, group: "US registrations" },
  { key: "vehicles", label: "Contract vehicles held (GSA MAS, GWACs, IDIQs, BPAs)", step: 1, region: "US", kind: "list", group: "US registrations" },
  // 4.1 IN registrations
  { key: "pan", label: "PAN", step: 1, region: "IN", kind: "masked", required: "IN", sensitive: true, group: "India registrations" },
  { key: "gstin", label: "GSTIN", step: 1, region: "IN", kind: "masked", required: "IN", sensitive: true, group: "India registrations" },
  { key: "cin_llpin", label: "CIN / LLPIN", step: 1, region: "IN", kind: "text", required: "IN", group: "India registrations" },
  { key: "tan", label: "TAN", step: 1, region: "IN", kind: "masked", required: "IN", sensitive: true, group: "India registrations" },
  { key: "udyam_number", label: "Udyam (MSME) registration no.", step: 1, region: "IN", kind: "text", group: "India registrations" },
  { key: "udyam_category", label: "Udyam category", step: 1, region: "IN", kind: "enum", group: "India registrations" },
  { key: "dpiit_number", label: "DPIIT Startup India recognition no.", step: 1, region: "IN", kind: "text", group: "India registrations" },
  { key: "gem_seller_id", label: "GeM seller ID", step: 1, region: "IN", kind: "text", group: "India registrations" },
  { key: "registrations", label: "Portal enrolments and DSC", step: 1, region: "IN", kind: "list", group: "India registrations", help: "CPPP / state portal enrolment IDs, GeM, Class 3 DSC holder and expiry. No passwords." },
  { key: "local_supplier_class", label: "Local supplier status (Make in India)", step: 1, region: "IN", kind: "enum", group: "India registrations" },
  { key: "local_content_pct", label: "Local content %", step: 1, region: "IN", kind: "percent", group: "India registrations" },
  // Banking (encrypted, both)
  { key: "bank_name", label: "Bank name", step: 1, region: "both", kind: "text", group: "Banking (optional)" },
  { key: "bank_account_number", label: "Bank account number", step: 1, region: "both", kind: "masked", sensitive: true, group: "Banking (optional)" },
  { key: "bank_routing_code", label: "Routing / IFSC code", step: 1, region: "both", kind: "masked", sensitive: true, group: "Banking (optional)" },

  // 4.2 Size and finances
  { key: "employee_count_total", label: "Employees (total)", step: 2, region: "both", kind: "number", group: "Size" },
  { key: "employees_by_country", label: "Employees by country", step: 2, region: "both", kind: "list", group: "Size" },
  { key: "annual_revenue", label: "Annual revenue (last 3 fiscal years)", step: 2, region: "both", kind: "list", group: "Finances" },
  { key: "audited_fiscal_years", label: "Audited financials available for FYs", step: 2, region: "both", kind: "tags", group: "Finances" },
  { key: "bonding_capacity", label: "Bonding capacity / bank guarantee limit", step: 2, region: "both", kind: "money", group: "Finances" },
  { key: "net_worth", label: "Net worth", step: 2, region: "IN", kind: "money", group: "Finances" },
  { key: "solvency_certificate_available", label: "Solvency certificate available", step: 2, region: "IN", kind: "boolean", group: "Finances" },
  { key: "size_status_by_naics", label: "Small-business status per NAICS", step: 2, region: "US", kind: "list", group: "Status", help: "Computed from revenue and employees against the SBA size standards." },
  { key: "certifications.socio_economic", label: "Socio-economic certifications", step: 2, region: "US", kind: "list", group: "Status" },
  { key: "mse_ownership", label: "SC/ST-owned or women-owned MSE", step: 2, region: "IN", kind: "enum", group: "Status" },

  // 4.3 What we sell
  { key: "codes.naics", label: "NAICS codes", step: 3, region: "US", kind: "list", group: "Codes" },
  { key: "codes.psc", label: "PSC codes", step: 3, region: "US", kind: "list", group: "Codes" },
  { key: "codes.aln", label: "ALN / CFDA programs", step: 3, region: "US", kind: "list", group: "Codes" },
  { key: "codes.gem", label: "GeM categories", step: 3, region: "IN", kind: "list", group: "Codes" },
  { key: "codes.india_category", label: "Indian product categories", step: 3, region: "IN", kind: "list", group: "Codes" },
  { key: "keywords", label: "Capability and exclusion keywords", step: 3, region: "both", kind: "list", group: "Keywords" },
  { key: "service_lines", label: "Service lines", step: 3, region: "both", kind: "list", group: "Service lines" },

  // 4.4 Where and how big
  { key: "target_countries", label: "Target countries", step: 4, region: "both", kind: "tags", group: "Geography" },
  { key: "target_us_states", label: "Target US states", step: 4, region: "US", kind: "tags", group: "Geography" },
  { key: "target_in_states", label: "Target Indian states / UTs", step: 4, region: "IN", kind: "tags", group: "Geography" },
  { key: "target_cities", label: "Target cities", step: 4, region: "both", kind: "tags", group: "Geography" },
  { key: "remote_ok", label: "Remote delivery OK", step: 4, region: "both", kind: "boolean", group: "Geography" },
  { key: "target_buyers", label: "Target agencies / ministries / PSUs", step: 4, region: "both", kind: "tags", group: "Buyers" },
  { key: "blocked_buyers", label: "Blocked buyers", step: 4, region: "both", kind: "tags", group: "Buyers" },
  { key: "value_range_usd", label: "Contract value range (USD)", step: 4, region: "US", kind: "money", group: "Value" },
  { key: "value_range_inr", label: "Contract value range (INR)", step: 4, region: "IN", kind: "money", group: "Value" },
  { key: "notice_types_wanted", label: "Notice types wanted", step: 4, region: "both", kind: "multi-enum", group: "Types" },
  { key: "contract_types_preferred", label: "Contract types preferred", step: 4, region: "both", kind: "multi-enum", group: "Types" },
  { key: "teaming_roles", label: "Willing to prime / sub / JV", step: 4, region: "both", kind: "multi-enum", group: "Teaming" },
  { key: "teaming_partners", label: "Known teaming partners", step: 4, region: "both", kind: "list", group: "Teaming" },

  // 4.5 Proof
  { key: "past_performance", label: "Past performance", step: 5, region: "both", kind: "list", group: "Past performance" },
  { key: "personnel", label: "Key personnel", step: 5, region: "both", kind: "list", group: "People" },
  { key: "rate_card", label: "Labor categories and rate card", step: 5, region: "both", kind: "list", group: "People" },
  { key: "certifications.security", label: "Security and quality certifications", step: 5, region: "both", kind: "list", group: "Security" },
  { key: "cleared_personnel_count", label: "Cleared personnel count", step: 5, region: "US", kind: "number", group: "Security" },
  { key: "insurance", label: "Insurance", step: 5, region: "both", kind: "list", group: "Insurance" },
  { key: "boilerplate", label: "Boilerplate library", step: 5, region: "both", kind: "list", group: "Boilerplate" },
  { key: "files", label: "Files (capability statement, brochures, past proposals, brand)", step: 5, region: "both", kind: "list", group: "Files" },

  // 4.6 Preferences
  { key: "notification_prefs", label: "Alert channels, quiet hours and digest", step: 6, region: "both", kind: "list", group: "Alerts" },
  { key: "scoring_weights", label: "Fit-score weights", step: 6, region: "both", kind: "list", group: "Weights" },
  { key: "bid_no_bid_weights", label: "Bid / no-bid weights", step: 6, region: "both", kind: "list", group: "Weights" },
  { key: "required_approver_roles", label: "Required approvers", step: 6, region: "both", kind: "multi-enum", group: "Approvals" },
  { key: "output_languages", label: "Output languages", step: 6, region: "both", kind: "multi-enum", group: "Languages" },
];

const FIELD_INDEX: ReadonlyMap<string, FieldMeta> = new Map(PROFILE_FIELDS.map((f) => [f.key, f]));

/** Fields visible for a region, optionally limited to one step. */
export function fieldsForRegion(region: Region, step?: StepId): FieldMeta[] {
  return PROFILE_FIELDS.filter(
    (f) => isAllowedInRegion(f.region, region) && (step === undefined || f.step === step),
  );
}

export function fieldMeta(key: string): FieldMeta | undefined {
  return FIELD_INDEX.get(key);
}

export function fieldLabel(key: string): string {
  return FIELD_INDEX.get(key)?.label ?? key.replace(/_/g, " ");
}

/**
 * Whether a field may be rendered or sent for a region. Unknown keys are
 * allowed (they are not region-gated); known keys follow the table.
 */
export function isFieldAllowed(key: string, region: Region): boolean {
  const meta = FIELD_INDEX.get(key);
  return meta ? isAllowedInRegion(meta.region, region) : true;
}

export function isFieldRequired(key: string, region: Region): boolean {
  const req = FIELD_INDEX.get(key)?.required;
  return req === true || req === region;
}

/** Profile columns that must never be sent for the other region. */
export const US_ONLY_COLUMNS: readonly string[] = [
  "uei",
  "cage_code",
  "sam_status",
  "sam_expires_on",
  "ein",
  "cleared_personnel_count",
];
export const IN_ONLY_COLUMNS: readonly string[] = [
  "pan",
  "gstin",
  "tan",
  "cin_llpin",
  "udyam_number",
  "udyam_category",
  "dpiit_number",
  "gem_seller_id",
  "local_supplier_class",
  "local_content_pct",
  "net_worth_amount",
  "net_worth_currency",
  "solvency_certificate_available",
  "mse_ownership",
];

/** Drops region-foreign profile columns from a payload (returns a copy). */
export function stripRegionForeign<T extends Record<string, unknown>>(payload: T, region: Region): T {
  const foreign = new Set(region === "US" ? IN_ONLY_COLUMNS : US_ONLY_COLUMNS);
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(payload)) {
    if (!foreign.has(k)) out[k] = v;
  }
  return out as T;
}

/** Mask marker used by the backend for encrypted identifiers. */
export const MASK_PREFIX = "•••••";
export const isMaskedValue = (value: unknown): boolean =>
  typeof value === "string" && value.startsWith(MASK_PREFIX);
