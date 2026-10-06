# Spécification — Voice Gateway (v2)

> Révision de la v1. Changements principaux : capture audio **locale côté serveur** (pas de navigateur), **AEC intégrée**, **streaming** TTS/Player, **pipeline texte** (filtre speakable + segmenteur), state machine redécoupée Gateway/Controller, intégration ACP en **stdio**, harness de test sur fichiers WAV.

---

## 1. Objectif

Le Voice Gateway est un composant **autonome et local** qui gère toute la chaîne audio :

```
Micro → AudioIO → Resampler → AEC → VAD (+pre-roll) → STT → Transcript
      → Conversation Controller → Text Pipeline → TTS (stream) → Player → Haut-parleur
```

- Indépendant de Kilo, d'ACP et de tout LLM.
- Toute l'inférence (VAD, STT, TTS) tourne **en local sur la machine hôte**.
- Le navigateur, s'il existe, n'est qu'une **UI d'affichage et de commande** : il ne capture ni ne joue d'audio.

Priorité : une chaîne vocale locale stable, validée sur **50+ tours** en mode test, avant toute intégration agent.

---

## 2. Principes d'architecture

| Principe | Règle |
|---|---|
| Local-first | Audio, VAD, STT, TTS locaux. Aucun appel réseau requis en mode test. |
| Découplage | Le Gateway ne connaît ni Kilo ni ACP. STT, TTS, VAD et AEC sont interchangeables. |
| Propriété unique de l'audio | Un seul composant (`AudioIO`) possède micro **et** sortie, via un flux full-duplex unique ouvert pour la durée de la session. |
| Streaming par défaut | La synthèse et la lecture sont des flux de chunks annulables, jamais des appels bloquants « tout ou rien ». |
| Annulation explicite | Toute opération longue (STT, TTS, requête agent) accepte un jeton d'annulation. |
| Robustesse avant sophistication | Push-to-talk → VAD → barge-in, avec validation de stabilité à chaque palier. |
| Observabilité | Chaque étape horodatée (horloge monotone) et mesurée. |

---

## 3. Choix techniques

### 3.1 Langage

**Python ≥ 3.11 (asyncio)** pour le Gateway.

Justification : l'écosystème audio et d'inférence locale y est natif et mature (capture, VAD, Whisper, TTS), contrairement à Node où chaque brique passe par un addon natif fragile. Un SDK Python ACP est disponible pour l'intégration agent.

### 3.2 Implémentations de référence

| Brique | Défaut | Alternatives |
|---|---|---|
| Audio I/O | `sounddevice` (PortAudio), flux full-duplex | `pyaudio` |
| Resampling | `soxr` | `scipy.signal.resample_poly` |
| AEC | WebRTC Audio Processing (binding Python) | SpeexDSP ; désactivée en mode casque |
| VAD | Silero VAD (ONNX, CPU) | WebRTC VAD |
| STT | `faster-whisper` (CTranslate2) | `whisper.cpp`, MLX Whisper |
| TTS | Piper (voix `fr_FR`) | Kokoro, autre moteur local |
| Transport UI | WebSocket (`websockets`) | — |
| Agent | ACP sur stdio (agent lancé en subprocess) | — |

Le **téléchargement des modèles est hors périmètre** : le Gateway reçoit des chemins locaux et échoue explicitement si un modèle est absent ou invalide.

---

## 4. Modes de fonctionnement

### 4.1 Mode test (obligatoire)

```
Micro → AEC → VAD → STT → Transcript → Fake Agent → Text Pipeline → TTS → Player
```

Le Fake Agent répond `"J'ai bien reçu : " + transcript`. Il doit pouvoir **streamer** sa réponse mot par mot avec un délai configurable, pour simuler un agent réel et exercer le segmenteur.

Aucun accès réseau.

### 4.2 Mode agent

```
Micro → AEC → VAD → STT → Transcript → Controller → ACP (stdio) → Agent
                                           ↑                        │
                                           └── session/update ──────┘
Controller → Text Pipeline → TTS → Player
```

Agents cibles : Kilo, Gemini CLI, ou tout agent exposant ACP. Aucun code spécifique à un agent dans le Gateway.

### 4.3 Mode replay (tests)

