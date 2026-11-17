ARG BASE_IMAGE=node:22-bookworm-slim
FROM ${BASE_IMAGE}
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium-headless-shell python3 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY server.mjs /opt/ninebuddy/server.mjs
ENV TMPDIR=/tmp
USER 65534:65534
ENTRYPOINT ["node", "/opt/ninebuddy/server.mjs"]
