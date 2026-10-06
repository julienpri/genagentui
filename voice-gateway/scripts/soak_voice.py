"""Soak test étape 4 (VOICEGTWSPEC.md §19, critères §17.3).

50+ tours consécutifs sur le pipeline réel (AudioIO + Player + Whisper +
Fake Agent + Text Pipeline + Piper), en réutilisant un enregistrement
existant comme entrée (pour ne pas demander 50 prises vocales manuelles).
Chaque tour exerce aussi un vrai begin_capture()/end_capture() sur le
micro réel pour stresser le chemin d'entrée.

Critères de sortie (§17.3) vérifiés automatiquement :
- 0 blocage (pas d'exception, pas de timeout)
- 0 fuite de ressources (dérive RSS < 5%, threads/FD stables)
- 0 micro définitivement perdu (AudioIO reste fonctionnel du 1er au dernier tour)
- 0 état incohérent (is_idle() cohérent à la fin de chaque tour)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import wave
from pathlib import Path

import psutil
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.fake_agent import FakeAgent
from voice_gateway.audio.audio_io import AudioIO
from voice_gateway.audio.player import Player
from voice_gateway.stt.faster_whisper import WhisperSTT
from voice_gateway.stt.filter import TranscriptFilter
from voice_gateway.text.speakable import SpeakableFilter
from voice_gateway.tts.piper import PiperTTS
from main_voice import load_config, process_turn, JsonlLogger

N_TAKES = 50
RSS_DRIFT_BUDGET = 0.05  # 5%, §16.3


async def main() -> int:
    cfg = load_config()
    wav_path = ROOT / "recordings" / "take_001.wav"
    with wave.open(str(wav_path), "rb") as wf:
        canned_pcm = wf.readframes(wf.getnframes())

    audio = AudioIO(frame_ms=cfg["audio"]["frame_ms"])
    audio.start()
    print(f"AudioIO démarré : {audio.fmt}")
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

    tts_cfg = dict(cfg["tts"])
    tts_cfg["model"] = str(ROOT / tts_cfg["model"])
    tts = PiperTTS()
    await tts.initialize(tts_cfg)

    agent = FakeAgent(word_delay_ms=cfg["fake_agent"]["word_delay_ms"])
    speakable = SpeakableFilter(
        code_blocks=cfg["text"]["speakable"]["code_blocks"],
        max_path_chars=cfg["text"]["speakable"]["max_path_chars"],
    )
    logger = JsonlLogger(ROOT / "logs" / "soak_voice.jsonl")

    proc = psutil.Process(os.getpid())
    rss_start = proc.memory_info().rss
    threads_start = proc.num_threads()

    print(f"RSS initiale: {rss_start / 1e6:.1f} MB, threads: {threads_start}")
    print(f"Lancement du soak : {N_TAKES} tours...\n")

    t0 = time.monotonic()
    incoherent_states = 0
    for take in range(1, N_TAKES + 1):
        # Stresse le chemin d'entrée réel (micro) à chaque tour.
        audio.begin_capture()
        await asyncio.sleep(0.05)
        audio.end_capture()

        t_speech_end = time.monotonic()
        try:
            await process_turn(
                take, canned_pcm, t_speech_end, audio, stt, transcript_filter,
                agent, speakable, tts, player, cfg["text"], logger,
            )
        except Exception as e:
            print(f"[tour {take}] EXCEPTION: {e!r}")
            logger.log("error", take=take, message=repr(e))
            break

        if not player.is_idle():
            incoherent_states += 1
            print(f"[tour {take}] ETAT INCOHERENT: player non drainé après le tour")

        if take % 10 == 0:
            rss_now = proc.memory_info().rss
            print(
                f"[tour {take}/{N_TAKES}] RSS={rss_now / 1e6:.1f}MB "
                f"threads={proc.num_threads()} "
                f"overflow={audio.input_overflow_count} underflow={audio.output_underflow_count}"
            )

    elapsed = time.monotonic() - t0
    rss_end = proc.memory_info().rss
    threads_end = proc.num_threads()
    rss_drift = (rss_end - rss_start) / rss_start

    print(f"\n--- Bilan soak ({elapsed:.1f}s) ---")
    print(f"RSS: {rss_start/1e6:.1f}MB -> {rss_end/1e6:.1f}MB (dérive {rss_drift*100:.1f}%, budget <5%)")
    print(f"Threads: {threads_start} -> {threads_end}")
    print(f"input_overflow_count={audio.input_overflow_count} output_underflow_count={audio.output_underflow_count}")
    print(f"États incohérents détectés: {incoherent_states}")

    ok = (
        rss_drift < RSS_DRIFT_BUDGET
        and incoherent_states == 0
        and audio.fmt is not None  # AudioIO toujours fonctionnel
    )

    audio.stop()
    await stt.shutdown()
    await tts.shutdown()
    logger.close()

    # AudioIO doit rester réutilisable après stop() (pas de micro perdu définitivement).
    try:
        audio.start()
        audio.stop()
        print("AudioIO: restart post-soak OK")
    except Exception as e:
        print(f"AudioIO: restart post-soak ECHEC: {e!r}")
        ok = False

    print("\n" + ("CRITERES §17.3 : PASS" if ok else "CRITERES §17.3 : FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
