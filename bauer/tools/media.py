"""Media/vision tools: vision_analyze, video_analyze (url/cv2/gif), image_generate,
text_to_speech, transcribe_audio e mixture_of_agents.

Mixin herdado por ToolRouter. Inclui os helpers de cliente de visao/LLM
(_resolve_vision_client/_llm_single_turn), a deteccao de modelo multimodal
(_looks_multimodal/_MULTIMODAL_PATTERNS) e _package_available (checa cv2/PIL).
"""

from __future__ import annotations

from pathlib import Path

from .base import ToolError


_MULTIMODAL_PATTERNS = (
    "gpt-4o", "gpt-4-vision", "gpt-4-turbo", "o1", "o3", "o4",
    "claude-3", "claude-4", "claude-opus", "claude-sonnet", "claude-haiku",
    "gemini", "llava", "bakllava", "moondream", "pixtral", "llama-3.2-vision",
    "llama3.2-vision", "qwen2-vl", "qwen2.5-vl", "qwen-vl", "minicpm-v",
    "internvl", "phi-3-vision", "phi-3.5-vision", "vision",
)


def _looks_multimodal(model_name: str) -> bool:
    """Heurística: o nome do modelo parece suportar visão? (G18.4)"""
    m = (model_name or "").lower()
    return any(p in m for p in _MULTIMODAL_PATTERNS)


def _package_available(name: str) -> bool:
    """Verifica se um pacote Python está disponível sem importá-lo."""
    import sys
    import importlib.util
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ModuleNotFoundError):
        return False


