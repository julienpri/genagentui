"""Harness replay STT (§17.1) : corpus WAV -> Resampler -> STT -> Filter.

Critère de sortie étape 2 (§19) : corpus replay transcrit, 0 transcript
fantôme sur silence/bruit.
"""

import asyncio
import sys
import wave
from pathlib import Path

import yaml

from voice_gateway.audio.resampler import resample_pcm
from voice_gateway.core.cancel import CancelToken
from voice_gateway.core.types import AudioFormat
from voice_gateway.stt.faster_whisper import WhisperSTT
from voice_gateway.stt.filter import TranscriptFilter

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus"

# fichier -> doit produire un transcript accepté (True) ou non (False)
EXPECTATIONS = {
    "silence.wav": False,
    "noise.wav": False,
    "speech_1.wav": True,
}


def read_wav(path: Path) -> tuple[bytes, AudioFormat]:
    with wave.open(str(path), "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
        fmt = AudioFormat(sample_rate=wf.getframerate(), channels=wf.getnchannels(), dtype="int16")
    return pcm, fmt


async def main() -> int:
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    stt_cfg = dict(cfg["stt"])
    stt_cfg["model"] = str(ROOT / stt_cfg["model"])

    stt = WhisperSTT()
    print(f"Chargement du modèle {stt_cfg['model']} ...")
    await stt.initialize(stt_cfg)

    filt = TranscriptFilter(
        max_no_speech_prob=stt_cfg["filter"]["max_no_speech_prob"],
        min_avg_logprob=stt_cfg["filter"]["min_avg_logprob"],
        max_compression_ratio=stt_cfg["filter"]["max_compression_ratio"],
        min_chars=stt_cfg["filter"]["min_chars"],
        blacklist_file=ROOT / stt_cfg["filter"]["blacklist_file"],
    )

    all_ok = True
    for wav_path in sorted(CORPUS.glob("*.wav")):
        pcm, fmt = read_wav(wav_path)
        internal_pcm = resample_pcm(pcm, fmt)
        internal_fmt = AudioFormat(sample_rate=16000, channels=1, dtype="int16")

        transcript = await stt.transcribe(internal_pcm, internal_fmt, CancelToken())
        result = filt.check(transcript)

        expected = EXPECTATIONS.get(wav_path.name)
        status = "OK" if (expected is None or result.accepted == expected) else "FAIL"
        if status == "FAIL":
            all_ok = False

        print(
            f"[{status}] {wav_path.name:16s} accepted={result.accepted!s:5s} "
            f"reason={result.reason} text={transcript.text!r} "
            f"no_speech_prob={transcript.no_speech_prob} avg_logprob={transcript.avg_logprob} "
            f"compression_ratio={transcript.compression_ratio} stt_latency_ms={transcript.stt_latency_ms}"
        )

    await stt.shutdown()
    print("\n" + ("TOUS LES TESTS PASSENT" if all_ok else "ECHECS DETECTES"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
