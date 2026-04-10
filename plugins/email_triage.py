"""
Email Triage Plugin — AI email categorization via SiliconFlow.
Provides an MCP tool for categorizing emails by priority and category.
"""

import json
import logging
import os

logger = logging.getLogger("plugins.email_triage")

__plugin_meta__ = {
    "name": "email_triage",
    "version": "1.0.0",
    "description": "AI email kategorizalas -- beerkező emailek prioritas/kategoria besorolasa",
}

IGNORE_SENDERS = [s.strip().lower() for s in os.environ.get("IGNORE_SENDERS", "noreply,no-reply,mailer-daemon,newsletter").split(",") if s.strip()]
URGENT_SENDERS = [s.strip().lower() for s in os.environ.get("URGENT_SENDERS", "").split(",") if s.strip()]
URGENT_KEYWORDS = [k.strip().lower() for k in os.environ.get("URGENT_KEYWORDS", "urgent,surgos,fontos,deadline,hatarido").split(",") if k.strip()]


def _fallback_categorize(sender: str, subject: str) -> dict:
    """String-matching fallback when AI is unavailable."""
    sender_lower = sender.lower()
    subject_lower = subject.lower()
    for pattern in IGNORE_SENDERS:
        if pattern in sender_lower:
            return {"priority": "ignore", "category": "spam", "summary_hu": "", "suggested_action": None}
    for pattern in URGENT_SENDERS:
        if pattern in sender_lower:
            return {"priority": "urgent", "category": "work", "summary_hu": subject, "suggested_action": "reply"}
    for kw in URGENT_KEYWORDS:
        if kw in subject_lower:
            return {"priority": "urgent", "category": "work", "summary_hu": subject, "suggested_action": "flag"}
    return {"priority": "normal", "category": "work", "summary_hu": subject, "suggested_action": None}


def register_tools(app, deps):
    """Register email triage MCP tool."""
    import httpx

    sf_key = deps.get("siliconflow_api_key", "")
    sf_base = deps.get("siliconflow_base_url", "https://api.siliconflow.com/v1")
    sf_models = deps.get("siliconflow_models", {})

    @app.tool()
    async def email_triage(sender: str, subject: str, body: str = "") -> str:
        """Categorize an email using AI. Returns priority, category, summary, and suggested action.

        Args:
            sender: Email sender address/name
            subject: Email subject line
            body: Email body text (truncated to 1500 chars)
        """
        if not sf_key:
            return json.dumps(_fallback_categorize(sender, subject), ensure_ascii=False)

        prompt = (
            f"Elemezd az alabbi emailt es valaszolj CSAK JSON formatumban.\n\n"
            f"Felado: {sender}\nTargy: {subject}\nTartalom: {body[:1500]}\n\n"
            f'Valasz (kizarolag valid JSON, semmi mas):\n'
            f'{{"priority": "urgent|important|normal|ignore",\n'
            f'  "category": "work|personal|newsletter|invoice|notification|spam",\n'
            f'  "summary_hu": "Max 2 mondat magyarul az email lenyege",\n'
            f'  "suggested_action": "reply|archive|forward|flag|null"}}'
        )

        model_id = sf_models.get("deepseek", "deepseek-ai/DeepSeek-V3.2")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{sf_base}/chat/completions",
                    headers={"Authorization": f"Bearer {sf_key}", "Content-Type": "application/json"},
                    json={
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": "Te egy email triage rendszer vagy. KIZAROLAG valid JSON-t valaszolj, semmi mast."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 400,
                    },
                )
                data = json.loads(resp.text)

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            result = json.loads(content)
            if "priority" not in result or "summary_hu" not in result:
                result = _fallback_categorize(sender, subject)

            logger.info("Plugin triage: %s -> %s (%s)", subject[:40], result["priority"], result.get("category", "?"))
            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            logger.warning("Plugin triage failed, fallback: %s", e)
            return json.dumps(_fallback_categorize(sender, subject), ensure_ascii=False)
