# ACP Web Client générique

## 1. Objectif

Développer une interface Web légère permettant d'interagir avec n'importe quel agent compatible avec le protocole ACP.

Le client ne doit contenir aucune dépendance fonctionnelle à Kilo CLI, Gemini CLI ou à un autre harness particulier.

L'agent est considéré comme une boîte noire ACP.

Le client découvre dynamiquement :

* les capacités de l'agent ;
* les sessions disponibles ;
* les commandes ;
* les options de configuration ;
* les demandes de permission ;
* les tool calls ;
* les mises à jour de progression ;
* les fonctionnalités multimodales supportées.

L'interface adapte son comportement aux capacités effectivement annoncées par l'agent.

---

## 2. Architecture cible

```text
┌──────────────────────────────────────────────┐
│                  Browser                     │
│                                              │
│  Chat                                        │
│  Sessions                                    │
│  Commands                                    │
│  Configuration                               │
│  Tool calls                                  │
│  Permissions                                 │
│  Progress / Plan                             │
│                                              │
│             ACP Web Client                   │
└──────────────────────┬───────────────────────┘
                       │
                 HTTP / SSE
                       │
┌──────────────────────▼───────────────────────┐
│                 ACP Bridge                   │
│                                              │
│  Web transport ↔ ACP transport               │
└──────────────────────┬───────────────────────┘
                       │
                       │ ACP
                       ▼
┌──────────────────────────────────────────────┐
│                ACP Agent                     │
│                                              │
│ Kilo / Gemini / autre agent compatible ACP   │
└──────────────────────────────────────────────┘
```

Le bridge doit être indépendant du frontend.

Le transport navigateur → bridge est une décision d'implémentation et ne constitue pas une partie du contrat ACP.

Pour le MVP, utiliser SSE pour les événements serveur → navigateur et des requêtes HTTP pour les actions client → serveur.

---

## 3. Contraintes techniques

### Frontend

Utiliser :

* HTML ;
* CSS ;
* JavaScript vanilla.

Ne pas utiliser React, Vue ou Angular pour le MVP.

Objectif : minimiser le nombre de dépendances et conserver une architecture facilement remplaçable.

### Backend

Utiliser un petit serveur Node.js.

Responsabilités :

1. servir les fichiers statiques ;
2. exposer l'API Web du client ;
3. maintenir la connexion ACP avec l'agent ;
4. transformer les événements ACP en événements consommables par le navigateur ;
5. transmettre les actions du navigateur vers ACP.

Le backend ne doit pas implémenter de logique métier propre à Kilo.

---

# 4. Initialisation ACP

À la connexion :

```text
Browser
   │
   ▼
ACP Bridge
   │
   │ initialize
   ▼
Agent
   │
   │ protocol version
   │ capabilities
   │ agent information
   ▼
ACP Bridge
   │
   ▼
Browser
```

Le client doit mémoriser :

```javascript
agent = {
    name,
    version,
    title,
    protocolVersion,
    capabilities
}
```

L'interface ne doit afficher que les fonctionnalités effectivement supportées.

---

# 5. Gestion des capacités

Créer un objet central :

```javascript
const capabilities = {
    sessions: {},
    prompt: {},
    tools: {},
    permissions: {},
    commands: {},
    config: {},
    audio: {},
    images: {},
    plans: {}
};
```

Ne jamais tester le nom de l'agent :

```javascript
// INTERDIT
if (agent.name === "kilo") { ... }
```

Tester les capacités :

```javascript
if (capabilities.permissions) {
    renderPermissions();
}
```

Une capacité inconnue doit être ignorée sans provoquer d'erreur.

---

# 6. Sessions

L'interface doit proposer :

```text
New session
Resume session
Close session
```

Lorsqu'une session est créée, le client doit récupérer les informations fournies par l'agent concernant :

* commandes disponibles ;
* options de configuration ;
* capacités spécifiques à la session.

Ces informations doivent alimenter dynamiquement l'interface.

---

# 7. Conversation

L'écran principal doit être organisé autour d'une conversation.

```text
┌──────────────────────────────────────────────┐
│ Agent: Kilo             Session: abc123      │
├──────────────────────────────────────────────┤
│                                              │
│ User                                        │
│ Analyse ce fichier                           │
│                                              │
│ Agent                                       │
│ Je vais examiner le fichier...               │
│                                              │
│ 🔧 Read file                                 │
│    ✓ completed                               │
│                                              │
│ Agent                                       │
│ Voici le résultat...                         │
│                                              │
├──────────────────────────────────────────────┤
│ Message...                            [Send] │
└──────────────────────────────────────────────┘
```

