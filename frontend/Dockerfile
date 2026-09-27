# BidRadar front end (SPEC 10.1: Next.js 15 App Router) for Cloud Run.
#
#   docker build -t bidradar-frontend frontend
#
# Three stages so the runtime image carries only the standalone server bundle:
# deps (pnpm install), builder (next build -> .next/standalone), runtime (node, non-root).

# ---------------------------------------------------------------- deps
FROM node:22-alpine AS deps
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,target=/pnpm-store \
    pnpm config set store-dir /pnpm-store && pnpm install --frozen-lockfile

# ---------------------------------------------------------------- builder
FROM node:22-alpine AS builder
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* are inlined at build time; the deploy workflow passes the env's API URL.
ARG NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
RUN pnpm build

# ---------------------------------------------------------------- runtime
FROM node:22-alpine AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app

RUN addgroup --system --gid 10001 bidradar \
 && adduser --system --uid 10001 --ingroup bidradar bidradar

# `standalone` already contains the pruned node_modules and server.js; .next/static and
# public/ are the only things it does not copy for us. (public/ is empty today, so it is
# not copied — add it here the day the app ships static assets.)
COPY --from=builder --chown=10001:10001 /app/.next/standalone ./
COPY --from=builder --chown=10001:10001 /app/.next/static ./.next/static

USER 10001:10001
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
