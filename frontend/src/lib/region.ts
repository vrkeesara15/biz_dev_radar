export type Region = "US" | "IN";

/** Deployment region shown in the top bar. Defaults to US. */
export function getRegion(env: NodeJS.ProcessEnv = process.env): Region {
  return env.NEXT_PUBLIC_REGION?.toUpperCase() === "IN" ? "IN" : "US";
}
