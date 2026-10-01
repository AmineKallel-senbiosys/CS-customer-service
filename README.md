# Velio

Velio is Velia’s customer-support chatbot for the Velia ring. It answers from a validated knowledge base, verifies the customer by email before showing personal data, and can look up orders, run a battery diagnostic, update shipping, and open a HubSpot ticket.

The chat UI is served by the same FastAPI app. Open it at [http://127.0.0.1:8000](http://127.0.0.1:8000) after you start the server.

## How a reply is built

1. The customer message is stored on an in-memory session.
2. Validated policies, product facts, and global rules from `KB/velio_kb_v1.yaml` are always in the prompt. Draft and excluded content is not.
3. Article embeddings (`text-embedding-3-small`) shortlist the closest validated articles. The model opens an article with the `open_article` tool when it needs the procedure.
4. [Laya](https://pypi.org/project/laya/) reads language and conversation signals (safety, frustration, personal data, scope). Those signals are hints. The model still chooses the reply.
5. The model (`gpt-4.1-mini` by default) answers in the customer’s language, using one of the codes listed under `meta.reply_languages` in the knowledge base.

## Tools the model can call

| Tool | What it does |
| --- | --- |
| `open_article` | Reads one validated article by id, such as `KB-03`. |
| `send_verification_code` | Starts email verification. The code is not shown in the chat. |
| `check_verification_code` | Checks the code the customer sent. |
| `run_ring_diagnostic` | Battery verdict for a verified app-login email. Measurements stay on the ticket. |
| `lookup_orders` | Orders for a verified email, from Shopify and Floship. |
| `update_shipping_details` | Updates the address of an order that has not shipped. |
| `create_support_ticket` | Opens a HubSpot ticket titled `Velio - …` and attaches the transcript. |

Order, address, phone, size, colour, and ring data are available only after the email is verified. Until a mail provider is configured, verification uses the fixed demo code in `OTP_CODE` (default `000111`).

Battery results come from `KB/battery_runtime_by_user.csv`. A last-week average of 24 hours or more is treated as healthy.

## Requirements

- Python 3.11
- An OpenAI API key
- Optional: HubSpot, Shopify, and Floship credentials for tickets, orders, and shipping updates

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
OPEN_AI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_TEMPERATURE=0.2

HUBSPOT_PRIVATE_APP_TOKEN=
HUBSPOT_BASE_URL=https://api.hubapi.com
HUBSPOT_PORTAL_ID=

FLOSHIP_API_TOKEN=
SHOPIFY_API_KEY=
SHOPIFY_SHOP=

OTP_CODE=000111
VELIO_HOST=127.0.0.1
VELIO_PORT=8000
```

`OPENAI_API_KEY` is accepted as an alias of `OPEN_AI_API_KEY`. `Floship_API_Token` is accepted as an alias of `FLOSHIP_API_TOKEN`.

## Run

```powershell
python -m app.main
```

The API listens on `VELIO_HOST` and `VELIO_PORT` (defaults `127.0.0.1:8000`). On startup it loads the knowledge base, builds or reuses article embeddings, and warms Laya in the background.

Embeddings are cached in `.cache/article_embeddings.json` and rebuilt when article text changes.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Chat page |
| `POST` | `/api/session` | Start a session. Body: `{ "topic": "..." }` |
| `POST` | `/api/message` | One turn. Body: `{ "message": "...", "session_id": "..." }` |
| `POST` | `/api/message/stream` | Same turn as server-sent events (`start`, `token`, `done`, `error`) |
| `GET` | `/api/thinking/{session_id}` | Current tool status label |
| `GET` | `/api/agents` | Returns the Velio agent |

Sessions live in process memory. Restarting the server clears them.

## Project layout

```
app/
  main.py            FastAPI app and chat routes
  agent.py           Model loop, tools, and embeddings
  kb.py              YAML load and article retrieval
  services.py        HubSpot, Shopify, Floship, OTP, battery diagnostic
  signals.py         Laya language and conversation signals
  prompts/system.txt System instructions
  config.py          Paths and environment variables
KB/
  velio_kb_v1.yaml              Knowledge base
  battery_runtime_by_user.csv   Battery averages by app user
web/
  index.html
  static/css/chatbot.css
  static/js/chatbot.js
```

## Knowledge base

`KB/velio_kb_v1.yaml` is the source of truth. Only content with `status: validated` is given to the model: policies, product facts, global rules, and articles. Draft articles, mappings, and open decisions stay in the file for editors and are not used in replies.

Reply languages are `meta.reply_languages`. Add or remove a language there, then restart the API.

When a policy or product fact changes, search the file for its id. Templates list the ids they use, so those templates need a new review before they go back to `validated`.
