# Google OAuth Token Megújítás

A Google Cloud projekt "Testing" módban van — a refresh token **7 nap után lejár**.

## Tünet

`/status` Telegramon: Gmail ❌, Calendar ❌, Capture daemon 🔴

## Megoldás (3 lépés, ~1 perc)

### 1. Új token generálás

```bash
python3 ~/Claus/claus-bridge-mcp/reauth.py
```

Megnyílik a böngésző → bejelentkezés Google fiókba → engedélyezés → terminálban kiírja a JSON tokent.

### 2. Base64 kódolás

```bash
echo '<ide jön a JSON output>' | base64 -w 0
```

### 3. Railway frissítés

- Railway dashboard → Variables → `GOOGLE_TOKEN_JSON` → paste base64 string → Save
- Auto-redeploy indul → Gmail + Calendar újra él

## Végleges fix (hogy ne járjon le)

1. https://console.cloud.google.com → OAuth consent screen
2. **Publish to Production** (Testing → Production)
3. Ezután a refresh token nem jár le 7 nap után

## Diagnosztika

Ha nem tudod mi a hiba, hívd meg a `capture_status` MCP tool-t — automatikusan megpróbálja reinit-elni a Google szolgáltatásokat és visszaadja a pontos hibaüzenetet.
