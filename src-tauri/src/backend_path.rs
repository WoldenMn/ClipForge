use std::path::{Path, PathBuf};

use tauri::{AppHandle, Manager};

/// Fichier-pointeur vers le dossier du projet, dans le dossier de configuration
/// de l'app (`%APPDATA%\com.clipforge.app`). Ce chemin est le meme pour le
/// binaire lance depuis le projet et pour la copie installee : c'est ce qui
/// permet a la seconde de retrouver ce que la premiere a vu.
const BACKEND_POINTER: &str = "backend-dir.txt";

fn is_backend(dir: &Path) -> bool {
    dir.join("pyproject.toml").is_file()
}

fn pointer_path(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|d| d.join(BACKEND_POINTER))
}

/// Chemin du pointeur, pour le message d'erreur : sans lui l'utilisateur d'une
/// app installee n'a aucun moyen de savoir quel fichier renseigner.
pub fn pointer_hint(app: &AppHandle) -> String {
    pointer_path(app)
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| format!("<dossier de configuration>\\{BACKEND_POINTER}"))
}

/// Note le dossier localise pour que l'app INSTALLEE le retrouve : posee dans
/// Program Files, elle n'a le projet dans aucun de ses ancetres et n'a aucun
/// autre moyen de le deviner. Une ecriture qui echoue ne casse pas le lancement
/// en cours ; c'est l'installation qui, faute de pointeur, affichera l'erreur
/// explicite de `spawn_engine`.
fn remember_backend(app: &AppHandle, dir: &Path) {
    let Some(path) = pointer_path(app) else {
        eprintln!("ClipForge : dossier de configuration introuvable, pointeur non ecrit");
        return;
    };
    let deja_a_jour =
        std::fs::read_to_string(&path).is_ok_and(|contenu| Path::new(contenu.trim()) == dir);
    if deja_a_jour {
        return;
    }
    let ecriture = std::fs::create_dir_all(path.parent().unwrap_or(&path))
        .and_then(|()| std::fs::write(&path, dir.to_string_lossy().as_bytes()));
    if let Err(e) = ecriture {
        eprintln!("ClipForge : ecriture de {} impossible : {e}", path.display());
    }
}

fn read_pointer(app: &AppHandle) -> Option<PathBuf> {
    let contenu = std::fs::read_to_string(pointer_path(app)?).ok()?;
    let dir = PathBuf::from(contenu.trim());
    is_backend(&dir).then_some(dir)
}

/// Localise le dossier du moteur sans dependre du repertoire courant :
/// l'app est lancee au double-clic, ou le cwd est celui de l'explorateur.
pub fn backend_dir(app: &AppHandle) -> Option<PathBuf> {
    // 1) variable d'environnement (echappatoire documentee)
    if let Ok(p) = std::env::var("CLIPFORGE_BACKEND_DIR") {
        let p = PathBuf::from(p);
        if is_backend(&p) {
            return Some(p);
        }
    }
    // 2) remontee depuis l'executable (binaire lance depuis le projet). Elle
    //    passe avant le pointeur : elle constate le projet la ou il est, donc
    //    elle corrige un pointeur devenu faux apres un deplacement du dossier.
    if let Ok(exe) = std::env::current_exe() {
        for dir in exe.ancestors() {
            let candidate = dir.join("backend");
            if is_backend(&candidate) {
                remember_backend(app, &candidate);
                return Some(candidate);
            }
        }
    }
    // 3) fichier-pointeur : seul recours de l'app installee, qui vit hors du projet
    if let Some(dir) = read_pointer(app) {
        return Some(dir);
    }
    // 4) ressources embarquees (futur empaquetage autonome)
    app.path()
        .resource_dir()
        .ok()
        .map(|r| r.join("backend"))
        .filter(|p| is_backend(p))
}
