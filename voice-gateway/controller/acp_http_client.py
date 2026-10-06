"""Client HTTP+SSE vers le serveur Node existant (server/acp-bridge.js).

Le Voice Gateway ne parle pas ACP directement : le serveur Node gère déjà
le spawn de l'agent et le cycle de vie des sessions (cf. VOICEGTWSPEC.md
§12, adapté ici car l'intégration ACP existe déjà côté Node). Ce client ne
fait que : POST /api/prompt (texte -> agent), GET /api/events en SSE
(réponse de l'agent en streaming), POST /api/cancel, et POST
/api/voice/event pour relayer l'état du Gateway vers l'UI web.
"""

from __future__ import annotations

import json
from typing import Callable, Iterator

import requests


class ACPHttpClient:
    def __init__(self, api_base: str, connection_id: str, session_id: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.connection_id = connection_id
        self.session_id = session_id

    def send_prompt(self, text: str) -> None:
        r = requests.post(
            f"{self.api_base}/api/prompt",
            json={"connectionId": self.connection_id, "sessionId": self.session_id, "text": text},
            timeout=10,
        )
        r.raise_for_status()

    def cancel(self) -> None:
        requests.post(
            f"{self.api_base}/api/cancel",
            json={"connectionId": self.connection_id, "sessionId": self.session_id},
            timeout=10,
        )

    def notify_event(self, event: str, data: dict) -> None:
        try:
            requests.post(
                f"{self.api_base}/api/voice/event",
                json={"connectionId": self.connection_id, "event": event, "data": data},
                timeout=5,
            )
        except requests.RequestException:
            pass  # best-effort : ne doit jamais interrompre la boucle vocale

    def iter_events(self) -> Iterator[tuple[str, dict]]:
        """Générateur bloquant sur le flux SSE /api/events. À lancer dans un thread dédié."""
        with requests.get(
            f"{self.api_base}/api/events",
            params={"connectionId": self.connection_id},
            stream=True,
            timeout=(10, None),
        ) as resp:
            resp.raise_for_status()
            event_name: str | None = None
            for raw_line in resp.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line.rstrip("\r")
                if line == "":
                    event_name = None
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:") :].strip()
                elif line.startswith("data:") and event_name:
                    payload = line[len("data:") :].strip()
                    try:
                        yield event_name, json.loads(payload)
                    except json.JSONDecodeError:
                        continue


def content_block_to_text(block) -> str:
    if block is None:
        return ""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            return block["text"]
        if isinstance(block.get("text"), str):
            return block["text"]
    return ""