`AudioIO` est remplacé par `FileAudioSource` / `NullAudioSink` : injection de fichiers WAV, capture de la sortie TTS dans un fichier. Mode déterministe, utilisable en CI.

---

## 5. Architecture logique

```
┌──────────────────────────── VOICE GATEWAY ─────────────────────────────┐
│                                                                        │
│  ┌──────────────────────── AudioIO (full-duplex) ───────────────────┐  │
│  │   input stream ──────────────┐          ┌────── output stream    │  │
│  └──────────────────────────────┼──────────┼────────────────────────┘  │
│                                 ↓          ↑                           │
│                            Resampler    Player ◄── AudioChunk          │
│                                 ↓          │                           │
│                    ┌──────── AEC ◄─────────┘ (signal de référence)     │
│                    ↓                                                   │
│                VAD + pre-roll ring buffer                              │
│                    ↓ segment                                           │
│                STT Provider                                            │
│                    ↓                                                   │
│            Transcript Filter (anti-hallucination)                      │
│                    ↓                                                   │
│              transcript event ──────────────────────► (Controller)     │
│                                                                        │
│  (Controller) ──► text chunks ──► Speakable Filter ──► Sentence        │
│                                                        Segmenter       │
│                                                            ↓           │
│                                                    TTS Provider        │
│                                                            ↓           │
│                                                     AudioChunk stream  │
│                                                            ↓           │
│                                                         Player         │
│                                                                        │
│  Gateway State Machine · Event Bus · Metrics · Logger                  │
└────────────────────────────────────────────────────────────────────────┘
```

Le **Conversation Controller** est hors du Gateway. Il consomme ses événements et lui pousse du texte.

---

## 6. Composants

### 6.1 AudioIO

Responsabilités :
- sélectionner les périphériques d'entrée et de sortie ;
- ouvrir **un seul flux full-duplex** (`sounddevice.Stream`) pour la durée de la session, afin d'aligner temporellement capture et lecture (prérequis AEC) ;
- pousser les frames d'entrée dans une file non bloquante ; le callback audio ne fait **aucun** traitement lourd ;
- tirer les frames de sortie depuis le Player, en émettant du silence si le buffer est vide (jamais de blocage dans le callback) ;
- détecter les déconnexions et changements de périphérique, puis tenter une réouverture avec backoff ;
- compter les `input_overflow` et `output_underflow`.

Règle : jamais d'ouverture ou de fermeture du micro par tour. Le flux vit de `start()` à `stop()`.

### 6.2 Resampler

- Format interne : **PCM float32 ou int16, mono, 16 kHz, frames de 20 ms (320 échantillons)**.
- Le format natif du périphérique (souvent 44,1 ou 48 kHz, stéréo) est converti explicitement.
- Le format interne est configurable, mais **fixe pendant une session**.

### 6.3 AEC (annulation d'écho)

Nécessaire dès que le barge-in est actif sans casque : sans AEC, le VAD détecte la voix du TTS dans les haut-parleurs et l'agent s'interrompt lui-même.