Les réponses doivent être affichées progressivement.

Le client ne doit pas attendre la fin du prompt pour afficher les événements reçus.

---

# 8. Streaming

Toutes les mises à jour ACP doivent être traitées comme des événements.

Le frontend doit disposer d'un dispatcher :

```javascript
function handleACPUpdate(update) {
    switch (update.type) {
        case "message":
            renderMessage(update);
            break;

        case "tool_call":
            renderToolCall(update);
            break;

        case "tool_call_update":
            updateToolCall(update);
            break;

        case "plan":
            renderPlan(update);
            break;

        case "status":
            renderStatus(update);
            break;

        default:
            handleUnknownUpdate(update);
    }
}
```

Les types exacts doivent suivre le schéma ACP utilisé par le client.

---

# 9. Permissions

Une demande ACP de permission doit être transformée en composant UI.

Exemple :

```text
┌────────────────────────────────────┐
│ Permission required                │
├────────────────────────────────────┤
│ Execute command                    │
│                                    │
│ npm test                           │
│                                    │
│ [ Allow once ]                     │
│ [ Always allow ]                   │
│ [ Reject ]                         │
│ [ Always reject ]                  │
└────────────────────────────────────┘
```

Le client ne doit pas inventer ses propres catégories de permissions.

Il affiche les options proposées par l'agent et renvoie le choix correspondant.

La politique de sécurité peut ensuite être configurée côté client, mais elle doit rester séparée du protocole d'affichage.

---

# 10. Commands / slash commands

Les commandes doivent être découvertes dynamiquement.

Exemple :

```text
Agent
   ↓
availableCommands
   ↓
UI

/test
/review
/commit
```

Le client doit afficher automatiquement les commandes disponibles.

Il ne doit pas avoir de liste codée en dur.

L'utilisateur doit pouvoir :

```text
/
```

et obtenir une palette :

```text
┌─────────────────────────┐
│ Commands                │
├─────────────────────────┤
│ /test                   │
│ /review                 │
│ /commit                 │
└─────────────────────────┘
```

Si l'agent modifie ses commandes pendant la session, l'interface doit se mettre à jour.

---

# 11. Configuration

Les options exposées par l'agent doivent être rendues dynamiquement.

Exemple :

```text
Model
[ Gemini 4 ▼ ]

Mode
[ Code ▼ ]

Thinking
[ High ▼ ]
```

Le frontend ne doit pas connaître la signification métier de ces options.

Il doit simplement savoir rendre les types supportés par ACP.

Une option inconnue doit être affichée de manière générique ou ignorée proprement.

---

# 12. Tool calls

Chaque tool call doit avoir un élément UI identifié par son identifiant ACP.

Exemple :

```text
🔧 Search
   status: running

🔧 Read file
   status: completed

🔧 Execute command
   status: rejected
```

Une mise à jour doit modifier l'élément existant plutôt que créer un nouvel élément.

Le frontend doit conserver :

```javascript
toolCalls[toolCallId]
```

---

# 13. Annulation

Pendant l'exécution d'un prompt :

```text
[ Stop ]
```

doit permettre d'envoyer la demande d'annulation ACP appropriée.

L'UI doit distinguer :

```text
running
cancelling
cancelled
completed
failed
```

---

# 14. Unknown / extensions

Le client doit être tolérant aux extensions.

Il est interdit de faire échouer toute la session parce qu'un agent renvoie une fonctionnalité inconnue.

Principe :

```text
ACP standard
     ↓
Renderer standard

Extension connue
     ↓
Renderer spécialisé

Extension inconnue
     ↓
Ignore / fallback
```

Une future extension spécifique à Kilo ne doit donc pas casser le client.

---

# 15. API interne du frontend

Créer une seule abstraction :

```javascript
const agent = {
    initialize(),
    newSession(),
    resumeSession(),
    closeSession(),
    prompt(text),
    cancel(),
    respondToPermission(id, option),
    setConfigOption(id, value),
    executeCommand(command)
};
```

Le reste de l'application ne doit pas manipuler directement JSON-RPC.

Ainsi :

```text
UI
 │
 ▼
ACP Client abstraction
 │
 ▼
ACP transport
 │
 ▼
Agent
```

---

# 16. Structure minimale

Le premier prototype doit pouvoir fonctionner avec :

```text
acp-web-client/
│
├── server.js
├── index.html
├── app.js
└── style.css
```

Après validation du concept, refactoriser :

```text
acp-web-client/
│
├── server/
│   ├── server.js
│   └── acp-bridge.js
│
├── public/
│   ├── index.html
│   ├── app.js
│   └── style.css
│
└── acp/
    ├── client.js
    ├── protocol.js
    └── events.js
```

