"""Platform uploaders (official, free APIs) and manual-publish helpers.

Credentials live in data/secrets/ (gitignored):
- YouTube: youtube_client_secret.json (OAuth "Desktop app" client from Google Cloud, free) +
  youtube_token_<channel_id>.json created on first authorization.
  Until Google audits the project, uploads are forced to private (we default to private).
- TikTok: tiktok_<channel_id>.json {"access_token": ...} from your TikTok developer app (video.upload scope).
  Unaudited apps can only send to the creator's inbox (draft), which is what we use.
- Instagram: instagram_<channel_id>.json {"ig_user_id", "access_token"} (Business/Creator + Meta app review).
  Meta downloads the video from a public URL: set CVM_PUBLIC_BASE_URL (e.g. a free cloudflared quick tunnel).
"""

import json
import re
import time
from pathlib import Path

import httpx

from cvm.config import settings
from cvm.models import Channel, Render

SECRETS = settings.data_dir / "secrets"
UPLOAD_PAGES = {
    "youtube": "https://www.youtube.com/upload",
    "youtube_shorts": "https://www.youtube.com/upload",
    "instagram": "https://www.instagram.com/",
    "tiktok": "https://www.tiktok.com/tiktokstudio/upload",
}
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class NotConfigured(RuntimeError):
    pass


def _secret(name: str) -> dict:
    p = SECRETS / name
    if not p.exists():
        raise NotConfigured(f"credencial ausente: data/secrets/{name}")
    return json.loads(p.read_text())

def youtube_tags(tags: list[str], limit: int = 500) -> list[str]:
    """Clean tags within YouTube's 500-character budget (commas between tags count, and a tag with spaces counts its
    two quotes); no '#', no commas or angle brackets inside a tag, no duplicates."""
    out, used, seen = [], 0, set()
    for t in tags:
        t = re.sub(r"[,<>]", " ", str(t)).replace("#", "")
        t = re.sub(r"\s+", " ", t).strip()[:100]
        if not t or t.lower() in seen:
            continue
        cost = len(t) + (2 if " " in t else 0) + (1 if out else 0)
        if used + cost > limit:
            break
        out.append(t)
        seen.add(t.lower())
        used += cost
    return out


# YouTube categories: id → names as YouTube Studio shows them (pt-BR / en / es UI), for the browser assist
CATEGORIES = {
    "1": ["Filmes e desenhos", "Film & Animation", "Cine y animación"],
    "2": ["Autos e veículos", "Autos & Vehicles", "Motor"],
    "10": ["Música", "Music"],
    "15": ["Animais de estimação e animais", "Pets & Animals", "Mascotas y animales"],
    "17": ["Esportes", "Sports", "Deportes"],
    "19": ["Viagens e eventos", "Travel & Events", "Viajes y eventos"],
    "20": ["Jogos", "Gaming", "Videojuegos"],
    "22": ["Pessoas e blogs", "People & Blogs", "Gente y blogs"],
    "23": ["Comédia", "Comedy", "Comedia"],
    "24": ["Entretenimento", "Entertainment", "Entretenimiento"],
    "25": ["Notícias e política", "News & Politics", "Noticias y política"],
    "26": ["Guia e estilo", "Instruções e estilo", "Howto & Style", "Consejos y estilo"],
    "27": ["Educação", "Education", "Educación"],
    "28": ["Ciência e tecnologia", "Science & Technology", "Ciencia y tecnología"],
    "29": ["Sem fins lucrativos e ativismo", "ONGs e ativismo", "Nonprofits & Activism", "ONG y activismo"],
}
_CATEGORY_WORDS = [  # first match wins; words of the title/tags in any of the 3 languages (lower case)
    ("20", r"\b(gta|game|games|jogo|jogos|gamer|gameplay|playstation|ps5|xbox|switch|nintendo|steam|wow|warcraft|zelda|"
           r"ocarina|elder scrolls|mu online|gears of war|blizzard|rockstar|guitar hero|رياضات إلكترونية|لعبة|ألعاب)\b"),
    ("28", r"\b(iphone|apple|android|samsung|macbook|chip|ia|ai|inteligência artificial|artificial intelligence|tecnologia|"
           r"technology|software|app|robô|robot|gpu|nvidia|m5|celular|smartphone|programação|coding|تقنية|ذكاء اصطناعي|آيفون)\b"),
    ("26", r"\b(artesanato|diy|faça você mesmo|cerâmica|crochê|decoração|tutorial|passo a passo|receita|enfeite|craft|"
           r"crafts|decor|how to|recipe|أشغال يدوية|ديكور)\b"),
    ("19", r"\b(viagem|passeio|turismo|travel|trip|dubai|abu dhabi|jalapão|praia|beach|سياحة|سفر)\b"),
    ("27", r"\b(ciência|science|dinossauro|dinosaur|fóssil|fossil|matemática|math)\b"),
    ("25", r"\b(notícia|news|lançamento oficial|eleição|governo|government|أخبار)\b"),
]


