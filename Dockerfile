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
# O metadata do pacote precisa existir aqui; sem isso o pip cai no fallback e
# os extras novos de `server` podem ficar fora da imagem silenciosamente.
RUN mkdir -p bauer
COPY bauer/__init__.py ./bauer/__init__.py
# Instala só as dependências (sem o pacote ainda) para aproveitar cache do Docker
RUN (pip install --no-cache-dir ".[server]" --no-build-isolation 2>/dev/null || \
    pip install --no-cache-dir \
      typer rich pydantic pyyaml httpx psutil prompt-toolkit cryptography \
      ddgs beautifulsoup4 sqlalchemy openai "agno[os]>=1.7" \
      fastapi "uvicorn[standard]" python-multipart argon2-cffi google-auth) && \
    pip install --no-cache-dir "agno[os]>=1.7"

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
CMD ["/start.sh"]