---

# 17. MVP — ordre d'implémentation

### M1 — Connexion

* démarrer le bridge ;
* se connecter à un agent ACP ;
* `initialize` ;
* afficher nom/version/capacités.

### M2 — Conversation

* créer une session ;
* envoyer un prompt ;
* afficher le streaming ;
* annuler.

### M3 — Agent interactif

* tool calls ;
* mises à jour de tool calls ;
* permissions.

### M4 — Interface dynamique

* commandes ;
* configuration ;
* changement dynamique des commandes/options.

### M5 — Sessions

* nouvelle session ;
* reprise ;
* liste des sessions ;
* persistance.

### M6 — Fonctionnalités avancées

* plans ;
* usage ;
* multimodal ;
* extensions ACP.

---

# 18. Critères d'acceptation

Le MVP est considéré comme réussi si :

1. le frontend peut se connecter à un agent ACP sans connaître son nom ;
2. il découvre automatiquement ses capacités ;
3. il peut créer une session ;
4. il peut envoyer un prompt ;
5. il affiche la réponse en streaming ;
6. il affiche les tool calls ;
7. il sait présenter une demande de permission ;
8. il sait afficher les commandes exposées par l'agent ;
9. il sait afficher les options de configuration exposées ;
10. il peut annuler une opération ;
11. une fonctionnalité ACP inconnue ne fait pas planter l'application ;
12. remplacer Kilo par un autre agent ACP ne nécessite aucune modification du frontend.

---

# 19. Règle d'architecture fondamentale

**Le frontend ne doit jamais être un “Kilo Web UI”.**

Il doit être :

```text
             ACP Web Client
                    │
       ┌────────────┴────────────┐
       │                         │
   Agent ACP A               Agent ACP B
       │                         │
     Kilo                    autre agent
```

Le client est un **renderer générique du protocole ACP**.

Toute fonctionnalité spécifique à un agent doit être implémentée comme une extension isolée, jamais comme une dépendance structurelle du frontend.

---

# 20. Logging / Debug côté client

Le client doit offrir un mode debug clair, activable/désactivable sans rebuild.

```text
?debug=1
```

ou un bouton `[ Debug ]` dans l'interface.

### Objectif

Permettre de diagnostiquer un agent ACP qui se comporte mal (capacité manquante, update inattendu, erreur de transport) sans ouvrir la console navigateur et sans instrumenter le code à la main.

### Logger centralisé

Créer une seule abstraction, utilisée partout à la place de `console.log` direct :

```javascript
const log = {
    debug(scope, msg, data),
    info(scope, msg, data),
    warn(scope, msg, data),
    error(scope, msg, data)
};
```

`scope` identifie la provenance (`transport`, `session`, `tool_call`, `permission`, `command`, `config`, `unknown`, ...).

### Panneau Debug

En mode debug, afficher un panneau consultable dans l'UI (pas uniquement la console navigateur) listant, par ordre chronologique :

```text
┌──────────────────────────────────────────────┐
│ Debug                                        │
├──────────────────────────────────────────────┤
│ 12:03:41.102 → session/new           {...}   │
│ 12:03:41.340 ← session/new → ok      {...}   │
│ 12:03:42.001 ← update: tool_call     {...}   │
│ 12:03:42.050 ⚠ unknown update type: "foo"    │
└──────────────────────────────────────────────┘
```

Chaque entrée doit contenir au minimum :

* timestamp ;
* direction (`→` requête sortante, `←` événement/réponse entrante) ;
* type / méthode ACP ;
* payload brut (JSON-RPC non transformé).

### Lien avec les extensions inconnues (§14)

Toute capacité, update ou extension ignorée au sens du §14 doit obligatoirement produire une entrée `warn` dans le panneau Debug, avec le payload brut reçu. Le but : ignorer sans planter, mais jamais ignorer en silence quand le debug est actif.

### Contraintes

* Le mode debug ne doit rien changer au comportement fonctionnel du client (pas de side-effect, uniquement de l'observation).
* Les logs doivent pouvoir être exportés/copiés (ex : bouton `[ Copy logs ]`) pour joindre à un rapport de bug.
* Aucune donnée sensible (tokens, secrets transmis dans les options de config) ne doit apparaître en clair dans les logs ; prévoir un masquage basique si de telles valeurs sont identifiables.
* Le logging doit rester côté client uniquement pour ce MVP ; le logging serveur (bridge) est hors périmètre de cette section.

---

on va travailler sur 3 agentes pour démarrer kilo, puis opencode

