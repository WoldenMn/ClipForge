//! Reconnaitre le moteur, savoir quel processus le fait tourner, et etre celui
//! qui en repond.

use std::fs::{File, OpenOptions};
use std::os::windows::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::Command as StdCommand;

use tauri::{AppHandle, Manager};

/// Verrou de propriete du moteur, dans le dossier de configuration de l'app.
const LOCK_FILE: &str = "engine.lock";
/// FILE_SHARE_NONE : tant que ce handle vit, personne d'autre n'ouvre le fichier.
const NO_SHARING: u32 = 0;
/// ERROR_SHARING_VIOLATION : le cas normal ou une autre instance tient le moteur.
const ERROR_SHARING_VIOLATION: i32 = 32;

/// Marque « cette instance est responsable du moteur ».
///
/// Un handle de fichier ouvert sans partage, et non un PID ecrit dans un
/// fichier : le systeme ferme le handle meme quand l'application meurt
/// brutalement (plantage, « Fin de tache »), donc le verrou ne peut pas survivre
/// a son proprietaire. Un PID ecrit, lui, resterait lisible apres le plantage et
/// pourrait meme avoir ete reattribue a un autre programme.
pub struct EngineLock(#[allow(dead_code)] File);

/// Chemin du verrou. Partage par toutes les instances : le binaire du projet et
/// la copie installee ont le meme identifiant, donc le meme dossier.
pub fn lock_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join(LOCK_FILE))
}

impl EngineLock {
    /// Some si personne d'autre ne tient le moteur, None sinon.
    pub fn acquire(path: &Path) -> Option<Self> {
        if let Some(parent) = path.parent() {
            if let Err(e) = std::fs::create_dir_all(parent) {
                eprintln!("ClipForge : {} inaccessible ({e}), moteur non verrouille", parent.display());
                return None;
            }
        }
        match OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .share_mode(NO_SHARING)
            .open(path)
        {
            Ok(fichier) => Some(Self(fichier)),
            Err(e) if e.raw_os_error() == Some(ERROR_SHARING_VIOLATION) => None,
            Err(e) => {
                eprintln!("ClipForge : verrou {} illisible ({e}), moteur non verrouille", path.display());
                None
            }
        }
    }
}

/// Corps servi par /api/health. Un code 200 ne suffit pas a reconnaitre le
/// moteur : le reconnaitre donne le droit de l'arreter a la fermeture, et ce
/// droit ne doit jamais s'exercer sur un programme tiers qui occuperait le port.
const HEALTH_BODY: &str = "{\"ok\":true}";

/// Vrai si la reponse brute vient bien d'un moteur ClipForge.
pub fn is_engine_response(brut: &[u8]) -> bool {
    let texte = String::from_utf8_lossy(brut);
    let succes = texte.starts_with("HTTP/1.1 200") || texte.starts_with("HTTP/1.0 200");
    succes && texte.contains(HEALTH_BODY)
}

/// PID du processus qui detient reellement le port.
///
/// Le systeme est la seule source de verite : le PID retenu au lancement peut
/// designer un processus mort — le notre, sorti aussitot parce que le port etait
/// deja pris par un moteur precedent.
pub fn listener_pid(addr: &str) -> Option<u32> {
    match StdCommand::new("netstat").args(["-ano", "-p", "TCP"]).output() {
        Ok(sortie) => parse_listener_pid(&String::from_utf8_lossy(&sortie.stdout), addr),
        Err(e) => {
            eprintln!("ClipForge : netstat indisponible ({e}), moteur non localise");
            None
        }
    }
}

