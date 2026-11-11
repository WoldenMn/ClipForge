import {
  type Ref,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import type { SceneProfile } from "../api/client";
import { IconPlay } from "./ui/icons";

const PREVIEW_W = 270;
const TOP_H = 144;
const BOT_H = 336; // 1080x1920 / 4
/** HTMLMediaElement.HAVE_METADATA : la duree et les dimensions sont connues. */
const HAVE_METADATA = 1;

/** Ce que l'editeur pilote de l'exterieur : la touche Espace, et rien d'autre. */
export interface PreviewHandle {
  basculer: () => void;
}

export interface PreviewVerticalProps {
  src: string;
  profile: SceneProfile | null;
  startS: number;
  endS: number;
  videoW: number;
  videoH: number;
  ref?: Ref<PreviewHandle>;
}

export function PreviewVertical({
  src,
  profile,
  startS,
  endS,
  videoW,
  videoH,
  ref,
}: PreviewVerticalProps) {
  /* Le lecteur de reference est tenu en ETAT, pas en ref : sans profil de scene
     aucune balise video n'est montee, et un clip nait justement sans profil. Un
     useRef ne previent personne quand la balise apparait — l'effet ne se
     rejouait pas, donc aucun ecouteur n'etait pose : indicateur lecture/pause
     muet et boucle sur les bornes jamais armee. */
  const [master, setMaster] = useState<HTMLVideoElement | null>(null);
  const slave = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    const m = master;
    if (!m) return;
    /* Un currentTime pose avant les metadonnees est ignore sans bruit : on le
       repose a leur arrivee, sinon l'apercu demarre au debut du stream — a deux
       heures du moment qu'on regle. */
    const cadrer = () => {
      m.currentTime = startS;
    };
    if (m.readyState >= HAVE_METADATA) cadrer();
    m.addEventListener("loadedmetadata", cadrer);
    const onTime = () => {
      if (m.currentTime > endS) m.currentTime = startS; // boucle sur le clip
      const s = slave.current;
      if (s && Math.abs(s.currentTime - m.currentTime) > 0.3) {
        s.currentTime = m.currentTime;
      }
    };
    const sync = (play: boolean) => () => {
      setPlaying(play);
      const s = slave.current;
      if (!s) return;
      if (play) void s.play().catch(() => {});
      else s.pause();
    };
    const onPlay = sync(true);
    const onPause = sync(false);
    m.addEventListener("timeupdate", onTime);
    m.addEventListener("play", onPlay);
    m.addEventListener("pause", onPause);
    return () => {
      m.removeEventListener("loadedmetadata", cadrer);
      m.removeEventListener("timeupdate", onTime);
      m.removeEventListener("play", onPlay);
      m.removeEventListener("pause", onPause);
    };
  }, [startS, endS, master]);

  // Sans profil de scene, aucune balise video n'est montee : la poignee ne
  // trouve rien a basculer et ne fait rien, plutot que de lever une erreur.
  const toggle = useCallback(() => {
    if (!master) return;
    if (master.paused) void master.play().catch(() => {});
    else master.pause();
  }, [master]);
  useImperativeHandle(ref, () => ({ basculer: toggle }), [toggle]);

  const crop = (zone: "cam" | "game", outH: number) => {
    if (!profile) return {};
    const x = zone === "cam" ? profile.cam_x : profile.game_x;
    const y = zone === "cam" ? profile.cam_y : profile.game_y;
    const w = zone === "cam" ? profile.cam_w : profile.game_w;
    const h = zone === "cam" ? profile.cam_h : profile.game_h;
    const sx = PREVIEW_W / w;
    const sy = outH / h;
    return {
      width: videoW,
      height: videoH,
      maxWidth: "none",
      transform: `scale(${sx}, ${sy}) translate(${-x}px, ${-y}px)`,
      transformOrigin: "0 0",
    } as const;
  };

  if (!profile) {
    return (
      <div className="flex h-[480px] w-[270px] shrink-0 items-center justify-center rounded-card border border-dashed border-line-strong bg-surface px-5 text-center text-sm text-ink-soft">
        Choisis un profil de scène pour voir l'aperçu vertical.
      </div>
    );
  }
  return (
    <div className="w-[270px] shrink-0 overflow-hidden rounded-card border border-line bg-well shadow-card">
      <button
        type="button"
        aria-pressed={playing}
        aria-label={playing ? "Mettre en pause" : "Lire"}
        className="group relative block w-full cursor-pointer border-0 p-0"
        onClick={toggle}
      >
        <div style={{ width: PREVIEW_W, height: TOP_H, overflow: "hidden" }}>
          <video ref={setMaster} src={src} style={crop("cam", TOP_H)}>
            <track kind="captions" />
          </video>
        </div>
        <div style={{ width: PREVIEW_W, height: BOT_H, overflow: "hidden" }}>
          <video ref={slave} src={src} muted style={crop("game", BOT_H)}>
            <track kind="captions" />
          </video>
        </div>
        {!playing && (
          <span className="absolute inset-0 flex items-center justify-center">
            <span className="flex size-14 items-center justify-center rounded-full bg-black/65 text-ink transition duration-150 ease-soft group-hover:bg-accent group-hover:text-on-accent">
              <IconPlay className="size-6" />
            </span>
          </span>
        )}
      </button>
    </div>
  );
}