class MediaToolsMixin:
    """Visao, video, imagem, audio/TTS e consulta multi-agente."""

    def _resolve_vision_client(self, tool: str):
        """Resolve o cliente para tools de visão (G18.4).

        Preferência: vision_client dedicado (auxiliary.vision_model) → confia
        na escolha explícita. Senão, usa o llm_client principal, mas só se o
        modelo parecer multimodal. Levanta ToolError claro e acionável quando
        não há cliente ou o modelo é text-only — em vez de mandar imagem pra um
        modelo de texto e receber lixo.
        """
        if self._vision_client is not None:
            return self._vision_client
        if self._llm_client is None:
            raise ToolError(
                f"{tool}: nenhum modelo de visao configurado.\n"
                "Configure auxiliary.vision_model no config.yaml "
                "(ex: ollama 'llava', ou gpt-4o/claude/gemini), ou rode num "
                "fluxo com llm_client."
            )
        model = getattr(self._llm_client, "model", "") or ""
        if not _looks_multimodal(model):
            raise ToolError(
                f"{tool}: o modelo ativo ('{model or 'desconhecido'}') nao parece "
                "suportar visao.\n"
                "Configure auxiliary.vision_model com um modelo multimodal "
                "(ex: ollama pull llava; ou gpt-4o/claude/gemini)."
            )
        return self._llm_client

    def _llm_single_turn(self, client, messages: list[dict]) -> str:
        """Chamada single-turn (sem tools) a um cliente LLM, via chat_stream.

        Helper central das tools que fazem UMA chamada direta ao modelo
        (vision_analyze, video_analyze, mixture_of_agents, browser_vision).

        IMPORTANTE: NÃO usar `run_one_turn` para isso. A assinatura dele é
        ``run_one_turn(ctx, router, client, model_name, ...)`` — não aceita uma
        lista de mensagens nem `tools=`. Chamá-lo como
        ``run_one_turn(client, messages, tools=None)`` quebra com TypeError, que
        era silenciosamente mascarado pelos `except Exception` dessas tools
        (deixando-as não-funcionais em produção, ainda que os testes passassem
        por mockar run_one_turn).
        """
        model = (
            getattr(client, "default_model", "")
            or getattr(client, "model", "")
            or self._model_name
            or ""
        )
        chunks = list(client.chat_stream(model, messages))
        return "".join(chunks)

    def _vision_analyze(self, args: dict) -> str:
        """Analisa imagem via modelo multimodal (OpenAI vision format).

        Boas práticas:
        - Suporta URL externa (passa diretamente) e path local (base64)
        - Detecta formato da imagem por extensão/magic bytes
        - Requer llm_client com suporte a chat multimodal
        - Fallback: usa httpx para chamar API OpenAI-compat diretamente
        """
        import base64

        image = args.get("image", "").strip()
        query = args.get("query", "").strip()

        if not image:
            raise ToolError("vision_analyze requer 'image' (URL ou path).")
        if not query:
            raise ToolError("vision_analyze requer 'query'.")

        # Determina se é URL ou path local
        if image.startswith(("http://", "https://")):
            image_content = {"type": "image_url", "image_url": {"url": image}}
        else:
            # Path local — lê e base64-encoda
            p = self._sandbox(image)
            if not p.exists():
                raise ToolError(f"Imagem nao encontrada: '{image}'")

            raw = p.read_bytes()
            ext = p.suffix.lower().lstrip(".")
            mime_map = {
                "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "gif": "image/gif",
                "webp": "image/webp", "bmp": "image/bmp",
            }
            mime = mime_map.get(ext, "image/jpeg")
            b64 = base64.b64encode(raw).decode("ascii")
            data_url = f"data:{mime};base64,{b64}"
            image_content = {"type": "image_url", "image_url": {"url": data_url}}

        # Mensagem no formato OpenAI multimodal
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": query},
                image_content,
            ],
        }

        # G18.4: usa o cliente de visão resolvido (vision_model dedicado ou
        # llm_client principal se multimodal). Erro claro e acionável se nenhum.
        vision_client = self._resolve_vision_client("vision_analyze")
        try:
            return self._llm_single_turn(vision_client, [message])
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"vision_analyze: erro ao chamar modelo: {exc}")

    def _transcribe_audio(self, args: dict) -> str:
        """Transcreve áudio para texto (Whisper Groq/OpenAI)."""
        path = str(args.get("path", "")).strip()
        if not path:
            raise ToolError("transcribe_audio requer 'path'.")
        from ..transcription import transcribe_audio
        result = transcribe_audio(path)
        if not result.get("success"):
            raise ToolError(f"Transcrição falhou: {result.get('error')}")
        return f"[{result.get('provider')}] {result['transcript']}"

    def _mixture_of_agents(self, args: dict) -> str:
        """Consulta múltiplos agentes em paralelo com perspectivas diferentes.

        Arquitetura (Mixture of Agents — Li et al., 2024):
          1. Para cada perspectiva: cria prompt especializado + chama LLM em paralelo
          2. Coleta todas as respostas
          3. Se synthesize=true: passada final de síntese combinando os insights
          4. Retorna respostas individuais + síntese

        Sem llm_client: simula perspectivas via prompts diferentes no mesmo modelo.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("mixture_of_agents requer 'query'.")

        if self._llm_client is None:
            raise ToolError(
                "mixture_of_agents requer llm_client configurado.\n"
                "O agente precisa estar rodando com um provider LLM ativo."
            )

        raw_perspectives = str(args.get("perspectives", "analitico|critico|criativo|pragmatico"))
        perspectives = [p.strip() for p in raw_perspectives.split("|") if p.strip()]
        synthesize = str(args.get("synthesize", "true")).lower() != "false"

        # Prompts de sistema por perspectiva
        persona_prompts = {
            "analitico": "Você é um analista sistemático. Decomponha o problema em partes, identifique causas e efeitos, use dados e lógica.",
            "critico": "Você é um crítico rigoroso. Identifique falhas, riscos, suposições incorretas e pontos fracos na situação.",
            "criativo": "Você é um pensador criativo. Proponha soluções inovadoras, faça conexões inesperadas, pense fora do padrão.",
            "pragmatico": "Você é um executor pragmático. Foque em ações concretas, priorize pelo impacto, considere recursos e tempo.",
            "especialista": "Você é um especialista de domínio. Aplique conhecimento técnico profundo e melhores práticas da área.",
            "cético": "Você é um questionador cético. Questione premissas, peça evidências, desafie conclusões.",
            "otimista": "Você é um estrategista otimista. Identifique oportunidades, vantagens e cenários positivos.",
            "sistemico": "Você pensa sistemicamente. Considere interdependências, efeitos de segunda ordem e o contexto maior.",
        }

        def _call_perspective(perspective: str) -> tuple[str, str]:
            system = persona_prompts.get(
                perspective.lower(),
                f"Você é um especialista com perspectiva '{perspective}'. Analise o problema sob esse ângulo.",
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ]
            try:
                response = self._llm_single_turn(self._llm_client, messages)
                return perspective, str(response)
            except Exception as exc:
                return perspective, f"[erro: {exc}]"

        # Executa perspectivas em paralelo
        individual: list[tuple[str, str]] = []
        max_workers = min(len(perspectives), 6)
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_call_perspective, p): p for p in perspectives}
            for f in as_completed(futures):
                individual.append(f.result())

        # Ordena pela ordem original das perspectivas
        order = {p: i for i, p in enumerate(perspectives)}
        individual.sort(key=lambda x: order.get(x[0], 99))

        # Monta output das respostas individuais
        lines = [f"[mixture_of_agents] Query: {query[:100]}\n"]
        lines.append(f"Perspectivas ({len(individual)}):")
        for perspective, response in individual:
            resp_preview = response[:400].strip()
            if len(response) > 400:
                resp_preview += "\n  ..."
            lines.append(f"\n── [{perspective.upper()}] ──")
            lines.append(resp_preview)

        # Passada de síntese
        if synthesize and len(individual) >= 2:
            synthesis_context = "\n\n".join(
                f"[{p.upper()}]: {r[:300]}" for p, r in individual
            )
            synthesis_prompt = (
                f"Você recebeu análises de {len(individual)} perspectivas diferentes "
                f"sobre a seguinte questão:\n\n{query}\n\n"
                f"Análises:\n{synthesis_context}\n\n"
                f"Sintetize os insights mais valiosos de cada perspectiva em uma "
                f"resposta integrada e acionável. Seja conciso e direto."
            )
            try:
                synthesis = self._llm_single_turn(
                    self._llm_client,
                    [{"role": "user", "content": synthesis_prompt}],
                )
                lines.append("\n── [SÍNTESE] ──")
                lines.append(str(synthesis)[:600])
            except Exception as exc:
                lines.append(f"\n[síntese falhou: {exc}]")

        return "\n".join(lines)

    def _video_analyze(self, args: dict) -> str:
        """Analisa vídeo por URL ou arquivo local.

        Estratégias (em ordem de preferência):
          1. URL + provider nativo (Gemini, GPT-4o com video):
             passa a URL diretamente como conteúdo de mídia
          2. Arquivo local — extrai frames-chave via cv2 (se disponível)
             ou via iteração de bytes em formato simples
          3. Fallback — analisa apenas o primeiro frame como imagem

        Boas práticas:
          - max_frames limita custo (default 5)
          - Frames espaçados uniformemente ao longo do vídeo
          - Síntese final combina análises dos frames individuais
        """
        video = str(args.get("video", "")).strip()
        query = str(args.get("query", "")).strip()
        max_frames = int(args.get("max_frames", 5))
        max_frames = max(1, min(max_frames, 20))

        if not video:
            raise ToolError("video_analyze requer 'video' (URL ou path).")
        if not query:
            raise ToolError("video_analyze requer 'query'.")
        # G18.4: o gate de visão (modelo dedicado ou principal multimodal) é
        # aplicado nos helpers, no ponto da chamada ao modelo — depois da
        # validação de formato/dependências, para que erros de input venham
        # antes do erro de capability.

        # ── Estratégia 1: URL → provider nativo ─────────────────────────────
        if video.startswith(("http://", "https://")):
            return self._video_analyze_url(video, query)

        # ── Estratégia 2: arquivo local → extração de frames ────────────────
        p = self._sandbox(video)
        if not p.exists():
            raise ToolError(f"Video nao encontrado: '{video}'")

        ext = p.suffix.lower()
        if ext not in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".gif", ".m4v"):
            raise ToolError(
                f"Formato '{ext}' nao suportado. "
                "Suportados: .mp4, .avi, .mov, .mkv, .webm, .gif"
            )

        # Tenta cv2 primeiro (mais preciso)
        if _package_available("cv2"):
            return self._video_analyze_cv2(p, query, max_frames)

        # Fallback: tenta PIL/Pillow para GIFs animados
        if ext == ".gif" and _package_available("PIL"):
            return self._video_analyze_gif_pil(p, query, max_frames)

        raise ToolError(
            "video_analyze para arquivos locais requer OpenCV ou PIL instalado.\n"
            "Instale com: pip install opencv-python\n"
            "Ou use uma URL pública para análise via provider."
        )

    def _video_analyze_url(self, url: str, query: str) -> str:
        """Passa URL de vídeo diretamente ao LLM (Gemini, GPT-4o vision)."""
        # Formato OpenAI-compat para vídeo via URL
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": query},
                {
                    "type": "image_url",
                    "image_url": {"url": url},
                },
            ],
        }
        try:
            result = self._llm_single_turn(self._resolve_vision_client("video_analyze"), [message])
            return f"[video_analyze — URL]\n{result}"
        except Exception as exc:
            raise ToolError(
                f"video_analyze: erro ao analisar URL via provider: {exc}\n"
                "Verifique se seu provider suporta análise de vídeo por URL "
                "(Gemini suporta, OpenAI gpt-4o ainda não)."
            )

    def _video_analyze_cv2(self, path: Path, query: str, max_frames: int) -> str:
        """Extrai frames-chave via cv2 e analisa cada um."""
        import cv2
        import base64
        import tempfile

        cap = cv2.VideoCapture(str(path))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        duration_s = total_frames / fps if fps > 0 else 0

        if total_frames <= 0:
            cap.release()
            raise ToolError(f"Nao foi possivel ler frames de '{path.name}'.")

        # Índices uniformemente distribuídos
        indices = [int(i * total_frames / max_frames) for i in range(max_frames)]

        frame_analyses: list[str] = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            timestamp_s = idx / fps if fps > 0 else 0

            # Encode frame como JPEG em memória
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                continue
            b64 = base64.b64encode(buf.tobytes()).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64}"

            try:
                from ..agent import run_one_turn
                frame_query = (
                    f"Frame do vídeo '{path.name}' em {timestamp_s:.1f}s "
                    f"(de {duration_s:.1f}s total).\n{query}"
                )
                msg = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": frame_query},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
                resp = self._llm_single_turn(self._resolve_vision_client("video_analyze"), [msg])
                frame_analyses.append(f"[{timestamp_s:.1f}s] {str(resp)[:300]}")
            except Exception as exc:
                frame_analyses.append(f"[{timestamp_s:.1f}s] [erro: {exc}]")

        cap.release()

        if not frame_analyses:
            raise ToolError("Nao foi possivel extrair ou analisar nenhum frame.")

        # Síntese final
        lines = [
            f"[video_analyze] '{path.name}' — {duration_s:.1f}s, {max_frames} frames\n"
        ]
        lines.append("Análise por frame:")
        lines.extend(f"  {a}" for a in frame_analyses)

        if len(frame_analyses) > 1:
            try:
                synthesis_input = "\n".join(frame_analyses)
                synth_msg = {
                    "role": "user",
                    "content": (
                        f"Você analisou {len(frame_analyses)} frames do vídeo '{path.name}'. "
                        f"Pergunta original: {query}\n\n"
                        f"Análises dos frames:\n{synthesis_input}\n\n"
                        "Sintetize uma resposta final coerente sobre o vídeo completo."
                    ),
                }
                synthesis = self._llm_single_turn(self._resolve_vision_client("video_analyze"), [synth_msg])
                lines.append("\nSíntese:")
                lines.append(str(synthesis)[:600])
            except Exception:
                pass

        return "\n".join(lines)

    def _video_analyze_gif_pil(self, path: Path, query: str, max_frames: int) -> str:
        """Analisa GIF animado extraindo frames via PIL."""
        import base64
        from io import BytesIO
        from PIL import Image

        gif = Image.open(path)
        total = getattr(gif, "n_frames", 1)
        indices = [int(i * total / max_frames) for i in range(min(max_frames, total))]

        frame_analyses: list[str] = []
        for idx in indices:
            gif.seek(idx)
            buf = BytesIO()
            frame = gif.convert("RGB")
            frame.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64}"

            try:
                msg = {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Frame {idx}/{total} do GIF. {query}"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
                resp = self._llm_single_turn(self._resolve_vision_client("video_analyze"), [msg])
                frame_analyses.append(f"[frame {idx}] {str(resp)[:300]}")
            except Exception as exc:
                frame_analyses.append(f"[frame {idx}] [erro: {exc}]")

        lines = [f"[video_analyze] GIF '{path.name}' — {total} frames\n"]
        lines.extend(f"  {a}" for a in frame_analyses)
        return "\n".join(lines)

    def _image_generate(self, args: dict) -> str:
        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            raise ToolError("image_generate: 'prompt' é obrigatório.")
        if self._llm_client is None:
            raise ToolError("image_generate: llm_client não configurado.")

        model = str(args.get("model", "dall-e-3"))
        size = str(args.get("size", "1024x1024"))
        quality = str(args.get("quality", "standard"))
        output_file = args.get("output_file")

        valid_models = ("dall-e-3", "dall-e-2")
        if model not in valid_models:
            raise ToolError(f"image_generate: model deve ser {valid_models}.")
        valid_sizes = ("1024x1024", "1792x1024", "1024x1792", "512x512", "256x256")
        if size not in valid_sizes:
            raise ToolError(f"image_generate: size deve ser um de {valid_sizes}.")

        try:
            # Descobre qual objeto tem .images.generate:
            # 1) self._llm_client (caso mock direto — não precisa de openai instalado)
            # 2) self._llm_client._client (caso wrapper bauer sobre openai.OpenAI)
            # 3) cria openai.OpenAI com credenciais (exige openai instalado)
            _lc = self._llm_client
            if hasattr(_lc, "images") and callable(getattr(getattr(_lc, "images", None), "generate", None)):
                client_obj = _lc
            elif hasattr(getattr(_lc, "_client", None), "images"):
                client_obj = _lc._client
            else:
                try:
                    import openai
                except ImportError:
                    raise ToolError("image_generate: requer 'pip install openai'.")
                base_url = getattr(_lc, "base_url", None) or "https://api.openai.com/v1"
                api_key = getattr(_lc, "api_key", None) or ""
                client_obj = openai.OpenAI(api_key=api_key, base_url=base_url)

            kw: dict = {"model": model, "prompt": prompt, "size": size, "n": 1}
            if model == "dall-e-3":
                kw["quality"] = quality
            response = client_obj.images.generate(**kw)
            img_url = response.data[0].url
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"image_generate: falha na API — {exc}") from exc

        result = f"[image_generate] Imagem gerada:\n  URL: {img_url}"

        if output_file:
            try:
                import httpx
                dest = self._sandbox(output_file)
                dest.parent.mkdir(parents=True, exist_ok=True)
                r = httpx.get(img_url, timeout=30)
                r.raise_for_status()
                dest.write_bytes(r.content)
                result += f"\n  Salvo em: {dest.relative_to(self.workspace)}"
            except Exception as exc:
                result += f"\n  Aviso: falha ao salvar — {exc}"

        return result

    def _text_to_speech(self, args: dict) -> str:
        text = str(args.get("text", "")).strip()
        if not text:
            raise ToolError("text_to_speech: 'text' é obrigatório.")
        if len(text) > 4096:
            raise ToolError("text_to_speech: texto excede 4096 caracteres (limite da API).")
        output_file = str(args.get("output_file", "")).strip()
        if not output_file:
            raise ToolError("text_to_speech: 'output_file' é obrigatório.")
        if self._llm_client is None:
            raise ToolError("text_to_speech: llm_client não configurado.")

        voice = str(args.get("voice", "alloy"))
        model = str(args.get("model", "tts-1"))
        valid_voices = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
        if voice not in valid_voices:
            raise ToolError(f"text_to_speech: voice deve ser um de {valid_voices}.")
        valid_models = ("tts-1", "tts-1-hd")
        if model not in valid_models:
            raise ToolError(f"text_to_speech: model deve ser {valid_models}.")

        dest = self._sandbox(output_file)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            _lc = self._llm_client
            # Descobre o objeto com .audio.speech.create:
            # 1) self._llm_client direto (mocks e openai.OpenAI wrappers expostos)
            # 2) self._llm_client._client (wrapper bauer)
            # 3) cria openai.OpenAI com credenciais (exige openai instalado)
            if hasattr(_lc, "audio") and callable(getattr(getattr(_lc, "audio", None), "speech", None) and
                                                   getattr(getattr(getattr(_lc, "audio", None), "speech", None), "create", None) or None):
                client_obj = _lc
            elif hasattr(_lc, "audio"):
                client_obj = _lc
            elif hasattr(getattr(_lc, "_client", None), "audio"):
                client_obj = _lc._client
            else:
                try:
                    import openai
                except ImportError:
                    raise ToolError("text_to_speech: requer 'pip install openai'.")
                base_url = getattr(_lc, "base_url", None) or "https://api.openai.com/v1"
                api_key = getattr(_lc, "api_key", None) or ""
                client_obj = openai.OpenAI(api_key=api_key, base_url=base_url)

            response = client_obj.audio.speech.create(model=model, voice=voice, input=text)
            response.stream_to_file(str(dest))
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"text_to_speech: falha na API — {exc}") from exc

        size_kb = dest.stat().st_size // 1024 if dest.exists() else 0
        return (
            f"[text_to_speech] Áudio gerado:\n"
            f"  Arquivo: {dest.relative_to(self.workspace)}\n"
            f"  Tamanho: {size_kb} KB | Voice: {voice} | Model: {model}"
        )
