"""Étape 1 (VOICEGTWSPEC.md §19) : AudioIO full-duplex + PTT clavier → WAV.

Critère de sortie : 50 enregistrements successifs sans fuite ni overflow.

Usage :
    python main.py                       # mode toggle (Entrée/Entrée), défaut
    python main.py --ptt-mode hold        # espace maintenue (pynput)
    python main.py --max-takes 50         # s'arrête après N prises (soak §19 étape 1/4)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

import yaml

from voice_gateway.audio.audio_io import AudioIO

ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    with open(ROOT / "config" / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_wav(path: Path, pcm: bytes, fmt) -> None:
    sampwidth = {"int16": 2, "int32": 4, "int8": 1}[fmt.dtype]
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(fmt.channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(fmt.sample_rate)
        wf.writeframes(pcm)


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


def duration_ms(pcm: bytes, fmt) -> int:
    sampwidth = {"int16": 2, "int32": 4, "int8": 1}[fmt.dtype]
    n_frames = len(pcm) / (sampwidth * fmt.channels)
    return int(1000 * n_frames / fmt.sample_rate)


def run_toggle(audio: AudioIO, on_take) -> None:
    take = 0
    while True:
        try:
            cmd = input(f"\n[prise {take + 1}] Entrée pour démarrer (q pour quitter) > ")
        except EOFError:
            break
        if cmd.strip().lower() == "q":
            break
        audio.begin_capture()
        try:
            input(f"[prise {take + 1}] ... enregistrement en cours, Entrée pour arrêter > ")
        except EOFError:
            pass
        pcm = audio.end_capture()
        take += 1
        if not on_take(take, pcm):
            break


def run_hold(audio: AudioIO, key_name: str, on_take) -> None:
    from pynput import keyboard

    target = getattr(keyboard.Key, key_name, None)
    take = 0

    def matches(key) -> bool:
        if target is not None:
            return key == target
        try:
            return key.char == key_name
        except AttributeError:
            return False

    def on_press(key):
        if key == keyboard.Key.esc:
            return False
        if matches(key) and not audio.is_capturing:
            audio.begin_capture()
            print(f"\n[prise {take + 1}] capture démarrée...")

    def on_release(key):
        nonlocal take
        if matches(key) and audio.is_capturing:
            pcm = audio.end_capture()
            take += 1
            if not on_take(take, pcm):
                return False

    print(f"Maintenez '{key_name}' pour parler, relâchez pour arrêter. Échap pour quitter.")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def main() -> int:
    parser = argparse.ArgumentParser(description="Voice Gateway — étape 1")
    parser.add_argument("--ptt-mode", choices=["toggle", "hold"], default=None)
    parser.add_argument("--ptt-key", default=None)
    parser.add_argument("--out-dir", default=str(ROOT / "recordings"))
    parser.add_argument("--max-takes", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config()
    ptt_mode = args.ptt_mode or cfg["ptt"]["mode"]
    ptt_key = args.ptt_key or cfg["ptt"]["key"]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger = JsonlLogger(ROOT / cfg["observability"]["log_file"])

    audio = AudioIO(frame_ms=cfg["audio"]["frame_ms"])
    audio.start()
    print(f"AudioIO démarré : {audio.fmt}")
    logger.log("state", **{"from": "IDLE", "to": "LISTENING"})

    session_start = time.monotonic()

    def on_take(take_idx: int, pcm: bytes) -> bool:
        dur = duration_ms(pcm, audio.fmt)
        path = out_dir / f"take_{take_idx:03d}.wav"
        write_wav(path, pcm, audio.fmt)
        print(
            f"[prise {take_idx}] {dur} ms -> {path.name}  "
            f"(overflow={audio.input_overflow_count}, underflow={audio.output_underflow_count})"
        )
        logger.log(
            "speech-end",
            take=take_idx,
            duration_ms=dur,
            input_overflow_count=audio.input_overflow_count,
            output_underflow_count=audio.output_underflow_count,
        )
        if args.max_takes and take_idx >= args.max_takes:
            print(f"\n{args.max_takes} prises atteintes, arrêt.")
            return False
        return True

    try:
        if ptt_mode == "hold":
            run_hold(audio, ptt_key, on_take)
        else:
            run_toggle(audio, on_take)
    except KeyboardInterrupt:
        print("\nInterrompu (Ctrl+C).")
    finally:
        audio.stop()
        logger.log("state", **{"from": "LISTENING", "to": "IDLE"})
        logger.close()
        elapsed = time.monotonic() - session_start
        print(
            f"\nSession terminée en {elapsed:.1f}s — "
            f"overflow={audio.input_overflow_count}, underflow={audio.output_underflow_count}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
