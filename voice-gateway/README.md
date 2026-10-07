# Voice Gateway

Implémentation des étapes 1 à 4 de [`VOICEGTWSPEC.md`](../VOICEGTWSPEC.md) : chaîne
vocale locale autonome (capture → STT → agent de test → synthèse → lecture),
indépendante de Kilo/ACP/UI web pour l'instant.

```
🎙 Micro → PTT → Whisper local → Fake Agent (stream) → Speakable + Segmenter → Piper → 🔊
```

Statut : étapes 1 à 4 validées (voir §19 de la spec), **plus une intégration v0 à
l'UI web** du projet parent (`../public/`, `../server/`) — bouton "Mode vocal"
dans le chat, voir [`controller/`](#intégration-web-v0) ci-dessous. Pas encore
implémenté : VAD, barge-in, AEC (étapes 5+).

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

## Intégration web (v0)

Branche la Gateway sur l'UI de chat du projet parent plutôt qu'un Fake Agent.
N'est pas lancé à la main : le serveur Node (`../server/acp-bridge.js`) spawn
`controller/web_session.py` quand on clique "🎤 Mode vocal" dans le navigateur,
avec `--connection-id`/`--session-id` de la session ACP en cours.

```
controller/web_session.py       PTT -> STT -> POST /api/prompt -> écoute SSE
                                 (agent_message_chunk/status) -> Segmenter ->
                                 Speakable -> Piper -> haut-parleur
controller/acp_http_client.py   client HTTP+SSE du serveur Node existant
                                 (pas de nouvelle dépendance ACP : le serveur
                                 Node gère déjà le spawn de l'agent et les
                                 sessions, cf. ../server/acp-bridge.js)
```

Le serveur Node n'autorise qu'une seule instance de Voice Gateway à la fois
(propriété unique du micro/haut-parleur physiques, §6.1) : démarrer une
nouvelle session vocale tue automatiquement la précédente.

Un seul tour actif à la fois (§9) : un nouveau PTT pendant qu'un tour est en
cours est ignoré — pas de barge-in dans cette v0.

### Choix du rendu vocal (TTS)

Un `<select>` dans l'UI (à côté de "🎤 Mode vocal") choisit comment la
réponse de l'agent est rendue — la capture (PTT + Whisper) ne change pas,
seule la synthèse change :

| Valeur | Implémentation | Où ça tourne |
|---|---|---|
| `piper` (défaut) | `voice_gateway/tts/piper.py` | localement, dans le process Python |
| `say` | `voice_gateway/tts/say.py` (commande macOS `say`, PCM direct via `--data-format=LEI16@<rate>`, lu avec le module stdlib `wave`) | localement, dans le process Python |
| `browser` | aucune synthèse côté Python — le texte est relayé via `voice_event`/`synthesis-start` et c'est `speechSynthesis.speak()` côté navigateur qui parle | dans l'onglet |

Le choix est figé pour la durée d'une session vocale (passé en argument au
spawn du process, `--tts-provider`) ; le `<select>` se désactive pendant
qu'une session est active.

Testé bout-en-bout avec un agent ACP réel (opencode) : prompt -> réponse
réelle streamée -> synthèse -> lecture, sans crash, arrêt propre. Le round-trip
micro réel (PTT + voix humaine côté navigateur) a été validé manuellement.

## Architecture

```
voice_gateway/
├── core/types.py, cancel.py        # AudioFormat, AudioChunk, Transcript, CancelToken
├── audio/audio_io.py               # flux full-duplex sounddevice (§6.1)
├── audio/resampler.py              # vers format interne 16kHz mono int16 (§6.2)
├── audio/player.py                 # file de lecture (§6.9)
├── stt/faster_whisper.py, filter.py # STT + filtre anti-hallucination (§6.5, §6.6)
├── text/segmenter.py, speakable.py, pipeline.py  # Text Pipeline (§6.7) + speak_sentence partagé
└── tts/piper.py, say.py            # synthèse streaming (§6.8) — Piper et macOS `say`

controller/
├── fake_agent.py                   # Fake Agent de test (§4.1), utilisé par main_voice.py
├── acp_http_client.py              # client HTTP+SSE du serveur Node (intégration web)
└── web_session.py                  # orchestrateur mode web (PTT -> STT -> ACP -> TTS)

main.py                             # étape 1
main_voice.py                       # étapes 3/4 (tour complet, Fake Agent)
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