STYLE_CATEGORY = {"kids": "1", "lesson": "27", "review": "28"}  # styles whose category never depends on the topic
STYLE_FALLBACK = {"explainer": "27", "dark": "24", "top_list": "24", "react": "24"}


def category_for(render: Render, channel: Channel | None, style: str | None = None) -> str:
    """The YouTube category, chosen here (the SEO agent doesn't pick it). `style` = the project's (else the channel's):
    1. the channel's fixed category (prefs.youtube_category + youtube_category_fixed), or one typed on the render's SEO;
    2. kids series (project style kids, or a made-for-kids channel) → 1 Film & Animation;
    3. lesson → 27 Education, review → 28 Science & Technology;
    4. the topic, from the title and tags (_CATEGORY_WORDS);
    5. the channel's usual category (prefs.youtube_category, not fixed);
    6. the style's default (explainer 27, dark/top_list/react 24), else 22 People & Blogs."""
    seo = render.seo or {}
    prefs = (getattr(channel, "prefs", None) or {})
    own = str(prefs.get("youtube_category") or "")
    if own in CATEGORIES and prefs.get("youtube_category_fixed"):
        return own
    if str(seo.get("category_id") or "") in CATEGORIES:  # set by hand on /publicar
        return str(seo["category_id"])
    style = style or getattr(getattr(channel, "default_style", None), "value", getattr(channel, "default_style", None))
    if style == "kids" or (channel and channel.made_for_kids):
        return "1"
    if style in STYLE_CATEGORY:
        return STYLE_CATEGORY[style]
    text = " ".join([seo.get("title") or "", *(seo.get("tags") or [])]).lower()
    for cid, rx in _CATEGORY_WORDS:
        if re.search(rx, text):
            return cid
    if own in CATEGORIES:
        return own
    return STYLE_FALLBACK.get(style or "", "22")


def link_for(ref: str) -> str:
    """A cross link as written in the profile ("@Handle", a channel id or a URL) → a URL."""
    ref = ref.strip()
    if ref.startswith("http"):
        return ref
    if ref.startswith("UC") and len(ref) == 24:
        return f"https://www.youtube.com/channel/{ref}"
    return f"https://www.youtube.com/@{ref.lstrip('@')}"


def with_channel_extras(description: str | None, channel: Channel | None) -> str:
    """The channel's description_footer and cross_links (profile) appended when the SEO text does not have them."""
    desc = (description or "").rstrip()
    p = (getattr(channel, "profile", None) or {}) if channel else {}
    footer = str(p.get("description_footer") or "").strip()
    if footer and footer not in desc:
        desc = f"{desc}\n\n{footer}" if desc else footer
    links = []
    for ref in p.get("cross_links") or []:
        ref = str(ref).strip()
        handle = ref.lstrip("@").lower()
        if ref and handle not in desc.lower():
            links.append(link_for(ref))
    if links:
        desc = f"{desc}\n\n" + "\n".join(links) if desc else "\n".join(links)
    return desc


