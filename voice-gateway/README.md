# Voice Gateway

Implémentation des étapes 1 à 4 de [`VOICEGTWSPEC.md`](../VOICEGTWSPEC.md) : chaîne
vocale locale autonome (capture → STT → agent de test → synthèse → lecture),
indépendante de Kilo/ACP/UI web pour l'instant.

```
🎙 Micro → PTT → Whisper local → Fake Agent (stream) → Speakable + Segmenter → Piper → 🔊
```

Statut : étapes 1 à 4 validées (voir §19 de la spec). Pas encore implémenté : VAD,
barge-in, AEC, intégration ACP/Kilo, UI web (étapes 5+).

## Installation

Nécessite Python ≥ 3.11. Sur cette machine, le Python 3.14 Homebrew a une lib
`expat` cassée (`ensurepip` plante) — utiliser Python 3.12 à la place.

```bash
cd voice-gateway
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .
```

Dépendances (`pyproject.toml`) : `sounddevice`, `numpy`, `pynput`, `pyyaml`,
`faster-whisper`, `soxr`, `piper-tts`, `psutil`.

## Modèles utilisés

Les modèles ne sont **pas committés** (gros binaires, hors périmètre du repo —
cf. spec §3.2 : *« le téléchargement des modèles est hors périmètre »*). À
télécharger une fois en local avant utilisation, dans `models/` (ignoré par git).

### STT — faster-whisper `small`

Modèle CTranslate2 `Systran/faster-whisper-small` (français, CPU, `int8`).

```bash
.venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download('Systran/faster-whisper-small', local_dir='models/faster-whisper-small')
"
```

→ `models/faster-whisper-small/`

Latence mesurée : ~1.0-1.3s pour un segment de ~3s sur CPU (Apple M-series),
au-dessus du budget indicatif de la spec (<700ms, §16.3). Si besoin de
descendre la latence : modèle `tiny`/`base` à la place de `small`.

### TTS — Piper voix française `siwis` (medium)

Moteur [`piper-tts`](https://github.com/OHF-voice/piper1-gpl) (réécriture
Python/onnxruntime de Piper, pas de dépendance C++ exotique). Voix
`rhasspy/piper-voices` → `fr/fr_FR/siwis/medium`.

```bash
.venv/bin/python -c "
from huggingface_hub import hf_hub_download
for f in ['fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx',
          'fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json']:
    hf_hub_download('rhasspy/piper-voices', f, local_dir='models/piper')
"
```

→ `models/piper/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx(.json)`

Latence mesurée : ~100-150ms premier chunk (budget <250ms, §16.3 — OK).

Les chemins de modèles sont référencés dans `config/config.yaml` (`stt.model`,
`tts.model`), relatifs à `voice-gateway/`.

## Utilisation

```bash
# Étape 1 : PTT clavier -> WAV (pas de STT/TTS)
.venv/bin/python main.py --ptt-mode toggle        # Entrée/Entrée, zéro permission
.venv/bin/python main.py --ptt-mode hold           # espace maintenue (pynput, requiert
                                                     # la permission Accessibilité macOS)

# Étape 2 : harness replay STT sur corpus (silence/bruit/parole)
.venv/bin/python scripts/make_corpus.py             # génère tests/corpus/
.venv/bin/python scripts/replay_stt.py

# Étape 3 : tour complet interactif (STT -> Fake Agent -> TTS -> haut-parleur)
.venv/bin/python main_voice.py

# Étape 4 : soak 50 tours (rejoue un enregistrement existant, stresse le pipeline réel)
.venv/bin/python scripts/soak_voice.py
```

## Architecture

```
voice_gateway/
├── core/types.py, cancel.py        # AudioFormat, AudioChunk, Transcript, CancelToken
├── audio/audio_io.py               # flux full-duplex sounddevice (§6.1)
├── audio/resampler.py              # vers format interne 16kHz mono int16 (§6.2)
├── audio/player.py                 # file de lecture (§6.9)
├── stt/faster_whisper.py, filter.py # STT + filtre anti-hallucination (§6.5, §6.6)
├── text/segmenter.py, speakable.py # Text Pipeline (§6.7)
└── tts/piper.py                    # synthèse streaming (§6.8)

controller/fake_agent.py            # Fake Agent de test (§4.1), à remplacer par ACP plus tard
main.py                             # étape 1
main_voice.py                       # étapes 3/4 (tour complet)
scripts/                            # harnesses de test (corpus, replay, soak)
```

Le package `voice_gateway` n'importe jamais `controller` (cf. spec §18).

## Tests effectués

- **Étape 1** : 50 cycles start/stop automatisés (`scripts/soak_toggle.py`) +
  validation live au micro réel. 0 overflow/underflow.
- **Étape 2** : `scripts/replay_stt.py` sur silence/bruit/parole réelle — 0
  transcript fantôme, transcription correcte.
- **Étape 3** : tour complet validé en live (micro + haut-parleurs réels).
- **Étape 4** : `scripts/soak_voice.py`, 50 tours sur le pipeline réel — PASS
  (RSS stable, 0 état incohérent, micro jamais perdu). Détail dans
  `logs/soak_voice.jsonl` (non committé).
