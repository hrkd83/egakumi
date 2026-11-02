# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM dependencies AS build
ENV NEXT_TELEMETRY_DISABLED=1
COPY . .
RUN npm run build

# マイグレーションCLIも同じイメージで動かすため、実行時依存を含めます。
FROM dependencies AS production-dependencies
RUN npm prune --omit=dev --ignore-scripts --no-audit --no-fund

FROM node:24-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=production-dependencies --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
COPY --from=build --chown=node:node /app/scripts/migrate-auth.ts /app/scripts/auth-migrations.ts /app/scripts/auth-runtime-grants.ts /app/scripts/api-key-group-config.ts /app/scripts/api-key-administration-config.ts ./scripts/
COPY --from=build --chown=node:node /app/scripts/reconcile-api-key-groups.ts ./scripts/
COPY --from=build --chown=node:node /app/scripts/api-key-subscription-import.ts /app/scripts/bootstrap-api-key-subscriptions.ts ./scripts/
COPY --from=build --chown=node:node /app/lib ./lib
COPY --from=build --chown=node:node /app/scripts/serve-portal.ts ./scripts/
COPY --from=build --chown=node:node /app/sql ./sql
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "--conditions=react-server", "--experimental-strip-types", "scripts/serve-portal.ts"]
