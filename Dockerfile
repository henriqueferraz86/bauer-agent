# Estágio 1: pega o binário do Ollama da imagem oficial (evita download durante build)
FROM ollama/ollama:latest AS ollama-bin

# Estágio 2: imagem final leve com Python + binário Ollama copiado
FROM python:3.12-slim

WORKDIR /app

# gcc para compilar pacotes Python nativos
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copia o binário Ollama do estágio anterior
COPY --from=ollama-bin /usr/bin/ollama /usr/local/bin/ollama

# Dependências Python — instala deps primeiro (cache layer), depois copia código
COPY pyproject.toml .
COPY README.md .
# Instala só as dependências (sem o pacote ainda) para aproveitar cache do Docker
RUN pip install --no-cache-dir ".[server]" --no-build-isolation 2>/dev/null || \
    pip install --no-cache-dir typer rich pydantic pyyaml httpx psutil fastapi "uvicorn[standard]"

# Código da aplicação (após deps — muda mais frequentemente)
COPY bauer/ ./bauer/

# Instala o pacote local (rápido — deps já estão no cache)
RUN pip install --no-cache-dir -e ".[server]" --no-deps

# Script de inicialização: sobe Ollama em bg, baixa modelo, inicia bauer serve
COPY start.sh /start.sh
RUN chmod +x /start.sh

# Volumes persistentes
VOLUME ["/app/workspace", "/app/memory", "/app/logs"]

# Modelos Ollama ficam aqui — montar como volume para persistir entre rebuilds
VOLUME ["/root/.ollama"]

# Porta padrão do bauer serve
EXPOSE 8000

ENV PYTHONUNBUFFERED=1
# Modelo padrão — sobrescreva com BAUER_MODEL no .env ou docker-compose
ENV BAUER_MODEL=qwen2.5-coder:3b

CMD ["/start.sh"]