def caption_text(render: Render, platform: str, channel: Channel | None = None) -> str:
    if platform == "tiktok":  # its own caption: title + short text + 3–5 hashtags, no YouTube-only lines
        from cvm.publish.tiktok import caption

        return caption(render)
    seo = render.seo or {}
    tags = " ".join(f"#{h.lstrip('#')}" for h in seo.get("hashtags", []))
    desc = with_channel_extras(seo.get("description"), channel) if channel else seo.get("description", "")
    return f"{seo.get('title', '')}\n\n{desc}\n\n{tags}".strip()


# ---------- YouTube ----------

def youtube_credentials(channel: Channel, interactive: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token = SECRETS / f"youtube_token_{channel.id}.json"
    creds = Credentials.from_authorized_user_file(str(token), YT_SCOPES) if token.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:
            raise NotConfigured(f"autorize o canal {channel.name}: uv run python -m cvm.publish.platforms auth-youtube {channel.id}")
        client = SECRETS / "youtube_client_secret.json"
        if not client.exists():
            raise NotConfigured("baixe o OAuth client (Desktop) do Google Cloud para data/secrets/youtube_client_secret.json")
        creds = InstalledAppFlow.from_client_secrets_file(str(client), YT_SCOPES).run_local_server(port=0)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json())
    return creds


# YouTube upload boxes that stay OFF on every upload (user's rule, 2026-11-24): (name, label regex in en/pt/es).
# A channel with prefs.publish_defaults (install wizard) chooses for itself: youtube_rules() below.
# The assisted browser upload unticks them; the Data API sets notifySubscribers=False and no paid promotion
# (it has no field for automatic chapters/places, so the kit lists them for a manual upload).
# Paid promotion is a Yes/No radio pair in Studio since 2026-11 (VIDEO_PAID_PRODUCT_PLACEMENT_NOTIFY / _NO), not a box.
YOUTUBE_PAID_PROMO_NO = r"(no, my video does not include|não, meu vídeo não inclui|no, mi video no incluye)"
YOUTUBE_OFF_BOXES = [
    ("Capítulos automáticos", r"automatic chapters|cap[íi]tulos autom[áa]ticos"),
    ("Permitir a localização automática", r"automatic places|localiza[çc][ãa]o autom[áa]tica|lugares autom[áa]ticos"),
    ("Publicar no feed de inscrições e enviar notificações aos inscritos",
     r"notify subscribers|subscriptions feed|feed de inscri[çc][õo]es|notifica[çc][õo]es aos inscritos|notificar a los suscriptores"),
]

# which Channel.prefs["publish_defaults"] key turns each box above back on (install wizard, first channel)
_BOX_PREF = {"Capítulos automáticos": "auto_chapters", "Permitir a localização automática": "auto_places",
             "Publicar no feed de inscrições e enviar notificações aos inscritos": "notify_subscribers"}


def youtube_rules(channel: Channel | None) -> dict:
    """The upload choices for a channel: its prefs.publish_defaults (set by the install wizard) when present, else
    the owner's hard rules above (all boxes off, no paid promotion, synthetic box from the avatar check).
    {"off_boxes": [(name, rx)], "notify_subscribers", "paid_promotion", "synthetic": auto | yes | no}."""
    pd = ((getattr(channel, "prefs", None) or {}).get("publish_defaults") or {}) if channel else {}
    synthetic = pd.get("synthetic") if pd.get("synthetic") in ("auto", "yes", "no") else "auto"
    return {"off_boxes": [(n, rx) for n, rx in YOUTUBE_OFF_BOXES if pd.get(_BOX_PREF[n]) is not True],
            "notify_subscribers": pd.get("notify_subscribers") is True,
            "paid_promotion": pd.get("paid_promotion") is True,
            "synthetic": synthetic}


