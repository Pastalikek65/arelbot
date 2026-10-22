FROM node:22-alpine@sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914
WORKDIR /app
# Explicit allowlist: no account file, dotenv file or second Telegram poller.
COPY vendor/d2api/server.js vendor/d2api/package.json vendor/d2api/arel-start.js ./
COPY vendor/d2api/lib ./lib
RUN mkdir -p /app/assets /data && chown node:node /data
ENV NODE_ENV=production HOST=0.0.0.0 PORT=9655 D2API_DATA_DIR=/data LOG_REQUESTS=0 DELETE_SESSIONS=1
USER node
CMD ["node", "arel-start.js"]
