# Bauer Agent

Runtime adaptativo para LLMs locais e cloud.

> Hermes é rígido. Bauer é adaptativo.
> Roda com o que tem, ajusta o que precisar, avisa claramente.

## Funcionalidades

- **16 providers**: Ollama (local), Groq, OpenAI, Anthropic, Gemini, Mistral, DeepSeek, xAI, Together, OpenRouter, Azure, GitHub Models, GitHub Copilot e outros
- **18 tools**: leitura/escrita de arquivos, shell, HTTP, glob, regex, cálculo, Kanban e mais
- **Multi-agent**: agents especializados com identidade, memória e workspace isolado por empresa
- **Orquestrador**: execução paralela de passos com DAG de dependências e persistência de progresso
- **Persistência de sessão**: histórico automático por agent — retoma de onde parou
- **Model switch ao vivo**: troca de provider/modelo dentro da sessão sem reiniciar
- **bauer serve**: API HTTP + Web UI para uso remoto

---

## Instalação

### Linux (Debian/Ubuntu)

```bash
# 1. Instalar dependências do sistema
sudo apt install python3-full python3-pip -y

# 2. Clonar o repositório
git clone https://github.com/henriqueferraz86/bauer-agent.git
cd bauer-agent

# 3. Criar e ativar o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# 4. Instalar o Bauer
pip install -e ".[server]"

# 5. Verificar instalação
bauer doctor
```

### Windows

```powershell
# 1. Clonar o repositório
git clone https://github.com/henriqueferraz86/bauer-agent.git
cd bauer-agent

# 2. Criar e ativar o ambiente virtual
python -m venv .venv
.venv\Scripts\activate

# 3. Instalar o Bauer
pip install -e ".[server]"

# 4. Verificar instalação
bauer doctor
```

> **Nota Windows**: se o terminal travar ao digitar API keys, é normal — o campo está mascarado (não aparece o que você digita).

---

## Configuração

Copie o `.env.example` para `.env` e preencha as API keys dos providers que vai usar:

```bash
cp .env.example .env
# edite o .env com suas chaves
```

Configure o provider padrão interativamente:

```bash
bauer model
```

---

## Uso básico

```bash
bauer doctor              # diagnóstico: provider, modelo, RAM, contexto
bauer chat                # chat interativo
bauer model               # trocar provider/modelo
bauer status              # dashboard: modelo ativo, auth, memória

bauer agent list          # lista agents criados
bauer agent run <nome>    # inicia agent especializado

bauer orchestrate run "sua tarefa"   # orquestrador multi-passo

bauer serve               # sobe API HTTP + Web UI (porta 8000)
```

### Comandos dentro do chat / agent

| Comando | Ação |
|---|---|
| `/model` | Troca provider/modelo ao vivo |
| `/status` | Tokens usados e budget |
| `/clear` | Limpa histórico |
| `/sessions` | Lista sessões salvas |
| `/memory` | Memória do agent |
| `/kanban` | Board de tarefas |
| `/exit` | Encerra |

---

## Docker

```bash
# Sobe Bauer + Ollama juntos
docker compose up -d

# API disponível em http://localhost:8000
```

---

## Desenvolvimento

```bash
# Instalar com dependências de dev
pip install -e ".[server]"
pip install pytest pytest-cov

# Rodar testes
pytest

# Cobertura
pytest --cov=bauer --cov-report=term-missing
```

---

## Princípio do projeto

> Subir sem dor é mais importante que ter muitas features.

Ordem: confiável → adaptativo → aprendiz → especializado.
