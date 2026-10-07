# TODO — Voice Gateway

Suivi des étapes de [`VOICEGTWSPEC.md`](../VOICEGTWSPEC.md) et de l'intégration
à l'UI web du projet parent.

## Fait

- [x] Scaffold du projet (`pyproject.toml`, venv, structure)
- [x] Étape 1 — `AudioIO` full-duplex + PTT clavier → WAV
- [x] Étape 2 — Resampler + STT local (faster-whisper) + filtre anti-hallucination
- [x] Étape 3 — Fake Agent streaming + Text Pipeline + TTS (Piper) + Player
- [x] Étape 4 — Soak test 50+ tours en PTT
- [x] Intégration Voice Gateway dans l'UI web (v0) — bouton "Mode vocal",
      pont HTTP+SSE vers le serveur Node existant, instance unique forcée
- [x] Provider TTS alternatif via la commande macOS `say` (`voice_gateway/tts/say.py`)
- [x] Sélecteur de rendu vocal (piper / say / navigateur Chrome) dans l'UI —
      capture (PTT+Whisper) inchangée, seule la synthèse change ; `browser`
      ne synthétise rien côté Python, relaie le texte pour `speechSynthesis`
      côté navigateur

## À faire

- [ ] Annonce vocale sur demande de permission (§12.3/§12.4) — `web_session.py`
      ignore actuellement l'événement SSE `permission_request` ; faire dire
      une notice courte ("L'agent demande une autorisation, valide dans
      l'interface") pour ne pas bloquer silencieusement le tour en cours.

## Hors scope pour l'instant (étapes 5+ de la spec)

VAD, barge-in, AEC, intégration ACP en subprocess direct côté Python
(actuellement bypassée : le serveur Node gère déjà l'agent).
