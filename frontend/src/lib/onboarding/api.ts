/**
 * Thin, typed wrappers over the profile API for the onboarding wizard.
 * All calls go through `browserApi` (same-origin proxy) and throw `ApiError`
 * on non-2xx so forms can surface region mismatches and validation messages.
 */
import {
  asRegionMismatch,
  browserApi,
  errorMessage,
  type Profile,
  type ProfileCreate,
  type ProfileUpdate,
  type RegionMismatch,
  type Schemas,
} from "@/lib/api/browser";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  readonly regionMismatch: RegionMismatch | null;

  constructor(status: number, body: unknown) {
    const mismatch = asRegionMismatch(body);
    super(
      mismatch
        ? `Fields not available for region ${mismatch.region.toUpperCase()}: ${mismatch.fields.join(", ")}`
        : errorMessage(body, `Request failed (${status})`),
    );
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.regionMismatch = mismatch;
  }
}

type Result<T> = { data?: T; error?: unknown; response: Response };

async function unwrap<T>(promise: Promise<Result<T>>): Promise<T> {
  const { data, error, response } = await promise;
  if (!response.ok) throw new ApiError(response.status, error);
  return data as T;
}

// --- profile -----------------------------------------------------------------

export const listProfiles = () => unwrap(browserApi.GET("/api/v1/profiles"));

export const getProfile = (profileId: string) =>
  unwrap(browserApi.GET("/api/v1/profiles/{profile_id}", { params: { path: { profile_id: profileId } } }));

export const createProfile = (body: ProfileCreate) =>
  unwrap(browserApi.POST("/api/v1/profiles", { body }));

export const updateProfile = (profileId: string, body: ProfileUpdate) =>
  unwrap(
    browserApi.PUT("/api/v1/profiles/{profile_id}", {
      params: { path: { profile_id: profileId } },
      body,
    }),
  );

// --- sub-resources -----------------------------------------------------------

export type ResourceMap = {
  codes: { in: Schemas["CodeIn"]; out: Schemas["CodeOut"]; update: Schemas["CodeUpdate"] };
  keywords: { in: Schemas["KeywordIn"]; out: Schemas["KeywordOut"]; update: Schemas["KeywordUpdate"] };
  "service-lines": { in: Schemas["ServiceLineIn"]; out: Schemas["ServiceLineOut"]; update: Schemas["ServiceLineUpdate"] };
  certifications: { in: Schemas["CertificationIn"]; out: Schemas["CertificationOut"]; update: Schemas["CertificationUpdate"] };
  "teaming-partners": { in: Schemas["TeamingPartnerIn"]; out: Schemas["TeamingPartnerOut"]; update: Schemas["TeamingPartnerUpdate"] };
  "past-performance": { in: Schemas["PastPerformanceIn"]; out: Schemas["PastPerformanceOut"]; update: Schemas["PastPerformanceUpdate"] };
  personnel: { in: Schemas["PersonnelIn"]; out: Schemas["PersonnelOut"]; update: Schemas["PersonnelUpdate"] };
  registrations: { in: Schemas["RegistrationIn"]; out: Schemas["RegistrationOut"]; update: Schemas["RegistrationUpdate"] };
  vehicles: { in: Schemas["VehicleIn"]; out: Schemas["VehicleOut"]; update: Schemas["VehicleUpdate"] };
  insurance: { in: Schemas["InsuranceIn"]; out: Schemas["InsuranceOut"]; update: Schemas["InsuranceUpdate"] };
  boilerplate: { in: Schemas["BoilerplateIn"]; out: Schemas["BoilerplateOut"]; update: Schemas["BoilerplateUpdate"] };
  files: { in: Schemas["ProfileFileIn"]; out: Schemas["ProfileFileOut"]; update: Schemas["ProfileFileUpdate"] };
  "rate-card": { in: Schemas["RateCardIn"]; out: Schemas["RateCardOut"]; update: Schemas["RateCardUpdate"] };
};
export type Resource = keyof ResourceMap;
export type ItemIn<R extends Resource> = ResourceMap[R]["in"];
export type ItemOut<R extends Resource> = ResourceMap[R]["out"];
export type ItemUpdate<R extends Resource> = ResourceMap[R]["update"];