def upload_youtube(render: Render, channel: Channel, privacy: str = "private") -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    yt = build("youtube", "v3", credentials=youtube_credentials(channel), cache_discovery=False)
    seo = render.seo or {}
    title = seo.get("title") or Path(render.path).stem
    if render.profile == "youtube_shorts" and "#shorts" not in title.lower():
        title = f"{title[:90]} #shorts"
    rules = youtube_rules(channel)
    synthetic = (bool((render.meta or {}).get("synthetic_disclosure")) if rules["synthetic"] == "auto"  # avatar on screen
                 else rules["synthetic"] == "yes")
    body = {
        "snippet": {"title": title[:100], "description": caption_text(render, "youtube", channel)[:5000],
                    "tags": youtube_tags(seo.get("tags", [])), "categoryId": category_for(render, channel),
                    "defaultLanguage": render.language.split("-")[0], "defaultAudioLanguage": render.language.split("-")[0]},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(channel.made_for_kids),
                   "containsSyntheticMedia": synthetic},
        "paidProductPlacementDetails": {"hasPaidProductPlacement": rules["paid_promotion"]},
    }
    req = yt.videos().insert(part="snippet,status,paidProductPlacementDetails", body=body,
                             notifySubscribers=rules["notify_subscribers"], media_body=MediaFileUpload(render.path, chunksize=8 * 1024 * 1024, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]
    thumb = (render.meta or {}).get("thumbnail")
    if thumb and Path(thumb).exists() and render.profile == "youtube":
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumb)).execute()
    return f"https://youtube.com/watch?v={vid}" if render.profile == "youtube" else f"https://youtube.com/shorts/{vid}"


# ---------- TikTok (inbox / draft) ----------

def upload_tiktok_inbox(render: Render, channel: Channel) -> str:
    token = _secret(f"tiktok_{channel.id}.json")["access_token"]
    size = Path(render.path).stat().st_size
    with httpx.Client(timeout=120) as c:
        init = c.post("https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
                      json={"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}})
        init.raise_for_status()
        data = init.json()["data"]
        put = c.put(data["upload_url"], content=Path(render.path).read_bytes(),
                    headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"})
        put.raise_for_status()
    return f"tiktok-inbox:{data['publish_id']}"  # finish posting from the TikTok app's inbox


# ---------- Instagram Reels ----------

def upload_instagram(render: Render, channel: Channel) -> str:
    cfg = _secret(f"instagram_{channel.id}.json")
    if not settings.public_base_url:
        raise NotConfigured("defina CVM_PUBLIC_BASE_URL (ex.: túnel cloudflared) para o Instagram baixar o vídeo")
    video_url = f"{settings.public_base_url.rstrip('/')}/renders/{render.id}/file"
    base = f"https://graph.facebook.com/v21.0/{cfg['ig_user_id']}"
    with httpx.Client(timeout=60) as c:
        r = c.post(f"{base}/media", data={"media_type": "REELS", "video_url": video_url, "caption": caption_text(render, "instagram")[:2200],
                                          "share_to_feed": "true", "access_token": cfg["access_token"]})
        r.raise_for_status()
        creation = r.json()["id"]
        for _ in range(60):  # wait for Meta to process the video
            st = c.get(f"https://graph.facebook.com/v21.0/{creation}", params={"fields": "status_code", "access_token": cfg["access_token"]}).json()
            if st.get("status_code") == "FINISHED":
                break
            if st.get("status_code") == "ERROR":
                raise RuntimeError(f"Instagram processing error: {st}")
            time.sleep(10)
        pub = c.post(f"{base}/media_publish", data={"creation_id": creation, "access_token": cfg["access_token"]})
        pub.raise_for_status()
    return f"instagram:{pub.json()['id']}"


def publish_via_api(render: Render, channel: Channel) -> str:
    try:
        return _publish_via_api(render, channel)
    except ImportError as e:
        raise NotConfigured(f"dependência ausente: {e.name}") from e


def _publish_via_api(render: Render, channel: Channel) -> str:
    platform = channel.platform.value
    if platform in ("youtube", "youtube_shorts"):
        return upload_youtube(render, channel, privacy=settings.youtube_privacy)
    if platform == "tiktok":
        return upload_tiktok_inbox(render, channel)
    if platform == "instagram":
        return upload_instagram(render, channel)
    raise NotConfigured(platform)


if __name__ == "__main__":
    import sys

    from sqlmodel import Session

    from cvm.db import engine

    if len(sys.argv) == 3 and sys.argv[1] == "auth-youtube":
        with Session(engine) as s:
            youtube_credentials(s.get(Channel, int(sys.argv[2])), interactive=True)
            print("YouTube autorizado.")
