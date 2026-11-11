# Review instructions — ClipForge

Deux moitiés distinctes : un backend Python (`backend/`, `uv` + `ruff` + `pytest`) et un frontend Tauri/React (`frontend/`, `src-tauri/`). La CI passe `uv run ruff check .`, `uv run pytest -q -m "not integration"` et `bun run lint`. Deux conséquences pour la revue : ne pas refaire le travail de ruff, et **regarder de près les tests marqués `integration`, qui ne tournent pas en CI**.

## Ce que « Important » veut dire ici

### Le trou de la CI

🔴 `pytest -q -m "not integration"` exclut les tests d'intégration. Une PR qui modifie du code couvert **uniquement** par des tests `integration` traverse la CI sans être testée. C'est Important, et c'est le premier réflexe de revue sur ce dépôt : dire quels tests couvrent le diff, et lesquels ne tourneront pas.

### Traitement média et sous-processus

- Un argument construit par concaténation de chaînes pour `ffmpeg` ou tout binaire externe, à partir d'une valeur venue de l'utilisateur ou d'un nom de fichier. Passer une liste d'arguments, jamais `shell=True`.
- Un `subprocess` dont le code de retour n'est pas vérifié : un `ffmpeg` qui échoue rend un fichier vide ou tronqué, et le pipeline continue comme si de rien n'était. **`returncode == 0` avec une sortie vide n'est pas un succès** — c'est le mode de panne le plus dangereux d'une dépendance externe, parce qu'il est indiscernable d'un travail bien fait. Vérifier la présence du résultat attendu, pas le code de retour.
- Un fichier temporaire non nettoyé sur le chemin d'erreur.
- Un chemin média venu du frontend utilisé côté Python sans être contraint à un dossier attendu.

### Erreurs avalées

- Python : `except: pass`, `except Exception: pass`, `except` qui rend une valeur par défaut sans trace.
- TypeScript : `catch {}` vide, `.catch(() => {})`.

### La frontière Tauri

- Une commande `#[tauri::command]` qui prend un chemin venant du frontend sans le contraindre.
- Une permission élargie dans les capabilities sans justification dans la PR.

### Le reste

- Fichier au-delà de 300 lignes sans motif dit dans la PR.
- Chemin en dur avec séparateur POSIX : ce projet cible Windows desktop.

## Plafond de nits

Cinq. Au-delà, une ligne de décompte dans le résumé.

## Ne pas signaler

| Chemin | Motif |
|---|---|
| `_poubelle/**` | Zone de mise au rebut du poste |
| `data/**` | Données de travail, pas du code |
| `assets/**` (binaires) | Médias |
| `src-tauri/target/**`, `node_modules/**`, `.venv/**`, `__pycache__/**` | Générés |
| `uv.lock`, `bun.lock`, `Cargo.lock` | Lockfiles |

Ne pas redire ce que `ruff check` remonte : la CI bloque dessus.

## Toujours vérifier

- **Quels tests couvrent le diff**, et lesquels sont marqués `integration` donc absents de la CI. C'est la remarque la plus utile qu'une revue puisse faire ici.
- **Chaque appel à un binaire externe** : forme des arguments, vérification du résultat produit, nettoyage sur le chemin d'erreur.
- **Chaque dépendance ajoutée** côté `pyproject.toml` comme `package.json` : ce qu'elle apporte que le code existant ne faisait pas.
- **Plateforme** : Windows desktop uniquement.

## Barre de preuve

Toute affirmation de comportement se cite en `fichier:ligne`. Ne jamais déduire d'un nom de fonction ou de module ce qu'il fait.

Pour une affirmation « ce chemin n'est pas testé », nommer le fichier de test attendu et dire qu'il n'existe pas, ou nommer le marqueur qui l'exclut. Une affirmation de couverture sans l'une des deux est dégradée en avertissement.

## Re-review

Deuxième passe et suivantes : Important uniquement. Les nits de la première passe ne sont pas re-postés.

## Forme du résumé

Ouvrir par un décompte d'une ligne, du type `1 sous-processus, 2 non couverts en CI, 3 nits`. Toujours dire, même quand tout va bien, si le diff est couvert par des tests qui tournent réellement en CI.
