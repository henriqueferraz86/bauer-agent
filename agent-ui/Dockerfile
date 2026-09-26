# Agent UI oficial do Agno, empacotado para o stack do Bauer.
# A revisão pode ser uma tag, branch ou commit. Fixe um commit em produção.
FROM node:22-alpine AS source

RUN apk add --no-cache git
ARG AGENT_UI_REF=main
RUN git init /src/agent-ui \
    && cd /src/agent-ui \
    && git remote add origin https://github.com/agno-agi/agent-ui.git \
    && git fetch --depth=1 origin "${AGENT_UI_REF}" \
    && git checkout --detach FETCH_HEAD

FROM node:22-alpine AS dependencies

WORKDIR /app
RUN corepack enable
COPY --from=source /src/agent-ui/package.json /src/agent-ui/pnpm-lock.yaml ./
# O upstream ainda declara o override no campo `pnpm` do package.json. Pnpm
# 11/12 passou a ler overrides de pnpm-workspace.yaml; manter a mesma regra
# aqui permite instalação congelada sem editar o código oficial clonado.
RUN printf 'overrides:\n  picomatch: ">=2.3.2"\nallowBuilds:\n  sharp: true\nonlyBuiltDependencies:\n  - sharp\n' > pnpm-workspace.yaml
RUN pnpm install --frozen-lockfile

FROM node:22-alpine AS builder

WORKDIR /app
RUN corepack enable
COPY --from=source /src/agent-ui ./
COPY --from=dependencies /app/node_modules ./node_modules
COPY --from=dependencies /app/pnpm-workspace.yaml ./pnpm-workspace.yaml

# A UI oficial guarda o endpoint inicial no store do navegador. Aplicar o
# endpoint no build deixa a instalação remota funcional sem alterar o código
# do upstream; o usuário ainda pode trocá-lo pela própria UI.
ARG AGENT_UI_ENDPOINT=http://localhost:7777
ENV AGENT_UI_ENDPOINT=${AGENT_UI_ENDPOINT}
COPY patch-endpoint.mjs /usr/local/bin/patch-endpoint.mjs
RUN node /usr/local/bin/patch-endpoint.mjs /app "$AGENT_UI_ENDPOINT"
COPY customize-model-catalog.mjs /usr/local/bin/customize-model-catalog.mjs
RUN node /usr/local/bin/customize-model-catalog.mjs /app
RUN mkdir -p public
RUN pnpm build

FROM node:22-alpine AS runtime

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
RUN corepack enable
COPY --from=builder /app/package.json /app/pnpm-lock.yaml ./
COPY --from=builder /app/pnpm-workspace.yaml ./pnpm-workspace.yaml
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["pnpm", "start"]
