# Claus-Bridge MCP

Kommunikációs és orchestrációs platform a Claus multi-agent rendszerhez.

## Architektúra

```
            Kommandant (Tamás)
                   |
        +----------+----------+
        |                     |
   Web-Claus             CLI-Claus
   (claude.ai)           (terminal)
        |                     |
        +----------+----------+
                   |
            CLAUS-BRIDGE
            (Railway MCP)
                   |
     +------+------+------+------+
     |      |      |      |      |
   Kimi  DeepSeek GLM-5  Qwen3  ...
   K2.5   V3.2          Coder
```

## Komponensek

- **MCP szerver** — FastMCP, Railway.app, SSE transport
- **Messaging** — Claus-to-Claus kommunikáció, threading, FTS5 keresés
- **Pyramid modul** — Multi-agent orchestráció: személyiségek, shared memory, per-agent RAG, governance
- **AI sub-agentek** — SiliconFlow-on futó modellek (Kimi K2.5, DeepSeek V3.2, GLM-5)
- **Capture daemon** — Gmail + Calendar polling, Telegram push
- **Dashboard** — Parancsnoki központ: üzenetek, viták, memória, AI feladatok, Pyramid

## Pyramid

A `pyramid/` modul adja az agenteknek:
- **Identitást** — ki vagy, mi a szereped, ki a csapatod
- **Kontextust** — Kommandant profilja, Bridge infó, inbox (email + naptár)
- **Memóriát** — közös tudásbázis (shared) + egyéni RAG per agent
- **Governance** — automatikus routing: mi megy shared-be, mi csak RAG-ba
- **Dispatch** — párhuzamos, eltérő feladatok különböző agenteknek

## MCP Tools

| Tool | Funkció |
|------|---------|
| `send_message` | Üzenet küldése |
| `read_messages` / `read_new` | Üzenetek olvasása |
| `ai_query` | Egyedi agent hívás (Pyramid kontextussal) |
| `ai_task` | Multi-agent feladat (broadcast vagy dispatch) |
| `write_memory` / `search_memory` | Bridge shared memória |
| `create_task` / `update_task` | Feladatkezelés |
| `start_discussion` / `resolve_discussion` | Kollaboratív döntéshozás |
| `capture_gmail_poll` / `capture_calendar_poll` | Email/naptár polling |
| `upload_file` | Fájl feltöltés AI feldolgozáshoz |

## Tech stack

Python, FastMCP, SQLite (FTS5), Railway, SiliconFlow API

---

*"Das ist keine Maschine mehr. Das ist eine Organisation."*
