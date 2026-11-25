// Links accepted on /fabrica/novo (mirror of backend/cvm/lives/platforms.py; the API has the last word).
export type LinkPlatform = "youtube" | "twitch" | "kick" | "tiktok" | "instagram";

export const LINK_SITE: Record<LinkPlatform, string> = {
  youtube: "YouTube", twitch: "Twitch", kick: "Kick", tiktok: "TikTok", instagram: "Instagram",
};

const VIDEO: [LinkPlatform, RegExp][] = [
  ["youtube", /^https?:\/\/(?:www\.|m\.|music\.)?(?:youtube\.com\/(?:watch\?(?:.*&)?v=|shorts\/|live\/|embed\/)|youtu\.be\/)[\w-]{11}/],
  ["twitch", /^https?:\/\/(?:www\.|m\.)?twitch\.tv\/(?:videos\/|\w+\/v(?:ideo)?\/)\d+/],
  ["twitch", /^https?:\/\/(?:clips\.twitch\.tv\/(?:embed\?clip=)?|(?:www\.|m\.)?twitch\.tv\/\w+\/clip\/)[\w-]+/],
  ["kick", /^https?:\/\/(?:www\.)?kick\.com\/(?:[\w-]+\/videos\/|video\/)[0-9a-f-]{36}/],
  ["kick", /^https?:\/\/(?:www\.)?kick\.com\/[\w-]+(?:\/clips\/|\?(?:.*&)?clip=)clip_\w+/],
  ["tiktok", /^https?:\/\/(?:www\.|m\.)?tiktok\.com\/@[\w.-]+\/video\/\d+/],
  ["tiktok", /^https?:\/\/(?:(?:vm|vt)\.tiktok\.com\/|(?:www\.|m\.)?tiktok\.com\/t\/)[\w-]+/],
  ["instagram", /^https?:\/\/(?:www\.|m\.)?instagram\.com\/(?:[\w.]+\/)?(?:p|reels?|tv)\/[\w-]+/],
];

// links that can never be one whole public video → the message shown (pt-BR source, translated by t())
const REFUSED: [RegExp, string][] = [
  [/^https?:\/\/(?:[\w-]+\.)?twitch\.tv(?:\/|$)/, "Esse link é de um canal da Twitch (ao vivo), não de um vídeo. Cole o link de um vídeo salvo (twitch.tv/videos/…) ou de um clipe."],
  [/^https?:\/\/(?:www\.)?kick\.com(?:\/|$)/, "Esse link é de um canal do Kick, não de um vídeo. Cole o link de um vídeo salvo (kick.com/…/videos/…) ou de um clipe."],
  [/^https?:\/\/(?:www\.|m\.)?tiktok\.com\/@[\w.-]+\/photo\//, "Esse post do TikTok é de fotos, não de vídeo."],
  [/^https?:\/\/(?:[\w-]+\.)?tiktok\.com(?:\/|$)/, "Esse link é de um perfil do TikTok. Cole o link de um vídeo (tiktok.com/@…/video/…)."],
  [/^https?:\/\/(?:www\.|m\.)?instagram\.com\/stories\//, "Stories do Instagram pedem login. Só dá para usar posts e reels públicos."],
  [/^https?:\/\/(?:www\.|m\.)?instagram\.com(?:\/|$)/, "Esse link é de um perfil do Instagram. Cole o link de um post ou reel público (instagram.com/p/… ou /reel/…)."],
];

export function linkPlatform(url: string): { platform: LinkPlatform } | { error: string } {
  const u = url.trim();
  const hit = VIDEO.find(([, rx]) => rx.test(u));
  if (hit) return { platform: hit[0] };
  const refused = REFUSED.find(([rx]) => rx.test(u));
  return { error: refused ? refused[1] : "Link não reconhecido. Aceito links do YouTube, Twitch (vídeos e clipes), Kick, TikTok ou Instagram (posts públicos)." };
}
