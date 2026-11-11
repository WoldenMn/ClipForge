#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend_path;
mod engine;
mod engine_owner;

use engine::EngineState;
use tauri::{Manager, Url, WindowEvent};

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(EngineState::default())
        .invoke_handler(tauri::generate_handler![engine::engine_status])
        .setup(|app| {
            let handle = app.handle().clone();
            {
                let state = app.state::<EngineState>();
                // Devenir responsable du moteur, si personne ne l'est deja. Ce
                // verrou seul decide qui l'arretera a la fermeture.
                *state.lock.lock().unwrap() = engine_owner::lock_path(&handle)
                    .and_then(|path| engine_owner::EngineLock::acquire(&path));
                // Un moteur qui repond deja est repris tel quel : en lancer un
                // second echouerait faute de port libre, et on croirait tenir un
                // processus qui serait mort aussitot.
                if engine::health_probe().is_err() {
                    match engine::spawn_engine(&handle) {
                        Ok(child) => *state.child.lock().unwrap() = Some(child),
                        Err(detail) => {
                            engine::set_error(&handle, &detail);
                            return Ok(());
                        }
                    }
                }
            }
            std::thread::spawn(move || match engine::wait_ready() {
                Ok(()) => {
                    // les fenetres de la config sont creees avant le setup : le premier
                    // chargement de "main" a echoue, moteur pas encore la. On le rejoue.
                    if let Some(w) = handle.get_webview_window("main") {
                        if let Ok(url) = Url::parse(&engine::engine_url()) {
                            let _ = w.navigate(url);
                        }
                    }
                    // destroy() et non close() : close() emet CloseRequested,
                    // ce qui arreterait le moteur qu'on vient d'attendre.
                    if let Some(w) = handle.get_webview_window("splashscreen") {
                        let _ = w.destroy();
                    }
                    if let Some(w) = handle.get_webview_window("main") {
                        let _ = w.show();
                    }
                }
                Err(detail) => engine::set_error(&handle, &detail),
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                let app = window.app_handle();
                engine::stop_engine(app);
                // splash ferme avant l'affichage de l'interface : la fenetre principale
                // reste masquee, donc l'app survivrait sans fenetre utilisable.
                let main_shown = app
                    .get_webview_window("main")
                    .and_then(|w| w.is_visible().ok())
                    .unwrap_or(false);
                if window.label() == "splashscreen" && !main_shown {
                    app.exit(0);
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("erreur au lancement de ClipForge");
}