/// Colonnes de netstat : proto, adresse locale, adresse distante, etat, PID.
///
/// L'adresse est comparee a la colonne LOCALE : le port du moteur apparait aussi
/// en colonne distante chez chaque client connecte, dont le PID n'a rien a voir.
/// L'etat LISTENING ecarte les sockets residuels (TIME_WAIT), qui gardent
/// l'adresse du moteur avec un PID 0 apres sa mort.
fn parse_listener_pid(netstat: &str, addr: &str) -> Option<u32> {
    netstat.lines().find_map(|ligne| {
        let mut colonnes = ligne.split_whitespace();
        let (_proto, locale, _distante, etat) = (
            colonnes.next()?,
            colonnes.next()?,
            colonnes.next()?,
            colonnes.next()?,
        );
        if locale != addr || etat != "LISTENING" {
            return None;
        }
        colonnes.next()?.parse().ok()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Sortie reelle de `netstat -ano -p TCP` (Windows 11 fr), reduite.
    const NETSTAT: &str = "\r
Connexions actives\r
\r
  Proto  Adresse locale         Adresse distante       Etat           PID\r
  TCP    127.0.0.1:8765         0.0.0.0:0              LISTENING       63180\r
  TCP    127.0.0.1:8765         127.0.0.1:10401        ESTABLISHED     63180\r
  TCP    127.0.0.1:10401        127.0.0.1:8765         ESTABLISHED     42632\r
  TCP    127.0.0.1:9999         0.0.0.0:0              LISTENING       1234\r
";

    #[test]
    fn trouve_le_processus_qui_detient_le_port() {
        assert_eq!(parse_listener_pid(NETSTAT, "127.0.0.1:8765"), Some(63180));
    }

    #[test]
    fn ignore_le_client_connecte_au_moteur() {
        // 42632 n'ecoute pas : le port du moteur est son adresse DISTANTE. Le
        // prendre pour le moteur reviendrait a tuer une application tierce.
        assert_ne!(parse_listener_pid(NETSTAT, "127.0.0.1:8765"), Some(42632));
    }

    #[test]
    fn aucun_processus_quand_le_port_est_libre() {
        assert_eq!(parse_listener_pid(NETSTAT, "127.0.0.1:8766"), None);
    }

    #[test]
    fn ignore_les_sockets_residuels_sans_proprietaire() {
        // Apres la mort du moteur, des lignes TIME_WAIT gardent son adresse
        // locale avec le PID 0 : il n'y a plus personne a arreter.
        let residu = "  TCP    127.0.0.1:8765    127.0.0.1:46257    TIME_WAIT       0\r\n";
        assert_eq!(parse_listener_pid(residu, "127.0.0.1:8765"), None);
    }

    const REPONSE_MOTEUR: &[u8] =
        b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n{\"ok\":true}";

    #[test]
    fn reconnait_la_reponse_du_moteur() {
        assert!(is_engine_response(REPONSE_MOTEUR));
    }

    #[test]
    fn refuse_un_serveur_tiers_sur_le_meme_port() {
        // Un 200 ne suffit pas : adopter un moteur donne le droit de le tuer, et
        // ce droit ne doit jamais s'exercer sur le programme d'un autre.
        let tiers = b"HTTP/1.1 200 OK\r\ncontent-type: text/html\r\n\r\n<html>Bienvenue</html>";
        assert!(!is_engine_response(tiers));
    }

    #[test]
    fn refuse_une_reponse_en_erreur() {
        let erreur = b"HTTP/1.1 503 Service Unavailable\r\n\r\n{\"ok\":true}";
        assert!(!is_engine_response(erreur));
    }

    #[test]
    fn le_verrou_refuse_une_seconde_instance_puis_se_libere() {
        let path = std::env::temp_dir().join(format!("clipforge-verrou-{}.lock", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let premier = EngineLock::acquire(&path).expect("premier verrou pris");
        assert!(EngineLock::acquire(&path).is_none(), "deux instances ne peuvent pas tenir le moteur");
        drop(premier);
        assert!(EngineLock::acquire(&path).is_some(), "verrou rendu avec son proprietaire");
        let _ = std::fs::remove_file(&path);
    }
}