- Entrées : frame micro + frame de **référence** (ce que le Player vient d'envoyer à la sortie, au même format interne).
- Sortie : frame micro nettoyée, transmise au VAD.
- Le délai système (latence de sortie + latence d'entrée) est estimé au démarrage puis configurable (`aec.delay_ms`).
- Optionnel : suppression de bruit (NS) et contrôle de gain (AGC) du même module.

Modes de barge-in (`barge_in.mode`) :

| Mode | Comportement |
|---|---|
| `off` | Micro ignoré pendant `SPEAKING` (half-duplex). Défaut des étapes 1 à 5. |
| `headset` | Barge-in actif, AEC désactivée. Suppose un casque. |
| `aec` | Barge-in actif avec AEC. Mode cible enceintes + micro. |

### 6.4 VAD

- Émet `speech-start`, `speech-end` et le segment audio correspondant.
- Ne transcrit pas.
- **Pre-roll** : ring buffer permanent de `pre_roll_ms` (300–500 ms) préfixé au segment pour ne pas couper la première syllabe.
- **Hangover** : `speech-end` n'est émis qu'après `min_silence_ms` de silence continu.
- Les segments plus courts que `min_speech_ms` sont jetés (clics, toux).
- Au-delà de `max_segment_ms`, le segment est coupé et envoyé (borne la latence et la fenêtre Whisper).
- Pendant `SPEAKING` en mode barge-in, un seuil distinct et plus strict s'applique (`barge_in.threshold`, `barge_in.min_speech_ms`) pour limiter les faux positifs dus à l'écho résiduel.

En mode push-to-talk, le VAD est contourné : le segment est délimité par `ptt-down` / `ptt-up`, pre-roll inclus.

### 6.5 STT

- Transcription **par segment** en v1 (pas de partiels).
- Exécutée hors de la boucle asyncio (thread ou process dédié) pour ne pas bloquer l'audio.
- Annulable : un segment obsolète (barge-in, stop) est abandonné.
- Paramètres Whisper recommandés : `language` forcé (`fr`), `condition_on_previous_text=False` (réduit les boucles d'hallucination), `beam_size` configurable.

### 6.6 Transcript Filter (anti-hallucination)

Whisper hallucine sur le silence et le bruit, notamment en français (« Sous-titres réalisés par la communauté d'Amara.org », « Merci d'avoir regardé »…).

Un transcript est rejeté si l'une des conditions suivantes est vraie :
- `no_speech_prob > stt.filter.max_no_speech_prob` ;
- `avg_logprob < stt.filter.min_avg_logprob` ;
- `compression_ratio > stt.filter.max_compression_ratio` (texte répétitif) ;
- le texte normalisé correspond à une entrée de la blacklist configurable ;
- le texte est vide ou fait moins de `min_chars` caractères.

Un rejet émet `voice.transcript-rejected` (avec la raison) et ramène le Gateway en `LISTENING`.

### 6.7 Text Pipeline

Entre la réponse texte (Fake Agent ou agent ACP) et le TTS.

**Speakable Filter** : transforme du texte agent en texte prononçable.
- Supprime ou résume les blocs de code (« J'ai écrit un bloc de code Python de 12 lignes. ») ;
- retire la syntaxe markdown (titres, gras, puces, liens → libellé) ;
- abrège les chemins longs, URLs et hashes ;
- normalise les symboles et unités usuels.

Les règles sont configurables. Le texte complet non filtré reste disponible pour l'UI.

**Sentence Segmenter** : accumule les chunks de texte en streaming et émet des **phrases complètes** au TTS dès qu'elles sont terminées (ponctuation forte, ou longueur max atteinte sur une coupure faible). C'est ce qui permet de commencer à parler avant la fin de la réponse agent.

### 6.8 TTS

- Synthèse **phrase par phrase**, sortie en flux d'`AudioChunk`.
- Rééchantillonnage vers le format de sortie si nécessaire.
- Annulable immédiatement.
- File de phrases : la phrase N+1 est synthétisée pendant la lecture de la phrase N (pipelining).

### 6.9 Player

- File de chunks consommée par le callback de sortie d'`AudioIO`.
- `clear()` vide la file **et** le buffer en cours en moins d'une frame.
- Expose le signal de référence à l'AEC.
- Émet `playback-start` au premier chunk réellement envoyé à la sortie, et `playback-end` quand la file est drainée **et** qu'aucune synthèse n'est en cours.

---

## 7. Interfaces

```python
from typing import AsyncIterator, Protocol
from dataclasses import dataclass
import asyncio

@dataclass
class AudioFormat:
    sample_rate: int      # 16000
    channels: int         # 1
    dtype: str            # "int16" | "float32"

@dataclass
class AudioChunk:
    pcm: bytes
    fmt: AudioFormat
    t_mono: float         # horodatage time.monotonic()

@dataclass
class Transcript:
    text: str
    language: str | None
    confidence: float | None
    no_speech_prob: float | None
    avg_logprob: float | None
    compression_ratio: float | None
    audio_duration_ms: int
    stt_latency_ms: int


class Cancelled(Exception): ...

class CancelToken:
    """Wrapper autour d'asyncio.Event ; vérifié entre chaque étape coûteuse."""
    def cancel(self) -> None: ...
    @property
    def cancelled(self) -> bool: ...


class VADProvider(Protocol):
    def reset(self) -> None: ...
    def process(self, frame: AudioChunk) -> float: ...   # probabilité de parole [0,1]


class EchoCanceller(Protocol):
    def process(self, mic: AudioChunk, reference: AudioChunk) -> AudioChunk: ...


class STTProvider(Protocol):
    async def initialize(self, config: dict) -> None: ...
    async def transcribe(self, audio: bytes, fmt: AudioFormat,
                         cancel: CancelToken) -> Transcript: ...
    async def shutdown(self) -> None: ...


class TTSProvider(Protocol):
    async def initialize(self, config: dict) -> None: ...
    def output_format(self) -> AudioFormat: ...
    def synthesize(self, text: str,
                   cancel: CancelToken) -> AsyncIterator[AudioChunk]: ...
    async def shutdown(self) -> None: ...


class AudioPlayer(Protocol):
    def enqueue(self, chunk: AudioChunk) -> None: ...
    def clear(self) -> None: ...
    def is_idle(self) -> bool: ...
```

Les implémentations concrètes (`WhisperSTT`, `PiperTTS`, `SileroVAD`, `WebRTCAEC`) sont sélectionnées par configuration via un registre. Le cœur du Gateway n'importe que les `Protocol`.

---

## 8. State machines

La logique conversationnelle est séparée de la logique audio.

### 8.1 Gateway (audio)

| État | Description |
|---|---|
| `IDLE` | Session fermée, aucun périphérique ouvert. |
| `LISTENING` | Flux ouvert, VAD actif ou PTT armé, aucune parole en cours. |
| `CAPTURING` | Parole utilisateur en cours (après `speech-start` ou `ptt-down`). |
| `TRANSCRIBING` | Segment envoyé au STT. |
| `SPEAKING` | Synthèse et/ou lecture en cours. |
| `ERROR` | Erreur détectée ; aucune action audio. |
| `RECOVERING` | Tentative de réouverture ou réinitialisation. |

Transitions :

| De | Événement | Vers | Action |
|---|---|---|---|
| `IDLE` | `start()` | `LISTENING` | Ouvre AudioIO, charge les modèles si besoin |
| `LISTENING` | `speech-start` / `ptt-down` | `CAPTURING` | Démarre le segment (avec pre-roll) |
| `CAPTURING` | `speech-end` / `ptt-up` | `TRANSCRIBING` | Envoie le segment au STT |
| `CAPTURING` | segment < `min_speech_ms` | `LISTENING` | Jette le segment |
| `TRANSCRIBING` | transcript accepté | `LISTENING` | Émet `voice.transcript` |
| `TRANSCRIBING` | transcript rejeté | `LISTENING` | Émet `voice.transcript-rejected` |
| `LISTENING` | `say(text)` / chunk texte | `SPEAKING` | Démarre le Text Pipeline |
| `SPEAKING` | file drainée | `LISTENING` | Émet `voice.playback-end` |
| `SPEAKING` | `speech-start` (barge-in actif) | `CAPTURING` | `cancel` TTS + `player.clear()`, émet `voice.interrupt` |
| `SPEAKING` | `interrupt()` | `LISTENING` | `cancel` TTS + `player.clear()`, émet `voice.interrupt` |
| `TRANSCRIBING` | `speech-start` | `TRANSCRIBING` | Bufferise le nouveau segment (cf. §9) |
| `*` | erreur | `ERROR` | Émet `voice.error` |
| `ERROR` | automatique | `RECOVERING` | Selon la politique de §14 |
| `RECOVERING` | succès | `LISTENING` | |
| `RECOVERING` | échec définitif | `IDLE` | Ferme proprement |
| `*` | `stop()` | `IDLE` | Annule tout, ferme AudioIO |

`start()` en dehors d'`IDLE` et `stop()` en `IDLE` sont des no-op journalisés (idempotence).

### 8.2 Conversation Controller

| État | Description |
|---|---|
| `IDLE` | Aucun tour en cours. |
| `WAITING_AGENT` | Prompt envoyé, aucun chunk de réponse reçu. |
| `STREAMING` | Chunks de réponse reçus, relayés au Gateway. |
| `WAITING_PERMISSION` | L'agent attend une décision de permission. |
| `CANCELLING` | Annulation demandée à l'agent, en attente de confirmation. |

Sur `voice.interrupt` ou un nouveau transcript pendant `WAITING_AGENT` / `STREAMING` : le Controller envoie `session/cancel` à l'agent et passe en `CANCELLING`.

---

## 9. Politique de tours

- **Un seul tour actif à la fois.**
- Si l'utilisateur parle pendant que l'agent réfléchit (`WAITING_AGENT`) : le tour courant est annulé, le nouveau transcript devient le tour suivant (configurable : `turn_policy.on_new_speech: cancel | queue`).
- Si un segment arrive pendant `TRANSCRIBING` : il est bufferisé et transcrit à la suite ; les deux transcripts sont concaténés en un seul tour si l'écart est inférieur à `turn_policy.merge_window_ms`.
- Chaque tour porte un `turn_id` monotone, généré par le Gateway à `speech-start`.

---

## 10. API du Gateway

```python
class VoiceGateway:
    async def start(self) -> None
    async def stop(self) -> None

    # sortie vocale
    async def say(self, text: str, turn_id: int | None = None) -> None   # texte complet
    def push_text(self, chunk: str, turn_id: int) -> None                # streaming
    def end_text(self, turn_id: int) -> None                             # fin de réponse
    def interrupt(self) -> None

    # push-to-talk
    def ptt_down(self) -> None
    def ptt_up(self) -> None

    def on(self, event_type: str, handler) -> None
```

Déclenchement push-to-talk avant l'UI web : maintien de la barre espace dans le terminal (`main.py --ptt-key space`).

---

## 11. Événements

Préfixe unique `voice.`. Enveloppe commune :

```json
{
  "type": "voice.<event>",
  "session_id": "abc",
  "turn_id": 12,
  "t_mono": 1234.567,
  "ts": "2026-10-06T06:42:01.123+02:00",
  "data": {}
}
```

| Événement | `data` |
|---|---|
| `voice.state` | `{ "from": "LISTENING", "to": "CAPTURING" }` |
| `voice.speech-start` | `{}` |
| `voice.speech-end` | `{ "duration_ms": 2140 }` |
| `voice.transcript` | `{ "text": "...", "language": "fr", "stt_latency_ms": 410 }` |
| `voice.transcript-rejected` | `{ "text": "...", "reason": "blacklist" }` |
| `voice.synthesis-start` | `{ "sentence_idx": 0 }` |
| `voice.synthesis-end` | `{ "sentence_idx": 0, "latency_ms": 180 }` |
| `voice.playback-start` | `{}` |
| `voice.playback-end` | `{ "duration_ms": 3200 }` |
| `voice.interrupt` | `{ "source": "barge-in" \| "api" }` |
| `voice.device` | `{ "event": "disconnected" \| "reconnected" \| "changed", "device": "..." }` |
| `voice.error` | `{ "code": "stt-model-invalid", "message": "...", "recoverable": true }` |

---

## 12. Intégration ACP

Le Gateway n'importe pas ACP. Seul le Conversation Controller le fait.

### 12.1 Transport

- ACP fonctionne en **JSON-RPC sur stdio** : le Controller lance l'agent en **subprocess** (commande configurable) et dialogue via stdin/stdout.
- Pas d'endpoint HTTP par défaut. Un adaptateur réseau éventuel est un composant séparé.

### 12.2 Flux

| Côté Voice | Côté ACP |
|---|---|
| `voice.transcript` | `session/prompt` |
| — | `session/update` (chunks de message agent) → `gateway.push_text()` |
| — | fin de `session/prompt` → `gateway.end_text()` |
| `voice.interrupt` / nouveau transcript | `session/cancel` |
| — | `session/update` (tool call en cours) → feedback vocal court optionnel |
| — | demande de permission → politique §12.3 |

Les deux flux d'événements restent indépendants. Le Controller les orchestre sans les fusionner.

### 12.3 Permissions

Une demande de permission de l'agent (écriture de fichier, exécution de commande…) ne doit **jamais** être accordée par défaut sur la seule base d'une transcription vocale.

`acp.permissions.policy` :
- `deny` : refus systématique (défaut) ;
- `ui` : décision demandée dans l'UI web, annoncée vocalement (« L'agent demande l'autorisation de modifier deux fichiers, valide dans l'interface. ») ;
- `allow_read_only` : lecture autorisée, tout le reste refusé.

L'acceptation par commande vocale est hors périmètre v2.

### 12.4 Feedback pendant les tool calls

Si aucun chunk de texte n'arrive pendant `acp.feedback.silence_ms` alors qu'un tool call est en cours, le Controller peut faire prononcer un message court (« Je regarde… »), au plus une fois par tool call.

---

## 13. Configuration

```yaml
session:
  id_prefix: vg

audio:
  input_device: default
  output_device: default
  internal_sample_rate: 16000
  channels: 1
  frame_ms: 20
  output_buffer_ms: 200

aec:
  enabled: false               # activé en mode barge_in=aec
  engine: webrtc               # webrtc | speex
  delay_ms: auto
  noise_suppression: true
  auto_gain: false

vad:
  enabled: false               # false = push-to-talk
  engine: silero
  threshold: 0.5
  pre_roll_ms: 400
  min_speech_ms: 250
  min_silence_ms: 600
  max_segment_ms: 30000

barge_in:
  mode: off                    # off | headset | aec
  threshold: 0.7
  min_speech_ms: 300

stt:
  provider: faster-whisper
  model: /models/whisper/large-v3-turbo
  device: auto                 # cpu | cuda | auto
  compute_type: auto
  language: fr
  beam_size: 1
  condition_on_previous_text: false
  filter:
    max_no_speech_prob: 0.6
    min_avg_logprob: -1.0
    max_compression_ratio: 2.4
    min_chars: 2
    blacklist_file: config/stt-blacklist-fr.txt

text:
  speakable:
    code_blocks: summarize     # summarize | drop
    max_path_chars: 30
  segmenter:
    max_sentence_chars: 220

tts:
  provider: piper
  model: /models/piper/fr_FR-voice.onnx
  speed: 1.0

turn_policy:
  on_new_speech: cancel        # cancel | queue
  merge_window_ms: 800

mode:
  type: test                   # test | agent | replay

fake_agent:
  stream: true
  word_delay_ms: 40

acp:
  command: ["kilo", "acp"]     # commande de lancement de l'agent en subprocess
  cwd: /path/to/workspace
  permissions:
    policy: deny               # deny | ui | allow_read_only
  feedback:
    silence_ms: 2500

transport:
  websocket:
    enabled: false
    host: 127.0.0.1
    port: 8765

observability:
  log_file: logs/voice-gateway.jsonl
  level: info
  save_segments: false         # sauvegarde WAV des segments (debug)
```

---

## 14. Gestion des erreurs

| Erreur | Comportement |
|---|---|
| Micro indisponible au démarrage | `voice.error` non récupérable, retour `IDLE` |
| Micro déconnecté en session | `RECOVERING` : réouverture avec backoff (1 s, 2 s, 5 s… max `recovery.max_attempts`) |
| Changement de périphérique par défaut | Réouverture du flux sur le nouveau device, tour en cours annulé |
| Modèle STT/TTS absent ou invalide | Échec au `start()`, non récupérable |
| STT en erreur sur un segment | Segment abandonné, `voice.error` récupérable, retour `LISTENING` |
| TTS en erreur sur une phrase | Phrase sautée, suite de la file conservée |
| Underflow/overflow audio | Compté en métrique ; alerte au-delà d'un seuil |
| Timeout STT ou TTS | Annulation de l'opération, traité comme une erreur récupérable |
| Agent ACP crashé | Le Controller relance le subprocess, annonce vocalement l'incident |
| `start()` / `stop()` répétés | No-op idempotent, journalisé |
| Arrêt brutal (SIGINT/SIGTERM) | Annulation des tâches, fermeture du flux audio, flush des logs |

Invariant : à la sortie de `stop()`, aucun thread, flux PortAudio ou subprocess ne reste ouvert.

---

## 15. Observabilité

- Logs **JSONL**, un événement par ligne, enveloppe du §11 + `component`, `duration_ms`, `error`.
- Horloge **monotone** (`time.monotonic()`) pour toutes les durées ; horodatage ISO uniquement pour la lecture humaine.
- Option `save_segments` : WAV de chaque segment envoyé au STT, nommé `session_turn.wav`, pour rejouer les cas problématiques en mode replay.

Exemple :

```
06:42:01.120 abc t12 VAD  speech-start
06:42:03.260 abc t12 VAD  speech-end        dur=2140ms
06:42:03.670 abc t12 STT  transcript        lat=410ms  "Bonjour, comment ça va ?"
06:42:03.690 abc t12 TTS  synthesis-start   s=0
06:42:03.870 abc t12 TTS  synthesis-end     s=0 lat=180ms
06:42:03.890 abc t12 PLY  playback-start
06:42:06.010 abc t12 PLY  playback-end      dur=2120ms
```

---

## 16. Métriques et budgets de latence

### 16.1 Métriques par tour

| Métrique | Définition |
|---|---|
| `speech_duration_ms` | `speech-start` → `speech-end` |
| `vad_endpoint_ms` | fin réelle de parole → `speech-end` (≈ `min_silence_ms`) |
| `stt_latency_ms` | `speech-end` → transcript prêt |
| `stt_rtf` | `stt_latency / audio_duration` |
| `agent_ttft_ms` | prompt envoyé → premier chunk agent (mode agent) |
| `first_sentence_ms` | premier chunk → première phrase segmentée |
| `tts_first_chunk_ms` | phrase → premier `AudioChunk` |
| **`perceived_latency_ms`** | **`speech-end` → `playback-start`** |
| `interrupt_latency_ms` | `speech-start` (barge-in) → silence effectif en sortie |

### 16.2 Métriques de santé (échantillonnées toutes les 10 s)

- RSS du process, nombre de threads, descripteurs ouverts ;
- `input_overflow_count`, `output_underflow_count` ;
- longueur des files (frames d'entrée, chunks de sortie).

### 16.3 Budgets cibles

Cibles indicatives, à recalibrer selon la machine hôte (GPU / CPU) :

| Mesure | Mode test | Mode agent |
|---|---|---|
| `stt_latency_ms` (phrase de 3 s) | < 700 ms | < 700 ms |
| `tts_first_chunk_ms` | < 250 ms | < 250 ms |
| `perceived_latency_ms` (hors `vad_endpoint`) | < 1 200 ms | < 1 200 ms + `agent_ttft` |
| `interrupt_latency_ms` | < 150 ms | < 150 ms |
| Dérive RSS sur 50 tours | < 5 % | < 5 % |

---

## 17. Tests

### 17.1 Harness replay (automatisé)

- `FileAudioSource` injecte des WAV avec un timing réaliste (frames de 20 ms en temps réel ou accéléré).
- `NullAudioSink` capture la sortie et horodate le premier échantillon non nul.
- Corpus minimal :
  - phrases courtes (« oui », « non », « stop ») ;
  - phrases longues (> 20 s) ;
  - silence pur (doit produire 0 transcript) ;
  - bruit ambiant seul (doit produire 0 transcript) ;
  - plusieurs phrases séparées de pauses variables ;
  - parole superposée à la sortie TTS (barge-in, avec écho simulé).
- Assertions : transcripts attendus (WER toléré), états traversés, absence de transcript fantôme, métriques dans les budgets.

### 17.2 Tests de robustesse (manuels ou semi-automatisés)

- 50+ tours consécutifs (soak test de 200 tours recommandé) ;
- interruption pendant la lecture ;
- débranchement et rebranchement du micro ;
- changement de périphérique de sortie en cours de lecture ;
- STT en erreur (modèle corrompu, timeout simulé) ;
- TTS en erreur ;
- `start()` et `stop()` répétés ;
- SIGINT pendant chaque état ;
- redémarrage complet du Gateway.

### 17.3 Critères de sortie

```
0 blocage
0 fuite de ressources (RSS stable, threads et FD constants)
0 micro définitivement perdu
0 état incohérent (toute transition conforme au §8)
0 transcript fantôme sur le corpus silence/bruit
```

Le soak test est **rejoué à chaque palier** (§19), pas seulement une fois.

---

## 18. Structure du projet

```
voice-gateway/
├── pyproject.toml
├── main.py
├── config/
│   ├── config.yaml
│   └── stt-blacklist-fr.txt
├── voice_gateway/
│   ├── core/
│   │   ├── gateway.py
│   │   ├── state_machine.py
│   │   ├── events.py
│   │   ├── cancel.py
│   │   └── registry.py
│   ├── audio/
│   │   ├── audio_io.py          # flux full-duplex sounddevice
│   │   ├── resampler.py
│   │   ├── aec.py
│   │   ├── vad.py               # + pre-roll ring buffer
│   │   ├── player.py
│   │   └── file_source.py       # FileAudioSource / NullAudioSink
│   ├── stt/
│   │   ├── provider.py
│   │   ├── faster_whisper.py
│   │   └── filter.py
│   ├── text/
│   │   ├── speakable.py
│   │   └── segmenter.py
│   ├── tts/
│   │   ├── provider.py
│   │   └── piper.py
│   ├── transport/
│   │   └── websocket.py
│   └── observability/
│       ├── logger.py
│       └── metrics.py
├── controller/
│   ├── controller.py
│   ├── fake_agent.py
│   └── acp_client.py
└── tests/
    ├── corpus/                  # WAV de référence
    ├── test_state_machine.py
    ├── test_segmenter.py
    ├── test_speakable.py
    ├── test_replay.py
    └── soak.py
```

Le package `voice_gateway` n'importe jamais `controller`.

---

## 19. Ordre d'implémentation

Chaque étape a un **critère de sortie**. Pas d'étape suivante tant qu'il n'est pas atteint.

| # | Étape | Critère de sortie |
|---|---|---|
| 1 | AudioIO full-duplex + PTT clavier → WAV | 50 enregistrements successifs sans fuite ni overflow |
| 2 | + Resampler + STT local + filtre | Corpus replay transcrit, 0 fantôme sur silence/bruit |
| 3 | + Fake Agent (streaming) + Text Pipeline + TTS + Player | Tour complet audible, `perceived_latency` dans le budget |
| 4 | Soak 50+ tours en PTT | Critères §17.3 |
| 5 | + VAD + pre-roll | Soak rejoué, aucune syllabe coupée sur le corpus |
| 6a | + Barge-in mode `headset` | `interrupt_latency` < 150 ms, soak rejoué |
| 6b | + AEC, barge-in mode `aec` | 0 auto-interruption sur 50 tours enceintes + micro |
| 7 | + WebSocket + UI web (affichage, PTT, interrupt) | L'UI reflète tous les états, 0 audio côté navigateur |
| 8 | + Conversation Controller (state machine §8.2) | Fake Agent piloté par le Controller, soak rejoué |
| 9 | + Client ACP stdio + politique de permissions | Agent de test ACP minimal piloté à la voix, cancel fonctionnel |
| 10 | Connexion Kilo | 50 tours agent sans blocage, permissions conformes |

---

## 20. Architecture finale

```
                  ┌──────────────────────────────┐
                  │           Web UI             │
                  │  texte · états · PTT ·       │
                  │  permissions · (pas d'audio) │
                  └──────────────┬───────────────┘
                                 │ WebSocket (événements + commandes)
                                 ↓
┌──────────────────────── Machine hôte (local) ──────────────────────────┐
│                                                                        │
│   🎙 Micro ─┐                                         ┌─► 🔊 Speaker   │
│             ↓                                         │                │
│   ┌─────────────────────────────────────────────────────────────┐      │
│   │                      Voice Gateway                          │      │
│   │  AudioIO · AEC · VAD · STT · Text Pipeline · TTS · Player   │      │
│   │  State machine · Metrics                                    │      │
│   └──────────────────────────┬──────────────────────────────────┘      │
│                              │ événements voice.* / push_text()        │
│                              ↓                                         │
│                  ┌────────────────────────┐                            │
│                  │ Conversation Controller │                           │
│                  └───────────┬────────────┘                            │
│                              │ ACP (JSON-RPC stdio)                    │
│                ┌─────────────┼─────────────┐                           │
│                ↓             ↓             ↓                           │
│              Kilo       Gemini CLI     autre agent                     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Principe directeur

Le Voice Gateway est d'abord un **produit audio local, autonome et fiable**. ACP et l'agent ne sont qu'une intégration supplémentaire.

Première cible de validation :

```
🎙 Micro → PTT → Whisper local → Fake Agent (stream) → Speakable + Segmenter → Piper → 🔊
```

50 tours sans dégradation. Puis, palier par palier : VAD → barge-in → AEC → Web UI → Controller → ACP → Kilo.
