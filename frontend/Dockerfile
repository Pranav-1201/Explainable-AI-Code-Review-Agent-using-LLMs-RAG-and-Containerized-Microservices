# ==========================================================
# Phase F — the web tier: build the React bundle, then serve it from Caddy
# alongside a reverse proxy to the API. One image, two stages.
#
# Build context is the REPO ROOT (same as backend/Dockerfile), because the
# Caddyfile lives at the root next to docker-compose.yml. The root
# .dockerignore already excludes frontend/node_modules and frontend/dist.
# ==========================================================

# Node 20 to match .github/workflows/ci.yml, which is the only version this
# frontend has evidence of building under. The dev machine has 22; that is not
# a reason to build the shipped artifact with it.
# $BUILDPLATFORM, not the target: the bundle is plain JavaScript and identical
# on every architecture, so npm and Vite run natively on the build machine even
# while release.yml produces the arm64 image under QEMU. Only the caddy stage
# below is built per platform.
FROM --platform=$BUILDPLATFORM node:20-alpine AS build

WORKDIR /app

# Dependencies first so the install layer survives code-only rebuilds.
# `npm ci` (not install) installs exactly the lockfile and fails if
# package.json and package-lock.json have drifted apart.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# Vite INLINES import.meta.env.VITE_* at build time, so both of these are
# baked into the shipped JavaScript and travel with the image.
#
# VITE_API_BASE=/api is the whole point of the single-origin design: the
# bundle issues relative requests, so the same image works under any hostname
# with no rebuild.
ARG VITE_API_BASE=/api
ENV VITE_API_BASE=$VITE_API_BASE

# VITE_API_KEY is deliberately NOT a build argument (D36). A published image
# is a public artifact, so a key baked into one is a published key. The
# browser gets the key at runtime from /config.js instead, which Caddy renders
# from the web container's API_KEY. api.ts still reads VITE_API_KEY as a
# local-development fallback, where no image is involved.

# The public origin, e.g. https://example.com. Unlike VITE_API_KEY this IS safe
# to bake into a published image: it is the address the site answers on, which
# is public by definition. Empty until a domain exists, and everything that
# consumes it (canonical links, og:url, sitemap.xml, the robots.txt pointer)
# omits itself rather than emitting a placeholder origin.
#
# vite.config.ts reads this from process.env, which is why it is an ENV and not
# only an ARG.
ARG VITE_SITE_URL=
ENV VITE_SITE_URL=$VITE_SITE_URL

# Google Search Console verification token. A DNS TXT record is the better
# option once a domain exists, because it survives a rebuild; this is here for
# the case where DNS is not available to whoever is verifying.
ARG VITE_SEARCH_CONSOLE_TOKEN=
ENV VITE_SEARCH_CONSOLE_TOKEN=$VITE_SEARCH_CONSOLE_TOKEN

RUN npm run build


FROM caddy:2-alpine

COPY --from=build /app/dist /srv
# The bundle's key-less public/config.js is replaced by the template Caddy
# renders per request (Caddyfile, handle /config.js). Copied AFTER the bundle
# so it wins.
COPY config.js.template /srv/config.js
COPY Caddyfile /etc/caddy/Caddyfile

EXPOSE 80 443
