# VoxRouter

Roteador de voz unificado para modelos de geração de voz por IA — uma única API
(compatível com a OpenAI `/v1/audio/speech`) na frente de vários provedores:
ElevenLabs, OpenAI, Google Cloud TTS, Azure Speech, Microsoft Edge TTS (grátis),
Kokoro e OmniVoice (locais).

Ele decide **para qual modelo enviar cada pedido** e troca automaticamente quando
um provedor cai, fica caro demais, ou não atende a regra da rota.

## Recursos

- **API compatível com OpenAI** — funciona com o SDK oficial da OpenAI (basta trocar `base_url`)
- **Sintaxe direta** `provedor/voz` — `edge/pt-BR-FranciscaNeural`, `openai/tts-1-hd`, `elevenlabs/<id-da-voz>`
- **Rotas nomeadas** com estratégias: `first`, `random`, `round_robin`, `weighted`, `cheapest`, `quota_aware`
- **Cotas (quota-aware)**: cota de caracteres por período por provedor — o roteador desvia de provedores sem cota, persiste o consumo entre restarts e usa `quota_aware` para escolher o mais barato entre os que ainda têm cota
- **Descoberta de vozes** (`/v1/voices`): catálogo de vozes por provedor com filtros de idioma e busca
- **Regras de roteamento** por voz, idioma, formato, tamanho do texto, trecho do texto
- **Failover automático** + circuit breaker (cooldown crescente por provedor)
- **Cache em disco** (TTL configurável, chave determinística por parâmetros)
- **Health, stats e console web** em `http://127.0.0.1:8000`

## Instalação

```bash
cd voxrouter
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
```

## Executar

```bash
.venv/Scripts/python -m voxrouter --config config.example.yaml
# console: http://127.0.0.1:8000  |  docs: http://127.0.0.1:8000/docs
```

## Uso rápido

```bash
# rota nomeada (configuração decide o provedor)
curl -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","input":"Olá, mundo!"}' -o saida.mp3

# sintaxe direta: provedor/voz (sem tocar na config)
curl -X POST http://127.0.0.1:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"model":"edge/pt-BR-FranciscaNeural","input":"Testando o roteador."}' -o saida2.mp3

# com o SDK oficial da OpenAI:
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="local")
with client.audio.speech.with_streaming_response.create(
    model="auto", voice="alloy", input="Olá do VoxRouter!"
) as resp:
    resp.stream_to_file("out.mp3")
```

A resposta traz cabeçalhos úteis: `X-Provider`, `X-Voice`, `X-Latency-Ms`, `X-Cache`.

## Configuração

Copie `config.example.yaml` para `config.yaml` e ajuste. Dois blocos:

**`providers`** — cada provedor tem `type`, `api_key_env` (variável de ambiente com a
chave), `base_url`, `options` específicas, `cooldown_s` (janela do circuit breaker),
`weight` e `cost_per_char` (para a estratégia `cheapest`).

**`models`** — rotas nomeadas:

```yaml
models:
  auto:                        # tenta na ordem, com failover
    strategy: first
    providers: [kokoro, edge, openai]

  qualidade:
    strategy: weighted
    providers:
      - {name: elevenlabs, weight: 60}
      - {name: openai, weight: 40}

  ptbr:
    strategy: first
    providers: [edge, google, openai]
    rules:                     # primeira regra que bater substitui a lista
      - if: {voice_regex: "^pt-BR", text_max_chars: 300}
        providers: [openai, edge]
```

Condições suportadas em `if`: `voice`, `voice_regex`, `language`, `format`,
`text_max_chars`, `text_min_chars`, `contains`.

## Cotas (quota-aware)

Cada provedor pode ter uma cota de caracteres por período; o consumo é
persistido em `quota.json` (caminho em `quota_state.file`) e sobrevive a
restarts. Semântica:

- Consumo conta **apenas em sínteses bem-sucedidas** — falha e cache hit não consomem.
- Provedor `exhausted` vai para o fim da fila em toda estratégia; se **todos**
  estiverem exaustos, o roteador mantém a ordem normal (degradação graciosa em vez de 503).