export const RESOURCES: readonly Resource[] = [
  "codes",
  "keywords",
  "service-lines",
  "certifications",
  "teaming-partners",
  "past-performance",
  "personnel",
  "registrations",
  "vehicles",
  "insurance",
  "boilerplate",
  "files",
  "rate-card",
];

// The generated client is keyed by literal paths; the collection routes share
// one shape, so they are addressed through the `codes` literal and cast.
type CollectionPath = "/api/v1/profiles/{profile_id}/codes";
type ItemPath = "/api/v1/profiles/{profile_id}/codes/{item_id}";
const collectionPath = (r: Resource) => `/api/v1/profiles/{profile_id}/${r}` as CollectionPath;
const itemPath = (r: Resource) => `/api/v1/profiles/{profile_id}/${r}/{item_id}` as ItemPath;

export function listItems<R extends Resource>(profileId: string, resource: R): Promise<ItemOut<R>[]> {
  return unwrap(
    browserApi.GET(collectionPath(resource), { params: { path: { profile_id: profileId } } }),
  ) as Promise<ItemOut<R>[]>;
}

export function createItem<R extends Resource>(
  profileId: string,
  resource: R,
  body: ItemIn<R>,
): Promise<ItemOut<R>> {
  return unwrap(
    browserApi.POST(collectionPath(resource), {
      params: { path: { profile_id: profileId } },
      body: body as unknown as Schemas["CodeIn"],
    }),
  ) as Promise<ItemOut<R>>;
}

export function updateItem<R extends Resource>(
  profileId: string,
  resource: R,
  itemId: string,
  body: ItemUpdate<R>,
): Promise<ItemOut<R>> {
  return unwrap(
    browserApi.PUT(itemPath(resource), {
      params: { path: { profile_id: profileId, item_id: itemId } },
      body: body as unknown as Schemas["CodeUpdate"],
    }),
  ) as Promise<ItemOut<R>>;
}

export async function deleteItem(profileId: string, resource: Resource, itemId: string): Promise<void> {
  const { response, error } = await browserApi.DELETE(itemPath(resource), {
    params: { path: { profile_id: profileId, item_id: itemId } },
  });
  if (!response.ok) throw new ApiError(response.status, error);
}

// --- files & prefs -----------------------------------------------------------

export type UploadedFile = Schemas["FileOut"];

export async function uploadFile(file: File): Promise<UploadedFile> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch("/api/v1/files", { method: "POST", body: form });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, body);
  return body as UploadedFile;
}

export type NotificationPrefs = Schemas["NotificationPrefsOut"];
export type NotificationPrefsIn = Schemas["NotificationPrefsIn"];

export const getNotificationPrefs = () => unwrap(browserApi.GET("/api/v1/me/notification-prefs"));
export const putNotificationPrefs = (body: NotificationPrefsIn) =>
  unwrap(browserApi.PUT("/api/v1/me/notification-prefs", { body }));

// --- collection sync ---------------------------------------------------------

export type Draft<R extends Resource> = ItemIn<R> & { id?: string };

const stable = (v: unknown) => JSON.stringify(v ?? null);

/**
 * Reconciles an edited list against the server copy: rows without an id are
 * POSTed, rows that disappeared are DELETEd, rows whose payload fields changed
 * are PUT. Returns the fresh server rows in the edited order.
 */
export async function syncCollection<R extends Resource>(
  profileId: string,
  resource: R,
  previous: readonly ItemOut<R>[],
  next: readonly Draft<R>[],
): Promise<ItemOut<R>[]> {
  const prevById = new Map(previous.map((p) => [p.id, p]));
  const keep = new Set(next.map((n) => n.id).filter((id): id is string => Boolean(id)));

  for (const row of previous) {
    if (!keep.has(row.id)) await deleteItem(profileId, resource, row.id);
  }

  const result: ItemOut<R>[] = [];
  for (const row of next) {
    const { id, ...payload } = row;
    if (!id || !prevById.has(id)) {
      result.push(await createItem(profileId, resource, payload as ItemIn<R>));
      continue;
    }
    const before = prevById.get(id) as Record<string, unknown>;
    const changed = Object.entries(payload).some(([k, v]) => stable(v) !== stable(before[k]));
    result.push(
      changed
        ? await updateItem(profileId, resource, id, payload as unknown as ItemUpdate<R>)
        : (before as ItemOut<R>),
    );
  }
  return result;
}

export type { Profile, ProfileCreate, ProfileUpdate };
