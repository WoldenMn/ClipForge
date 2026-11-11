/// <reference lib="dom" />
import { describe, expect, test } from "bun:test";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SettingsPanel } from "../SettingsPanel";

interface FetchCall {
  url: string;
  method: string;
  body: unknown;
}

/* Reglages d'une installation existante : aucune ligne "montage" ni
   "cut_style" (elles n'existaient pas avant V6). C'est exactement le cas que
   le piege du defaut moteur doit couvrir. */
const EXISTING_SETTINGS: Record<string, string> = {
  weight_audio: "1.0",
  channel_handle: "streamer",
};

function jsonResponse(data: unknown): Response {
  return { ok: true, json: async () => data } as Response;
}

const PROFILES = [
  { id: 1, name: "Plateau GoR" },
  { id: 2, name: "WOLDENM OBS 1440p" },
];

const GET_ERROR = "base verrouillee";

/** Remplace fetch par un mock qui repond aux 3 routes utilisees par le
 * panneau et enregistre chaque appel pour inspection ulterieure. `settings`
 * a null : le GET des reglages echoue (HTTP 500) ; a "en attente" : le GET ne
 * repond jamais, comme pendant le premier rendu. */
function installFetchMock(
  settings: Record<string, string> | null | "en attente",
): FetchCall[] {
  const calls: FetchCall[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ url, method, body });

    if (url.endsWith("/api/settings/env")) {
      return jsonResponse({ anthropic_key: "absente" });
    }
    if (url.endsWith("/api/profiles")) {
      return jsonResponse(PROFILES);
    }
    if (url.endsWith("/api/settings") && method === "GET") {
      if (settings === "en attente") {
        return new Promise<Response>(() => {});
      }
      if (settings === null) {
        return {
          ok: false,
          status: 500,
          statusText: "Internal Server Error",
          json: async () => ({ detail: GET_ERROR }),
        } as Response;
      }
      return jsonResponse(settings);
    }
    if (url.endsWith("/api/settings") && method === "PATCH") {
      return jsonResponse(body.values);
    }
    throw new Error(`URL non mockee dans ce test : ${method} ${url}`);
  }) as typeof fetch;
  return calls;
}

