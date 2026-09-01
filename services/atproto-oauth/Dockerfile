FROM node:22-bookworm-slim

ENV NODE_ENV=production \
    FEED_PASSPORT_ATPROTO_HOST=0.0.0.0 \
    FEED_PASSPORT_ATPROTO_PORT=4310 \
    FEED_PASSPORT_ATPROTO_STORE_PATH=/data/atproto-sidecar.enc

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && npm cache clean --force

COPY --chown=node:node src ./src
RUN mkdir -p /data && chown node:node /data

USER node
VOLUME ["/data"]
EXPOSE 4310

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:4310/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]

CMD ["node", "src/server.mjs"]
