"""Intégration à l'UI web (VOICEGTWSPEC.md §12, adapté : le serveur Node
existant tient déjà le rôle ACP du Controller — ce process ne fait que
ponter PTT+voix <-> REST/SSE de ce serveur).

Spawné par server/acp-bridge.js (startVoice) avec :
    web_session.py --api-base http://127.0.0.1:PORT --connection-id ID --session-id ID

Micro → PTT (espace maintenue) → Whisper local → [POST /api/prompt] → agent
     ← [SSE /api/events: session_update, status] ← Speakable + Segmenter ← Piper → haut-parleur

Un seul tour actif à la fois (§9) : un nouveau PTT pendant qu'un tour est en
cours est ignoré (pas de barge-in dans cette première intégration).
"""

from __future__ import annotations

import argparse
import asyncio
import queue
import signal
import sys
import threading
import time
from pathlib import Path

import yaml
from pynput import keyboard

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.acp_http_client import ACPHttpClient, content_block_to_text  # noqa: E402
from voice_gateway.audio.audio_io import AudioIO  # noqa: E402
from voice_gateway.audio.player import Player  # noqa: E402
from voice_gateway.audio.resampler import internal_format, resample_pcm  # noqa: E402
from voice_gateway.core.cancel import CancelToken  # noqa: E402
from voice_gateway.stt.faster_whisper import WhisperSTT  # noqa: E402
from voice_gateway.stt.filter import TranscriptFilter  # noqa: E402
from voice_gateway.text.pipeline import speak_sentence  # noqa: E402
from voice_gateway.text.segmenter import SentenceSegmenter  # noqa: E402
from voice_gateway.text.speakable import SpeakableFilter  # noqa: E402
from voice_gateway.tts.piper import PiperTTS  # noqa: E402
from voice_gateway.tts.say import SayTTS  # noqa: E402


def load_config() -> dict:
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class Turn:
    """État du tour en cours (un seul à la fois, cf. §9)."""

    def __init__(self, segmenter: SentenceSegmenter, t_speech_end: float) -> None:
        self.segmenter = segmenter
        self.t_speech_end = t_speech_end
        self.t_agent_start = time.monotonic()
        self.t_first_agent_chunk: float | None = None
        self.t_first_sentence: float | None = None
        self.timers: dict = {}
        self.active = True


