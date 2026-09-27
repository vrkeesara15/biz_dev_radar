import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Cloud Run image (M7-01): `standalone` emits .next/standalone/server.js with only the
  // node_modules the app actually imports, so the runtime stage needs no pnpm install.
  output: "standalone",
  // The image runs behind Cloud Run's proxy; do not leak the build machine's path.
  poweredByHeader: false,
};

export default nextConfig;
