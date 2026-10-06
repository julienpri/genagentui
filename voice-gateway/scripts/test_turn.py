"""Test non-interactif d'un tour complet (sans PTT), en rejouant un WAV existant.

Valide que la chaîne STT -> Fake Agent -> Text Pipeline -> TTS -> Player
ne plante pas, avant de faire le test live avec micro.
"""

import asyncio
import sys
import time
import wave
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.fake_agent import FakeAgent
from voice_gateway.audio.player import Player
from voice_gateway.core.types import AudioFormat
from voice_gateway.stt.faster_whisper import WhisperSTT
from voice_gateway.stt.filter import TranscriptFilter
from voice_gateway.text.speakable import SpeakableFilter
from voice_gateway.tts.piper import PiperTTS
from main_voice import load_config, process_turn, JsonlLogger


class FakeAudioIO:
    """Simule AudioIO pour le test : fmt fixe, pas de vrai device."""

    def __init__(self, fmt: AudioFormat) -> None:
        self.fmt = fmt


async def main() -> None:
    cfg = load_config()
    wav_path = ROOT / "recordings" / "take_001.wav"
    with wave.open(str(wav_path), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
        fmt = AudioFormat(sample_rate=wf.getframerate(), channels=wf.getnchannels(), dtype="int16")

    audio = FakeAudioIO(fmt)
    player = Player()

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

    tts_cfg = dict(cfg["tts"])
    tts_cfg["model"] = str(ROOT / tts_cfg["model"])
    tts = PiperTTS()
    await tts.initialize(tts_cfg)

    agent = FakeAgent(word_delay_ms=cfg["fake_agent"]["word_delay_ms"])
    speakable = SpeakableFilter(
        code_blocks=cfg["text"]["speakable"]["code_blocks"],
        max_path_chars=cfg["text"]["speakable"]["max_path_chars"],
    )
    logger = JsonlLogger(ROOT / "logs" / "test_turn.jsonl")

    async def drain_player() -> None:
        # Simule le callback audio temps réel d'AudioIO (absent ici, FakeAudioIO
        # n'ouvre aucun flux) : vide le Player au même débit que la lecture réelle.
        chunk_bytes = int(fmt.sample_rate * 0.02) * 2  # ~20ms à 48kHz int16 mono
        while True:
            player.pull(chunk_bytes)
            await asyncio.sleep(0.02)

    drain_task = asyncio.create_task(drain_player())
    t_speech_end = time.monotonic()
    await process_turn(1, pcm, t_speech_end, audio, stt, transcript_filter, agent, speakable, tts, player, cfg["text"], logger)
    drain_task.cancel()

    print("player.is_idle() après le tour:", player.is_idle())

    await stt.shutdown()
    await tts.shutdown()
    logger.close()
    print("OK — aucune exception levée")


if __name__ == "__main__":
    asyncio.run(main())
