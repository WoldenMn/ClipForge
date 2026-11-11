/// <reference lib="dom" />
import { describe, expect, test } from "bun:test";
import { act, render, screen } from "@testing-library/react";
import { HealthBanner } from "../HealthBanner";

interface Controle {
  cle: string;
  ok: boolean;
  detail: string;
  action: string;
}

const FFMPEG_TROP_ANCIEN: Controle = {
  cle: "ffmpeg_montage",
  ok: false,
  detail:
    "trop ancien (6.1) : le montage V6 demande ffmpeg 7.0 ou plus (-/filter_complex), ses rendus echoueraient",
  action:
    "Mets ffmpeg a jour (winget upgrade Gyan.FFmpeg) puis relance ClipForge : sous la 7.0, chaque rendu du montage echoue sur « Error parsing global options », un message qui ne dit pas d'ou il vient.",
};

function installFetchMock(controles: Controle[]): void {
  globalThis.fetch = (async (_input: RequestInfo | URL, _init?: RequestInit) =>
    ({
      ok: true,
      json: async () => ({ controles }),
    }) as Response) as typeof fetch;
}

describe("HealthBanner — contrôle de version de ffmpeg", () => {
  test("affiche le contrôle du montage avec son libellé et son remède", async () => {
    installFetchMock([FFMPEG_TROP_ANCIEN]);
    render(<HealthBanner />);

    await screen.findByText("Version de ffmpeg");
    expect(screen.getByText(/trop ancien \(6\.1\)/)).not.toBeNull();
    expect(screen.getByText(/winget upgrade Gyan\.FFmpeg/)).not.toBeNull();
  });

  /* Le contrôle au vert n'est pas une alerte : la bannière ne montre que les
     échecs, et sans échec elle ne monte rien du tout. */
  test("ne montre rien quand le contrôle du montage passe", async () => {
    installFetchMock([
      { cle: "ffmpeg_montage", ok: true, detail: "ok", action: "" },
    ]);
    let container!: HTMLElement;
    await act(async () => {
      ({ container } = render(<HealthBanner />));
    });
    expect(container.textContent).toBe("");
  });
});
