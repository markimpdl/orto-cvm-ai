import type { NextConfig } from "next";

// Old addresses from before the Fábrica | Em alta | Canais | Avatares | Configurações menu (2026-11-24).
// Not permanent, so the browser never caches them; query strings (?trend=, ?avatar=…) pass through.
const MOVED: [string, string][] = [
  ["/", "/fabrica"],
  ["/fila", "/fabrica"],
  ["/projetos", "/fabrica"],
  ["/novo", "/fabrica/novo"],
  ["/sua-vez", "/fabrica/sua-vez"],
  ["/publicar", "/fabrica/publicar"],
  ["/calendario", "/fabrica/publicar"],
  ["/infantil", "/fabrica/series"],
  ["/crescer", "/fabrica/cortes"],
  ["/cluster", "/fabrica"],
  ["/canais/avatares", "/avatares"],
  ["/estudio-voz", "/avatares/estudio-voz"],
];

const nextConfig: NextConfig = {
  async redirects() {
    return MOVED.map(([source, destination]) => ({ source, destination, permanent: false }));
  },
};

export default nextConfig;
