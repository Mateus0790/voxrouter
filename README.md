# VoxRouter

Roteador de voz unificado para modelos de geração de voz por IA — uma única API
(compatível com a OpenAI `/v1/audio/speech` e `/v1/audio/transcriptions`) na frente
de vários provedores: Edge TTS (grátis), OpenAI, ElevenLabs, Google, Azure,
Cartesia, Deepgram, Fish Audio, Kokoro, OmniVoice e Whisper local.

Ele decide **para qual modelo enviar cada pedido** e troca automaticamente quando
um provedor cai, fica sem cota, ou não atende a regra da rota.

## Recursos (v0.2)

- **API compatível com OpenAI** — SDK oficial com `base_url` trocado
- **Sintaxe direta** `provedor/voz` — `edge/pt-BR-FranciscaNeural`, `cartesia/<uuid>`
- **Rotas nomeadas** com estratégias: `first`, `random`, `round_robin`, `weighted`, `cheapest`, `quota_aware`
- **Cotas** com sync remoto (ElevenLabs) + persistência em disco
- **Aliases de voz** — mesma persona em vários provedores (`voices:`)
- **Textos longos** — split por frase + concat MP3/WAV
- **Streaming** com failover antes do primeiro byte
- **STT** via `/v1/audio/transcriptions` (OpenAI, Deepgram, Whisper local)
- **Auth + rate limit** por chave (`Bearer` + RPM)
- **Cache**, circuit breaker, `/health`, `/stats`, console web

## Instalação local

```bash
cd voxrouter
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
# STT local (opcional):
# .venv/Scripts/python -m pip install -e ".[local-stt]"
```

```bash
cp config.example.yaml config.yaml
.venv/Scripts/python -m voxrouter --config config.yaml
# console: http://127.0.0.1:8000  |  docs: http://127.0.0.1:8000/docs
```

## Docker Compose

```bash
cp .env.example .env          # preencha as chaves que for usar
cp config.example.yaml config.yaml
touch quota.json              # cotas persistidas (bind mount)
docker compose up --build
```

Só Edge + mock funcionam **sem nenhuma chave**. Para Cartesia/Deepgram/Fish/OpenAI/etc.,
preencha `.env` e mantenha os provedores na rota `models.auto`.

Kokoro local opcional: descomente o serviço `kokoro` em `docker-compose.yml`.

## Uso rápido

```bash
# rota nomeada
curl -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","input":"Olá, mundo!"}' -o saida.mp3

# sintaxe direta
curl -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"edge/pt-BR-FranciscaNeural","input":"Testando."}' -o saida2.mp3

# alias de voz + streaming
curl -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","voice":"pt-br-narradora-feminina","input":"Oi","stream":true}' -o stream.mp3

# transcrição
curl -X POST http://127.0.0.1:8000/v1/audio/transcriptions \
  -F file=@saida.mp3 -F model=auto -F response_format=text
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="local")
with client.audio.speech.with_streaming_response.create(
    model="auto", voice="alloy", input="Olá do VoxRouter!"
) as resp:
    resp.stream_to_file("out.mp3")
```

Cabeçalhos úteis: `X-Provider`, `X-Voice`, `X-Latency-Ms`, `X-Cache`.

## Configuração

Copie `config.example.yaml` → `config.yaml`. Blocos principais:

| Bloco | Função |
|-------|--------|
| `providers` | tipo, `api_key_env`, `base_url`, `options`, `quota`, `max_chars` |
| `models` | rotas TTS + estratégias + regras |
| `stt_models` | rotas de transcrição |
| `voices` | aliases lógicos → voz por provedor (`strict: true` pula sem mapeamento) |
| `auth` | chaves Bearer + RPM |
| `cache` / `quota_state` | disco e sync |

Condições em `if`: `voice`, `voice_regex`, `language`, `format`,
`text_max_chars`, `text_min_chars`, `contains`.

## Cotas

- Consome **só em síntese OK** (falha e cache hit não contam).
- `exhausted` vai pro fim; se todos exaustos, mantém ordem (não 503).
- `quota.sync: true` sincroniza com a API (ElevenLabs).

```bash
curl http://127.0.0.1:8000/quota
curl -X POST http://127.0.0.1:8000/quota/sync
curl -X POST "http://127.0.0.1:8000/quota/reset?provider=edge"
```

## Provedores

| type | Provedor | Chave (env) | Notas |
|------|----------|-------------|-------|
| `edge` | Microsoft Edge TTS | — | grátis; pt-BR |
| `openai` | OpenAI TTS + Whisper STT | `OPENAI_API_KEY` | tts-1 / whisper-1 |
| `elevenlabs` | ElevenLabs | `ELEVENLABS_API_KEY` | sync de cota |
| `google` | Google Cloud TTS | `GOOGLE_TTS_API_KEY` | Neural2 pt-BR |
| `azure` | Azure Speech | `AZURE_SPEECH_KEY` + região | SSML |
| `cartesia` | Cartesia Sonic | `CARTESIA_API_KEY` | voz = UUID |
| `deepgram` | Deepgram Aura/Nova | `DEEPGRAM_API_KEY` | TTS + STT |
| `fish` | Fish Audio | `FISH_API_KEY` | `reference_id` |
| `kokoro` | Kokoro local | — | `:8880` |
| `omnivoice` | OmniVoice local | — | `:6655` |
| `whisper_local` | faster-whisper | — | só STT |
| `mock` | seno WAV | — | testes |

## Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/v1/audio/speech` | síntese (streaming opcional) |
| POST | `/v1/audio/transcriptions` | STT (multipart) |
| GET | `/v1/models` | rotas TTS + STT |
| GET | `/v1/voices` | catálogo (`provider`, `language`, `search`) |
| GET | `/quota` | uso por provedor |
| POST | `/quota/sync` | sincroniza cotas remotas |
| POST | `/quota/reset` | zera cota |
| GET | `/health` | breaker + quota |
| GET | `/stats` | contadores |
| POST | `/cache/clear` | limpa cache |
| GET | `/` | console web |

## Testes

```bash
.venv/Scripts/python -m pytest -q
```

CI (GitHub Actions): pytest em 3.10–3.12 + build Docker + smoke `/health`.

## Aviso ético

Clonagem de voz exige consentimento explícito da pessoa dona da voz. Não use
para falsificação de identidade, fraudes ou golpes. O uso é de sua
responsabilidade e deve respeitar os termos de cada provedor.
