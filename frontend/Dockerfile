# Production image for the Janasunani demo frontend (Next.js 16, App Router).
# Build context is `frontend/` (see .github/workflows/deploy.yml). Multi-stage:
# `build` runs `next build` with `output: "standalone"` (next.config.ts) so the
# runtime stage ships only the traced server + deps, not the full
# `node_modules` tree — see node_modules/next/dist/docs/.../output.md.
#
# NEXT_PUBLIC_API_URL is baked in at build time (Next inlines NEXT_PUBLIC_*
# into the client bundle) and defaults to the relative `/api`, which the
# Caddy proxy (deploy/proxy/Caddyfile) rewrites to the api service — so the
# browser never sees a backend host/IP and there's no CORS to configure.
#
# node:22-alpine is pinned to a digest, not just the floating tag, so a
# build is fully reproducible — bump by re-resolving:
# `docker buildx imagetools inspect node:22-alpine`.
FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

FROM node:22-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/public ./public
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
