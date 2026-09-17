FROM node:24-bookworm-slim AS node-runtime

FROM python:3.14-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        findutils \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && git --version \
    && ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && python3 -m pip install --no-cache-dir pytest==9.1.1

WORKDIR /workspace

CMD ["sleep", "infinity"]
