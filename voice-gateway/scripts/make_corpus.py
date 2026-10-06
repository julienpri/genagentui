"""Génère le corpus minimal de replay (§17.1) : silence, bruit, parole réelle."""

import shutil
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "corpus"
CORPUS.mkdir(parents=True, exist_ok=True)

SAMPLE_RATE = 48000  # mime le format natif de capture (cf. étape 1)


def write_wav(path: Path, samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.astype(np.int16).tobytes())


def main() -> None:
    rng = np.random.default_rng(42)

    silence = np.zeros(SAMPLE_RATE * 3, dtype=np.int16)
    write_wav(CORPUS / "silence.wav", silence)

    noise = (rng.standard_normal(SAMPLE_RATE * 3) * 300).astype(np.int16)  # bruit ambiant faible
    write_wav(CORPUS / "noise.wav", noise)

    real_speech = ROOT / "recordings" / "take_001.wav"
    if real_speech.exists():
        shutil.copy(real_speech, CORPUS / "speech_1.wav")
        print(f"copié {real_speech} -> {CORPUS / 'speech_1.wav'}")
    else:
        print(f"ATTENTION: {real_speech} absent, pas d'échantillon de parole réelle dans le corpus")

    print(f"Corpus généré dans {CORPUS}")


if __name__ == "__main__":
    main()
