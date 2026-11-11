import { useRef, useState } from "react";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface ZoneRect {
  display: Rect; // coordonnees ecran, pour l'overlay
  source: Rect; // coordonnees mises a l'echelle de la source, deja calculees au tracer
}

export function RegionSelector({
  frameUrl,
  videoW,
  onDone,
}: {
  frameUrl: string;
  videoW: number;
  onDone: (cam: Rect, game: Rect) => void;
}) {
  const img = useRef<HTMLImageElement>(null);
  const [zone, setZone] = useState<"cam" | "game">("cam");
  const [rects, setRects] = useState<{ cam?: ZoneRect; game?: ZoneRect }>({});
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [cur, setCur] = useState<Rect | null>(null);

  const pos = (e: React.MouseEvent) => {
    const r = img.current?.getBoundingClientRect();
    return { x: e.clientX - (r?.left ?? 0), y: e.clientY - (r?.top ?? 0) };
  };
  const finishDrag = () => {
    if (cur && cur.w > 10 && cur.h > 10) {
      // echelle calculee ICI, au moment du trace — pas de biais si la fenetre
      // est redimensionnee entre le trace de la cam et celui du gameplay
      const k = videoW / (img.current?.clientWidth || 1);
      const source: Rect = {
        x: Math.round(cur.x * k),
        y: Math.round(cur.y * k),
        w: Math.round(cur.w * k),
        h: Math.round(cur.h * k),
      };
      const next = { ...rects, [zone]: { display: cur, source } };
      setRects(next);
      if (zone === "cam") {
        setZone("game");
      } else if (next.cam && next.game) {
        onDone(next.cam.source, next.game.source);
      }
    }
    setDrag(null);
    setCur(null);
  };

  const box = (r: Rect, color: string) => (
    <div
      className={`pointer-events-none absolute border-2 ${color}`}
      style={{ left: r.x, top: r.y, width: r.w, height: r.h }}
    />
  );
  return (
    <div>
      <p className="mb-2 text-sm text-amber-300">
        {zone === "cam"
          ? "Étape 1 sur 2 : trace un rectangle sur ta FACECAM"
          : "Étape 2 sur 2 : trace un rectangle sur le GAMEPLAY"}
      </p>
      <div
        role="application"
        aria-label={
          zone === "cam"
            ? "Zone de traçage du rectangle facecam, glisser la souris pour dessiner"
            : "Zone de traçage du rectangle gameplay, glisser la souris pour dessiner"
        }
        className="relative inline-block cursor-crosshair select-none"
        onMouseDown={(e) => setDrag(pos(e))}
        onMouseMove={(e) => {
          if (!drag) return;
          const p = pos(e);
          setCur({
            x: Math.min(drag.x, p.x),
            y: Math.min(drag.y, p.y),
            w: Math.abs(p.x - drag.x),
            h: Math.abs(p.y - drag.y),
          });
        }}
        onMouseUp={finishDrag}
      >
        <img
          ref={img}
          src={frameUrl}
          alt="Frame de la VOD pour tracer les zones cam et gameplay"
          className="max-w-full"
          draggable={false}
        />
        {rects.cam && box(rects.cam.display, "border-amber-400")}
        {rects.game && box(rects.game.display, "border-sky-400")}
        {cur &&
          box(cur, zone === "cam" ? "border-amber-400" : "border-sky-400")}
      </div>
    </div>
  );
}