- `reserve` marca os últimos X% como `low` (vai pro fim, mas ainda é usável).
- A estratégia `quota_aware` escolhe o **mais barato entre os que ainda têm cota**.

```yaml
providers:
  edge:
    type: edge
    quota:
      limit: 100000     # caracteres
      period: month     # day | month
      reserve: 0.05     # últimos 5% = "low"
```

```bash
curl http://127.0.0.1:8000/quota                # uso por provedor
curl -X POST "http://127.0.0.1:8000/quota/reset?provider=edge"
```

## Descoberta de vozes

```bash
curl "http://127.0.0.1:8000/v1/voices?provider=edge&language=pt-BR"
curl "http://127.0.0.1:8000/v1/voices?search=francisca"
```

O Edge busca a lista ao vivo da Microsoft (cache 1h, fallback estático se
offline); Azure, Google, Kokoro e OpenAI usam catálogos estáticos curados;
ElevenLabs lista as vozes da sua conta quando a chave está presente.

Campos aceitos na chamada: `model`, `input`, `voice`, `response_format`
(`mp3|wav|opus|aac|flac|pcm`), `speed`, `language` — e qualquer campo extra é
repassado ao provedor (`extra`), útil para `ref_audio`/`instruct` do OmniVoice
ou `lang_code` do Kokoro.

## Provedores

| type        | Provedor                     | Chave (env)              | Observações                                        |
|-------------|------------------------------|--------------------------|----------------------------------------------------|
| `edge`      | Microsoft Edge TTS           | nenhuma                  | grátis; pt-BR-AntonioNeural, pt-BR-FranciscaNeural |
| `openai`    | OpenAI TTS                   | `OPENAI_API_KEY`         | tts-1, tts-1-hd, gpt-4o-mini-tts                    |
| `elevenlabs`| ElevenLabs                   | `ELEVENLABS_API_KEY`     | clonagem de voz premium; `elevenlabs/<id-da-voz>`   |
| `google`    | Google Cloud TTS             | `GOOGLE_TTS_API_KEY`     | vozes Neural2 (pt-BR-Neural2-C...)                 |
| `azure`     | Azure Speech                 | `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` | via SSML          |
| `kokoro`    | Kokoro (local)               | nenhuma                  | `docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu` |
| `omnivoice` | OmniVoice (local)            | nenhuma                  | `omnivoice-openai-tts-server --port 6655`          |
| `mock`      | Tom senoidal WAV             | nenhuma                  | testes; `options.fail: true` simula queda          |

## Endpoints

| Método | Rota                | Descrição                                    |
|--------|---------------------|----------------------------------------------|
| POST   | `/v1/audio/speech`  | síntese (compatível com OpenAI)              |
| GET    | `/v1/models`        | rotas disponíveis + sintaxe direta           |
| GET    | `/v1/voices`        | catálogo de vozes (filtros: `provider`, `language`, `search`) |
| GET    | `/quota`            | uso de cota por provedor                     |
| POST   | `/quota/reset`      | zera a cota (`?provider=` para um só)        |
| GET    | `/health`           | status dos provedores (breaker + quota)      |
| GET    | `/stats`            | contadores por provedor                      |
| POST   | `/cache/clear`      | limpa o cache                                |
| GET    | `/`                 | console web de teste                         |

## Adicionar um provedor

1. Crie `voxrouter/providers/seuprovedor.py` com uma classe herdeira de
   `BaseProvider` (implemente `synthesize`, lance `ProviderError` em falha).
2. Registre em `REGISTRY` no `voxrouter/providers/__init__.py`.
3. Adicione o bloco em `config.yaml` e use em qualquer rota.

## Console web

Além do console embutido em `/`, há um widget standalone em
[`demo/widget.html`](demo/widget.html) — abra-o em qualquer página (ou no Hermes)
com o servidor rodando e ele fala com `http://127.0.0.1:8000` via CORS.

## Testes

```bash
.venv/Scripts/python -m pytest -q
```

## Aviso ético

Clonagem de voz exige consentimento explícito da pessoa dona da voz. Não use
para falsificação de identidade, fraudes ou golpes. O uso é de sua
responsabilidade e deve respeitar os termos de cada provedor.
