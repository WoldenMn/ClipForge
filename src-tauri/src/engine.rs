use std::path::Path;
use std::process::Command as StdCommand;
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

use crate::backend_path::{backend_dir, pointer_hint};
use crate::engine_owner::{self, EngineLock};

/// Point unique a changer pour passer a un moteur empaquete (PyInstaller).
const ENGINE_PROGRAM: &str = "uv";
const ENGINE_ARGS: [&str; 7] = [
    "run", "uvicorn", "clipforge.main:app",
    "--host", "127.0.0.1", "--port", "8765",
];
const ENGINE_ADDR: &str = "127.0.0.1:8765";
const HEALTH_PATH: &str = "/api/health";
const READY_TIMEOUT_S: u64 = 60;
const PROBE_TIMEOUT_S: u64 = 5;

/// URL servie par le moteur, a charger dans la fenetre principale.
pub fn engine_url() -> String {
    format!("http://{ENGINE_ADDR}")
}

/// Etat partage du moteur. L'erreur y est conservee parce que l'ecran de
/// demarrage enregistre son ecouteur apres le setup : un evenement seul se perd.
///
/// L'enfant lance est garde entier, et pas seulement son PID : tant que ce
/// handle vit, Windows n'attribue son numero a personne d'autre, donc l'arret
/// final ne peut pas tomber sur un processus sans rapport.
#[derive(Default)]
pub struct EngineState {
    pub child: Mutex<Option<CommandChild>>,
    pub lock: Mutex<Option<EngineLock>>,
    pub error: Mutex<Option<String>>,
}

/// Lue par l'ecran de demarrage a son chargement, pour rattraper une erreur
/// survenue avant que son ecouteur existe.
#[tauri::command]
pub fn engine_status(state: State<'_, EngineState>) -> Option<String> {
    state.error.lock().unwrap().clone()
}

/// Le moteur sert l'interface depuis frontend/dist. Sans ce dossier il repond
/// un 404 brut : autant le dire avant de lancer quoi que ce soit.
fn check_frontend(backend: &Path) -> Result<(), String> {
    let built = backend
        .parent()
        .map(|root| root.join("frontend").join("dist").join("index.html"))
        .is_some_and(|index| index.is_file());
    if built {
        Ok(())
    } else {
        Err("Interface non construite. Lance : cd frontend && bun install && bun run build".into())
    }
}

/// Lance le moteur. Retourne l'enfant pour pouvoir l'arreter a la fermeture.
pub fn spawn_engine(app: &AppHandle) -> Result<CommandChild, String> {
    let dir = backend_dir(app).ok_or_else(|| {
        format!(
            "Moteur introuvable. Lance ClipForge une fois depuis le dossier du projet, \
             ou definis CLIPFORGE_BACKEND_DIR, ou ecris le chemin du dossier backend dans :\n{}",
            pointer_hint(app)
        )
    })?;
    check_frontend(&dir)?;
    let (_rx, child) = app
        .shell()
        .command(ENGINE_PROGRAM)
        .args(ENGINE_ARGS)
        .current_dir(dir)
        .spawn()
        .map_err(|e| format!("Impossible de lancer le moteur : {e}"))?;
    Ok(child)
}

/// Sonde /api/health jusqu'a reponse ou expiration.
pub fn wait_ready() -> Result<(), String> {
    let deadline = std::time::Instant::now() + Duration::from_secs(READY_TIMEOUT_S);
    let mut last = String::new();
    while std::time::Instant::now() < deadline {
        match health_probe() {
            Ok(()) => return Ok(()),
            Err(e) => last = e,
        }
        std::thread::sleep(Duration::from_millis(300));
    }
    Err(format!("Le moteur n'a pas repondu en {READY_TIMEOUT_S}s. {last}"))
}

/// Requete GET minimale sans dependance HTTP supplementaire.
pub fn health_probe() -> Result<(), String> {
    use std::io::{Read, Write};
    use std::net::TcpStream;

    let timeout = Duration::from_secs(PROBE_TIMEOUT_S);
    let mut stream = TcpStream::connect(ENGINE_ADDR).map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|e| e.to_string())?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|e| e.to_string())?;
    stream
        .write_all(format!("GET {HEALTH_PATH} HTTP/1.0\r\nHost: {ENGINE_ADDR}\r\n\r\n").as_bytes())
        .map_err(|e| e.to_string())?;
    let mut buf = Vec::new();
    stream.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    if engine_owner::is_engine_response(&buf) {
        Ok(())
    } else {
        Err("reponse inattendue sur le port du moteur".into())
    }
}

/// Arrete le moteur, si cette instance en est responsable.
///
/// Le processus arrete est celui qui DETIENT LE PORT, jamais celui qu'on croit
/// avoir lance : quand un moteur repondait deja a l'ouverture, notre `uv` est
/// mort dans la seconde faute de port libre, et son PID ne designe plus le
/// moteur qui sert l'interface. C'est ce decalage qui rendait un moteur
/// orphelin immortel — chaque instance suivante l'adoptait sans le savoir et
/// arretait, en partant, un processus deja mort.
///
/// Le verrou est retente ici : son proprietaire a pu mourir depuis le
/// demarrage, auquel cas la derniere instance debout herite du moteur et doit
/// l'eteindre. S'il reste pris, une autre fenetre vivante s'en sert : on n'y
/// touche pas.
pub fn stop_engine(app: &AppHandle) {
    let state = app.state::<EngineState>();
    let mut lock = state.lock.lock().unwrap();
    if lock.is_none() {
        *lock = engine_owner::lock_path(app).and_then(|path| EngineLock::acquire(&path));
    }
    // L'arbitrage entre fenetres ne porte que sur le moteur partage. Le port
    // n'est repris que si le verrou est a nous ; l'enfant qu'on a lance nous
    // appartient dans tous les cas et part plus bas, meme sans verrou.
    if lock.is_some() {
        // Le detenteur du port n'est arrete que s'il repond encore comme le
        // moteur : notre moteur a pu mourir en cours de session et un programme
        // tiers reprendre le port, et celui-la ne doit jamais tomber avec nous.
        // La verification et l'arret ne sont pas atomiques, mais l'intervalle
        // se compte en millisecondes contre une session entiere sans garde.
        if health_probe().is_ok() {
            if let Some(pid) = engine_owner::listener_pid(ENGINE_ADDR) {
                kill_tree(pid);
            }
        }
    }
    // Le moteur a pu mourir en laissant son lanceur derriere lui : le port ne
    // designe alors plus personne, mais l'enfant lance, lui, est toujours la.
    let child = state.child.lock().unwrap().take();
    if let Some(child) = child {
        kill_tree(child.pid());
    }
}

/// Arrete le moteur ET ses descendants (ffmpeg, workers).
/// Sous Windows, tuer le parent ne suffit pas : taskkill /T s'en charge.
pub fn kill_tree(pid: u32) {
    let _ = StdCommand::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .output();
}

/// Conserve l'erreur dans l'etat ET l'emet : l'etat couvre l'ecran de demarrage
/// pas encore charge, l'evenement couvre celui qui l'est deja.
pub fn set_error(app: &AppHandle, detail: &str) {
    *app.state::<EngineState>().error.lock().unwrap() = Some(detail.to_string());
    let _ = app.emit(
        "engine-status",
        serde_json::json!({ "state": "error", "detail": detail }),
    );
}