def sse_reader_thread(client: ACPHttpClient, out_queue: "queue.Queue") -> None:
    """Tourne dans un thread dédié (requests.iter_lines est bloquant). Reconnecte en cas d'erreur."""
    while True:
        try:
            for event_name, data in client.iter_events():
                out_queue.put((event_name, data))
        except Exception as e:  # noqa: BLE001 - best-effort, on reconnecte
            out_queue.put(("__sse_error__", {"message": repr(e)}))
        time.sleep(2)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Voice Gateway — session web (intégration ACP via serveur Node)")
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--connection-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--tts-provider", choices=["piper", "say", "browser"], default=None)
    args = parser.parse_args()

    cfg = load_config()
    tts_provider = args.tts_provider or cfg["tts"]["provider"]
    client = ACPHttpClient(args.api_base, args.connection_id, args.session_id)

    audio = AudioIO(frame_ms=cfg["audio"]["frame_ms"])
    audio.start()
    player = Player()
    audio.player = player

    stt_cfg = dict(cfg["stt"])
    stt_cfg["model"] = str(ROOT / stt_cfg["model"])
    stt = WhisperSTT()
    await stt.initialize(stt_cfg)

    transcript_filter = TranscriptFilter(
        max_no_speech_prob=stt_cfg["filter"]["max_no_speech_prob"],
        min_avg_logprob=stt_cfg["filter"]["min_avg_logprob"],
        max_compression_ratio=stt_cfg["filter"]["max_compression_ratio"],
        min_chars=stt_cfg["filter"]["min_chars"],
        blacklist_file=ROOT / stt_cfg["filter"]["blacklist_file"],
    )

    tts = None
    if tts_provider == "piper":
        tts_cfg = dict(cfg["tts"])
        tts_cfg["model"] = str(ROOT / tts_cfg["model"])
        tts = PiperTTS()
        await tts.initialize(tts_cfg)
    elif tts_provider == "say":
        tts = SayTTS()
        await tts.initialize(dict(cfg["tts"]["say"]))
    # "browser" : pas de synthèse locale, le texte est juste relayé au
    # navigateur (voir handle_sentence) qui parle via speechSynthesis.

    speakable = SpeakableFilter(
        code_blocks=cfg["text"]["speakable"]["code_blocks"],
        max_path_chars=cfg["text"]["speakable"]["max_path_chars"],
    )
    text_cfg = cfg["text"]

    loop = asyncio.get_running_loop()
    current_turn: Turn | None = None

    def notify(event: str, data: dict) -> None:
        client.notify_event(event, data)

    async def finish_turn(turn: Turn) -> None:
        tail = turn.segmenter.flush()
        if tail:
            if turn.t_first_sentence is None:
                turn.t_first_sentence = time.monotonic()
            await handle_sentence(turn, tail)

        while not player.is_idle():
            await asyncio.sleep(0.02)

        t_playback_end = time.monotonic()
        t_playback_start = turn.timers.get("playback_start_t", t_playback_end)
        notify(
            "playback-end",
            {
                "perceived_latency_ms": int((t_playback_start - turn.t_speech_end) * 1000),
                "playback_duration_ms": int((t_playback_end - t_playback_start) * 1000),
            },
        )
        turn.active = False

    async def handle_sentence(turn: Turn, raw_sentence: str) -> None:
        if tts_provider == "browser":
            # Pas de synthèse locale : le navigateur reçoit le texte et
            # parle lui-même via speechSynthesis (cf. public/app.js).
            text = speakable.process(raw_sentence)
            if not text:
                return
            notify("synthesis-start", {"text": text})
            turn.timers.setdefault("playback_start_t", time.monotonic())
            return

        notify("synthesis-start", {"text": raw_sentence})
        await speak_sentence(raw_sentence, speakable, tts, player, audio.fmt.sample_rate, turn.timers)
        notify("synthesis-end", {"text": raw_sentence})

    async def on_sse_event(event_name: str, data: dict) -> None:
        nonlocal current_turn

        if event_name == "__sse_error__":
            notify("error", {"code": "sse-disconnected", "message": data.get("message", ""), "recoverable": True})
            return

        if data.get("sessionId") != args.session_id:
            return

        if event_name == "session_update":
            update = data.get("update") or {}
            if update.get("sessionUpdate") != "agent_message_chunk":
                return
            if current_turn is None or not current_turn.active:
                return
            text = content_block_to_text(update.get("content"))
            if not text:
                return
            if current_turn.t_first_agent_chunk is None:
                current_turn.t_first_agent_chunk = time.monotonic()
            for sentence in current_turn.segmenter.push(text):
                if current_turn.t_first_sentence is None:
                    current_turn.t_first_sentence = time.monotonic()
                await handle_sentence(current_turn, sentence)

        elif event_name == "status":
            state = data.get("state")
            if state in ("completed", "cancelled", "failed") and current_turn and current_turn.active:
                await finish_turn(current_turn)

    async def handle_voice_turn(pcm: bytes) -> None:
        nonlocal current_turn
        t_speech_end = time.monotonic()

        internal_pcm = resample_pcm(pcm, audio.fmt)
        transcript = await stt.transcribe(internal_pcm, internal_format(), CancelToken())

        result = transcript_filter.check(transcript)
        if not result.accepted:
            notify("transcript-rejected", {"reason": result.reason, "text": transcript.text})
            notify("state", {"from": "TRANSCRIBING", "to": "LISTENING"})
            return

        notify("transcript", {"text": transcript.text, "stt_latency_ms": transcript.stt_latency_ms})

        segmenter = SentenceSegmenter(max_sentence_chars=text_cfg["segmenter"]["max_sentence_chars"])
        current_turn = Turn(segmenter, t_speech_end)

        try:
            await asyncio.to_thread(client.send_prompt, transcript.text)
        except Exception as e:  # noqa: BLE001
            notify("error", {"code": "prompt-failed", "message": repr(e), "recoverable": True})
            current_turn.active = False
            return

        notify("state", {"from": "TRANSCRIBING", "to": "SPEAKING"})

    # -- PTT (espace maintenue) -----------------------------------------

    def on_press(key) -> None:
        if key == keyboard.Key.space and not audio.is_capturing:
            if current_turn is not None and current_turn.active:
                return  # un tour est déjà en cours (§9) : ignoré, pas de barge-in en v1
            audio.begin_capture()
            notify("state", {"from": "LISTENING", "to": "CAPTURING"})

    def on_release(key) -> None:
        if key == keyboard.Key.space and audio.is_capturing:
            pcm = audio.end_capture()
            notify("state", {"from": "CAPTURING", "to": "TRANSCRIBING"})
            asyncio.run_coroutine_threadsafe(handle_voice_turn(pcm), loop)

    ptt_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    ptt_listener.start()

    # -- Pont SSE (thread dédié -> queue thread-safe -> boucle asyncio) --

    sse_queue: "queue.Queue" = queue.Queue()
    threading.Thread(target=sse_reader_thread, args=(client, sse_queue), daemon=True).start()

    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    notify("state", {"from": "IDLE", "to": "LISTENING"})
    print("Voice Gateway (mode web) prêt — espace maintenue pour parler.", flush=True)

    async def sse_pump() -> None:
        while not stop_event.is_set():
            item = await asyncio.to_thread(sse_queue.get)
            await on_sse_event(*item)

    pump_task = asyncio.create_task(sse_pump())
    await stop_event.wait()

    pump_task.cancel()
    ptt_listener.stop()
    audio.stop()
    await stt.shutdown()
    if tts is not None:
        await tts.shutdown()
    notify("state", {"from": "LISTENING", "to": "IDLE"})
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