describe("SettingsPanel — montage et rythme des coupes", () => {
  test("affiche « Rythme des coupes » avec ses 3 options", async () => {
    installFetchMock(EXISTING_SETTINGS);
    const { container } = render(<SettingsPanel />);

    await screen.findByText("Rythme des coupes");
    const select = container.querySelector<HTMLSelectElement>("#cut_style");
    expect(select).not.toBeNull();

    const options = Array.from(select?.options ?? []);
    expect(options.map((o) => o.value)).toEqual([
      "doux",
      "standard",
      "nerveux",
    ]);
    expect(options.map((o) => o.textContent)).toEqual([
      "Doux",
      "Standard",
      "Nerveux",
    ]);
  });

  /* PIEGE : le moteur traite un reglage "montage" absent comme actif ("1").
     Une implementation naive (`settings[f.key] ?? ""`) affiche la case
     DECOCHEE dans ce cas precis, alors que le rendu est bien coupe — le
     panneau mentirait sur ce que fait l'appli. Ce test echoue contre cette
     implementation naive et ne passe qu'avec le repli `f.defaut`. */
  test("montage absent des reglages stockes s'affiche COCHE (defaut moteur actif)", async () => {
    installFetchMock(EXISTING_SETTINGS);
    const { container } = render(<SettingsPanel />);

    await screen.findByText("Rythme des coupes");
    const toggle = container.querySelector<HTMLInputElement>("#montage");
    expect(toggle).not.toBeNull();
    expect(toggle?.checked).toBe(true);
  });

  test("changer le select envoie un PATCH qui porte cut_style", async () => {
    const calls = installFetchMock(EXISTING_SETTINGS);
    const { container } = render(<SettingsPanel />);

    await screen.findByText("Rythme des coupes");
    const select = container.querySelector<HTMLSelectElement>("#cut_style");
    if (!select) throw new Error("select cut_style introuvable");
    fireEvent.change(select, { target: { value: "nerveux" } });

    fireEvent.click(screen.getByRole("button", { name: "Enregistrer" }));

    await waitFor(() => {
      const patch = calls.find(
        (c) => c.method === "PATCH" && c.url.endsWith("/api/settings"),
      );
      if (!patch) throw new Error("aucun PATCH /api/settings observe");
      const values = (patch.body as { values: Record<string, string> }).values;
      expect(values.cut_style).toBe("nerveux");
    });
  });

  /* Revue finale V6 (lot C, C9) : GET en echec, `settings` valait {} et
     chaque champ prenait son defaut : Montage coche et Standard, sans rien
     avoir lu. Aucun defaut ne doit se presenter comme une valeur lue.
     Fix round 1 (R7) : deux mutations survivaient a ce test. La premiere
     grille sans sa garde (Poids, Scoring IA, Modele Whisper, Dossier de
     sortie affichaient encore leurs defauts, le test ne regardait que
     #montage et #cut_style), et le message de non-lecture retire (rien ne
     l'assertait, le panneau pouvait rester vide sans rien expliquer). Les
     deux sont tenues ici. */
  test("GET en echec : aucun champ, aucun defaut presente comme valeur lue", async () => {
    installFetchMock(null);
    const { container } = render(<SettingsPanel />);

    await screen.findByText(GET_ERROR);
    expect(screen.getByText("Réglages non lus.")).toBeDefined();
    for (const id of [
      "#montage",
      "#cut_style",
      "#weight_audio",
      "#ai_enabled",
      "#whisper_model",
      "#output_dir",
      "#channel_handle",
      "#captions_emojis",
      "#auto_clip_count",
      "#auto_profile",
    ]) {
      expect(container.querySelector(id)).toBeNull();
    }
    const bouton = screen.getByRole("button", { name: "Enregistrer" });
    expect((bouton as HTMLButtonElement).disabled).toBe(true);
  });

  /* Revue finale V6 (fix round 2, K5) : fermer le bandeau d'erreur remettait
     le panneau en « Chargement… » pour toujours, alors que le GET avait
     echoue et ne reviendrait pas. */
  test("GET en echec puis bandeau ferme : le panneau ne se remet pas a charger", async () => {
    installFetchMock(null);
    render(<SettingsPanel />);

    await screen.findByText(GET_ERROR);
    fireEvent.click(screen.getByRole("button", { name: "Fermer" }));

    await waitFor(() => expect(screen.queryByText(GET_ERROR)).toBeNull());
    expect(screen.queryByText("Chargement…")).toBeNull();
    expect(screen.getByText("Réglages non lus.")).toBeDefined();
  });

  test("profil des clips automatiques : l'unique profil, puis chaque profil", async () => {
    installFetchMock(EXISTING_SETTINGS);
    const { container } = render(<SettingsPanel />);

    await screen.findByText("Profil des clips automatiques");
    const select = container.querySelector<HTMLSelectElement>("#auto_profile");
    if (!select) throw new Error("select auto_profile introuvable");
    expect(Array.from(select.options).map((o) => o.value)).toEqual([
      "",
      "Plateau GoR",
      "WOLDENM OBS 1440p",
    ]);
    expect(select.value).toBe("");
  });

  test("un profil choisi puis supprimé reste affiché, marqué introuvable", async () => {
    installFetchMock({ ...EXISTING_SETTINGS, auto_profile: "Ancien plateau" });
    const { container } = render(<SettingsPanel />);

    await screen.findByText("Profil des clips automatiques");
    const select = container.querySelector<HTMLSelectElement>("#auto_profile");
    expect(select?.value).toBe("Ancien plateau");
    expect(select?.selectedOptions[0]?.textContent).toBe(
      "Ancien plateau (introuvable)",
    );
  });

  test("choisir un profil envoie un PATCH qui porte auto_profile", async () => {
    const calls = installFetchMock(EXISTING_SETTINGS);
    const { container } = render(<SettingsPanel />);

    await screen.findByText("Profil des clips automatiques");
    const select = container.querySelector<HTMLSelectElement>("#auto_profile");
    if (!select) throw new Error("select auto_profile introuvable");
    fireEvent.change(select, { target: { value: "Plateau GoR" } });
    fireEvent.click(screen.getByRole("button", { name: "Enregistrer" }));

    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      if (!patch) throw new Error("aucun PATCH /api/settings observe");
      const values = (patch.body as { values: Record<string, string> }).values;
      expect(values.auto_profile).toBe("Plateau GoR");
    });
  });

  /* Revue finale V6 (fix round 1, R7) : « Réglages non lus. » s'affichait
     aussi AVANT la reponse du GET. Le panneau annoncait une panne pendant
     qu'il chargeait, et disait la meme chose dans les deux cas. */
  test("GET en cours : le panneau charge, il n'annonce pas une lecture ratee", async () => {
    installFetchMock("en attente");
    const { container } = render(<SettingsPanel />);

    await screen.findByText("Chargement…");
    expect(screen.queryByText("Réglages non lus.")).toBeNull();
    expect(container.querySelector("#montage")).toBeNull();
    expect(container.querySelector("#weight_audio")).toBeNull();
    const bouton = screen.getByRole("button", { name: "Enregistrer" });
    expect((bouton as HTMLButtonElement).disabled).toBe(true);
  });
});
