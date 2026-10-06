"""Étape 3 (VOICEGTWSPEC.md §19) : tour complet audible.

Micro → PTT → Whisper local → Fake Agent (stream) → Speakable + Segmenter
→ Piper → haut-parleur.

Critère de sortie : tour complet audible, perceived_latency dans le budget.

Usage :
    python main_voice.py                  # mode toggle (Entrée/Entrée)
    python main_voice.py --max-takes 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import yaml

from controller.fake_agent import FakeAgent
from voice_gateway.audio.audio_io import AudioIO
from voice_gateway.audio.player import Player
from voice_gateway.audio.resampler import internal_format, resample_pcm, resample_to
from voice_gateway.core.cancel import CancelToken
from voice_gateway.stt.faster_whisper import WhisperSTT
from voice_gateway.stt.filter import TranscriptFilter
from voice_gateway.text.segmenter import SentenceSegmenter
from voice_gateway.text.speakable import SpeakableFilter
from voice_gateway.tts.piper import PiperTTS

ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class JsonlLogger:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")

    def log(self, event: str, **data) -> None:
        rec = {"type": f"voice.{event}", "t_mono": time.monotonic(), "data": data}
        self._f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


async def process_turn(
    take: int,
    pcm: bytes,
    t_speech_end: float,
    audio: AudioIO,
    stt: WhisperSTT,
    transcript_filter: TranscriptFilter,
    agent: FakeAgent,
    speakable: SpeakableFilter,
    tts: PiperTTS,
    player: Player,
    text_cfg: dict,
    logger: JsonlLogger,
) -> None:
    internal_pcm = resample_pcm(pcm, audio.fmt)
    transcript = await stt.transcribe(internal_pcm, internal_format(), CancelToken())

    result = transcript_filter.check(transcript)
    if not result.accepted:
        print(f"[tour {take}] transcript rejeté ({result.reason}): {transcript.text!r}")
        logger.log("transcript-rejected", take=take, reason=result.reason, text=transcript.text)
        return

    print(f"[tour {take}] transcript ({transcript.stt_latency_ms} ms): {transcript.text!r}")
    logger.log("transcript", take=take, text=transcript.text, stt_latency_ms=transcript.stt_latency_ms)

    segmenter = SentenceSegmenter(max_sentence_chars=text_cfg["segmenter"]["max_sentence_chars"])
    out_fmt = audio.fmt

    t_agent_start = time.monotonic()
    t_first_agent_chunk: float | None = None
    t_first_sentence: float | None = None
    t_first_tts_chunk: float | None = None
    t_playback_start: float | None = None

    async def handle_sentence(raw_sentence: str) -> None:
        nonlocal t_first_tts_chunk, t_playback_start
        text = speakable.process(raw_sentence)
        if not text:
            return
        t_sentence_ready = time.monotonic()
        logger.log("synthesis-start", take=take, text=text)
        first_chunk = True
        async for chunk in tts.synthesize(text, CancelToken()):
            if first_chunk:
                t_first_tts_chunk = t_first_tts_chunk or time.monotonic()
                first_chunk = False
            out_pcm = resample_to(chunk.pcm, chunk.fmt, out_fmt.sample_rate)
            player.enqueue(out_pcm)
            if t_playback_start is None:
                # Laisse la main à la boucle asyncio jusqu'à ce que le
                # callback audio ait réellement consommé ce chunk (sinon la
                # course est perdue quand une phrase ne tient qu'en un seul
                # chunk : on vérifierait player.started avant qu'il bascule).
                for _ in range(200):  # ~1s max
                    if player.started:
                        t_playback_start = time.monotonic()
                        break
                    await asyncio.sleep(0.005)
        logger.log(
            "synthesis-end",
            take=take,
            text=text,
            latency_ms=int((time.monotonic() - t_sentence_ready) * 1000),
        )

    async for word in agent.respond(transcript.text):
        if t_first_agent_chunk is None:
            t_first_agent_chunk = time.monotonic()
        for sentence in segmenter.push(word):
            if t_first_sentence is None:
                t_first_sentence = time.monotonic()
            await handle_sentence(sentence)

    tail = segmenter.flush()
    if tail:
        if t_first_sentence is None:
            t_first_sentence = time.monotonic()
        await handle_sentence(tail)

    while not player.is_idle():
        await asyncio.sleep(0.02)
    t_playback_end = time.monotonic()

    if t_playback_start is None:
        t_playback_start = t_playback_end  # rien synthétisé (texte vide après filtrage)

    metrics = {
        "stt_latency_ms": transcript.stt_latency_ms,
        "agent_ttft_ms": int((t_first_agent_chunk - t_agent_start) * 1000) if t_first_agent_chunk else None,
        "first_sentence_ms": (
            int((t_first_sentence - t_first_agent_chunk) * 1000) if (t_first_sentence and t_first_agent_chunk) else None
        ),
        "tts_first_chunk_ms": (
            int((t_first_tts_chunk - t_first_sentence) * 1000) if (t_first_tts_chunk and t_first_sentence) else None
        ),
        "perceived_latency_ms": int((t_playback_start - t_speech_end) * 1000),
        "playback_duration_ms": int((t_playback_end - t_playback_start) * 1000),
    }
    print(f"[tour {take}] playback-start → playback-end : {metrics}")
    logger.log("playback-end", take=take, **metrics)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Voice Gateway — étape 3 (tour complet)")
    parser.add_argument("--max-takes", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config()

    logger = JsonlLogger(ROOT / cfg["observability"]["log_file"])

    audio = AudioIO(frame_ms=cfg["audio"]["frame_ms"])
    audio.start()
    print(f"AudioIO démarré : {audio.fmt}")

    player = Player()
    audio.player = player

    stt_cfg = dict(cfg["stt"])
    stt_cfg["model"] = str(ROOT / stt_cfg["model"])
    stt = WhisperSTT()
    print("Chargement du modèle STT...")
    await stt.initialize(stt_cfg)

    transcript_filter = TranscriptFilter(
        max_no_speech_prob=stt_cfg["filter"]["max_no_speech_prob"],
        min_avg_logprob=stt_cfg["filter"]["min_avg_logprob"],
        max_compression_ratio=stt_cfg["filter"]["max_compression_ratio"],
        min_chars=stt_cfg["filter"]["min_chars"],
        blacklist_file=ROOT / stt_cfg["filter"]["blacklist_file"],
    )

    tts_cfg = dict(cfg["tts"])
    tts_cfg["model"] = str(ROOT / tts_cfg["model"])
    tts = PiperTTS()
    print("Chargement du modèle TTS...")
    await tts.initialize(tts_cfg)

    agent = FakeAgent(word_delay_ms=cfg["fake_agent"]["word_delay_ms"])
    speakable = SpeakableFilter(
        code_blocks=cfg["text"]["speakable"]["code_blocks"],
        max_path_chars=cfg["text"]["speakable"]["max_path_chars"],
    )

    print("Prêt. Entrée pour parler, Entrée pour arrêter, q pour quitter.\n")

    take = 0
    try:
        while True:
            cmd = await asyncio.to_thread(input, f"[tour {take + 1}] Entrée pour démarrer (q pour quitter) > ")
            if cmd.strip().lower() == "q":
                break
            audio.begin_capture()
            await asyncio.to_thread(input, f"[tour {take + 1}] ... parlez, Entrée pour arrêter > ")
            pcm = audio.end_capture()
            t_speech_end = time.monotonic()
            take += 1

            await process_turn(
                take, pcm, t_speech_end, audio, stt, transcript_filter, agent, speakable, tts, player, cfg["text"], logger
            )

            if args.max_takes and take >= args.max_takes:
                print(f"\n{args.max_takes} tours atteints, arrêt.")
                break
    except KeyboardInterrupt:
        print("\nInterrompu (Ctrl+C).")
    finally:
        await stt.shutdown()
        await tts.shutdown()
        audio.stop()
        logger.close()
        print(f"\nSession terminée — {take} tours.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
