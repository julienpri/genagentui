"""AudioIO full-duplex (VOICEGTWSPEC.md §6.1).

Un seul flux sounddevice.Stream (entrée+sortie) ouvert pour toute la
session. Le callback ne fait aucun traitement lourd : il copie les
frames d'entrée dans un buffer si une capture est active, et tire les
frames de sortie depuis le Player (silence si aucun Player attaché ou
file vide).
"""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd

from voice_gateway.audio.player import Player
from voice_gateway.core.types import AudioFormat

_NUMPY_DTYPE = {"int16": np.int16, "int32": np.int32, "int8": np.int8, "float32": np.float32}


class AudioIO:
    def __init__(
        self,
        channels: int = 1,
        dtype: str = "int16",
        frame_ms: int = 20,
        samplerate: int | None = None,
        input_device: str | int | None = None,
        output_device: str | int | None = None,
    ) -> None:
        self.channels = channels
        self.dtype = dtype
        self.frame_ms = frame_ms
        self.samplerate = samplerate
        self.input_device = input_device
        self.output_device = output_device

        self._stream: sd.Stream | None = None
        self._capturing = threading.Event()
        self._lock = threading.Lock()
        self._capture_buf = bytearray()

        self.input_overflow_count = 0
        self.output_underflow_count = 0
        self.fmt: AudioFormat | None = None
        self.player: Player | None = None

    # -- lifecycle ---------------------------------------------------

    def start(self) -> None:
        if self._stream is not None:
            return  # no-op idempotent

        device = (self.input_device, self.output_device)
        samplerate = self.samplerate
        if samplerate is None:
            # Entrée et sortie ont souvent des samplerates par défaut différents
            # (ex. micro 48kHz / haut-parleurs 44.1kHz) ; un flux full-duplex
            # unique impose un samplerate commun (cf. §6.1) — on force celui
            # du device d'entrée, la qualité de capture important le plus ici.
            in_idx = self.input_device if self.input_device is not None else sd.default.device[0]
            samplerate = int(sd.query_devices(in_idx)["default_samplerate"])

        blocksize = 0  # laisse sounddevice/PortAudio choisir
        self._stream = sd.Stream(
            device=device,
            samplerate=samplerate,
            channels=self.channels,
            dtype=self.dtype,
            callback=self._callback,
            blocksize=blocksize,
        )
        self._stream.start()

        frame_samples = int(self._stream.samplerate * self.frame_ms / 1000)
        self.fmt = AudioFormat(
            sample_rate=int(self._stream.samplerate),
            channels=self.channels,
            dtype=self.dtype,
        )
        self._frame_samples = frame_samples

    def stop(self) -> None:
        if self._stream is None:
            return  # no-op idempotent
        self._stream.stop()
        self._stream.close()
        self._stream = None

    # -- capture -------------------------------------------------------

    def begin_capture(self) -> None:
        with self._lock:
            self._capture_buf = bytearray()
        self._capturing.set()

    def end_capture(self) -> bytes:
        self._capturing.clear()
        with self._lock:
            data = bytes(self._capture_buf)
            self._capture_buf = bytearray()
        return data

    @property
    def is_capturing(self) -> bool:
        return self._capturing.is_set()

    # -- internals -----------------------------------------------------

    def _callback(self, indata, outdata, frames, time_info, status) -> None:
        if status.input_overflow:
            self.input_overflow_count += 1
        if status.output_underflow:
            self.output_underflow_count += 1

        if self.player is not None:
            n_bytes = outdata.nbytes
            pcm = self.player.pull(n_bytes)
            outdata[:] = np.frombuffer(pcm, dtype=_NUMPY_DTYPE[self.dtype]).reshape(outdata.shape)
        else:
            outdata.fill(0)

        if self._capturing.is_set():
            with self._lock:
                self._capture_buf += indata.tobytes()
