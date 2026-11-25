"""OBS scene collection for a channel's gameplay recordings.

    uv run python ../scripts/obs_template.py <channel_id> "<channel name>"

Draws the frames from the channel's brand art (data/channels/<id>/brand) into
data/channels/<id>/obs/ and writes a scene collection into OBS's own folder
(a new file; existing collections are left alone). Canvas 1920x1080.
"""
import json
import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OBS = Path.home() / "Library/Application Support/obs-studio/basic/scenes"
FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
W, H = 1920, 1080
GOLD, NAVY, WHITE = (255, 198, 43), (6, 12, 34), (255, 255, 255)

# x, y, w, h: the game fills the screen; over it on the right, at the bottom, the webcam and
# under it, same width, a strip cut from the channel cover
CAM = (1536, 678, 360, 270)      # 4:3, the webcam is cropped to fill
CARD = (1536, 960, 360, 96)      # the cover's skyline + channel picture and name


def cover(img, w, h):
    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def font(size):
    return ImageFont.truetype(FONT, size)


def card(banner, logo, name, w, h):
    """The skyline on the right of the channel cover, with the channel picture and name over its sky."""
    wide = w / h > 2.2  # a strip: picture and name side by side
    bw, bh = banner.size
    top, ch = (round(bh * .285), round(bh * .385)) if wide else (round(bh * .2), round(bh * .45))
    cw = round(ch * w / h)
    own = min(cw, round(bw * .31))  # further left the cover has its own lettering
    sky = banner.crop((bw - own, top, bw, top + ch))
    out = Image.new("RGB", (cw, ch))
    out.paste(sky, (cw - own, 0))
    clear = sky.crop((0, 0, own // 2, ch))  # the part without towers goes on, mirrored back and forth
    x, flip = cw - own, True
    while x > 0:
        x -= clear.width
        out.paste(clear.transpose(Image.FLIP_LEFT_RIGHT) if flip else clear, (x, 0))
        flip = not flip
    out = out.resize((w, h), Image.LANCZOS).convert("RGBA")

    d = round(h * (.8 if wide else .56))
    s = logo.width
    face = logo.crop((round(s * .16), round(s * .07), round(s * .84), round(s * .75)))
    face = face.resize((d * 4, d * 4), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", face.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, face.width - 1, face.height - 1), fill=255)
    ring = Image.new("RGBA", face.size, (0, 0, 0, 0))
    ring.paste(face, (0, 0), mask)
    ImageDraw.Draw(ring).ellipse((0, 0, face.width - 1, face.height - 1), outline=GOLD + (255,), width=14)
    ring = ring.resize((d, d), Image.LANCZOS)

    left = round(w * .58)  # the sky; the towers keep the rest
    if not wide:
        out.alpha_composite(ring, ((left - d) // 2, round(h * .07)))
        return out
    # the cover's own lettering, its dark glow faded into the strip's sky
    out.alpha_composite(ring, (round(h * .1), (h - d) // 2))
    x0 = round(h * .1) + d + round(h * .04)
    mark = banner.crop((round(bw * .355), round(bh * .375), round(bw * .685), round(bh * .553)))  # down to the stripes, without the tagline
    mw = round(w * .775) - x0
    mark = mark.resize((mw, round(mark.height * mw / mark.width)), Image.LANCZOS).convert("RGBA")
    fade = Image.new("L", mark.size, 0)
    edge = round(mark.height * .16)
    ImageDraw.Draw(fade).rectangle((edge, edge, mark.width - edge, mark.height - edge // 3), fill=255)
    mark.putalpha(fade.filter(ImageFilter.GaussianBlur(edge * .4)))
    out.alpha_composite(mark, (x0, (h - mark.height) // 2))
    return out


def slide(banner, text):
    img = cover(banner, W, H).convert("RGBA")
    fade = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(fade)
    for i in range(420):
        d.line((0, H - 420 + i, W, H - 420 + i), fill=NAVY + (round(235 * (i / 420) ** 1.3),))
    img.alpha_composite(fade)
    d = ImageDraw.Draw(img)
    f = font(104)
    box = d.textbbox((0, 0), text, font=f)
    x, y = (W - (box[2] - box[0])) // 2 - box[0], 880 - box[1]
    d.text((x, y), text, font=f, fill=WHITE + (255,), stroke_width=6, stroke_fill=NAVY + (255,))
    d.rectangle((W // 2 - 150, 850, W // 2 + 150, 856), fill=GOLD + (255,))
    return img


def draw(channel_id, name):
    brand = ROOT / "data/channels" / str(channel_id) / "brand"
    out = ROOT / "data/channels" / str(channel_id) / "obs"
    out.mkdir(parents=True, exist_ok=True)
    banner, logo = Image.open(brand / "banner.png").convert("RGB"), Image.open(brand / "logo.png").convert("RGB")

    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for x, y, w, h in (CAM, CARD):
        ImageDraw.Draw(shadow).rectangle((x - 6, y, x + w + 6, y + h + 12), fill=(0, 0, 0, 170))
    over.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(over)
    for x, y, w, h in (CAM, CARD):
        d.rectangle((x - 3, y - 3, x + w + 2, y + h + 2), fill=GOLD + (255,))
    x, y, w, h = CAM
    d.rectangle((x, y, x + w - 1, y + h - 1), fill=(0, 0, 0, 0))  # the camera shows through
    over.paste(card(banner, logo, name, *CARD[2:]), CARD[:2])
    over.save(out / "moldura_camera.png")
    (out / "moldura.png").unlink(missing_ok=True)

    for key, text in (("abertura", "JÁ VAI COMEÇAR"), ("pausa", "VOLTO JÁ"), ("fim", "VALEU! ATÉ A PRÓXIMA")):
        slide(banner, text).convert("RGB").save(out / f"{key}.png")
    return out


# --- OBS scene collection -----------------------------------------------------------------------

def source(name, kind, settings, hotkeys=None):
    audio = {"libobs.mute": [], "libobs.unmute": [], "libobs.monitor-on": [], "libobs.monitor-off": [],
             "libobs.push-to-mute": [], "libobs.push-to-talk": []}
    return {"prev_ver": 553648129, "name": name, "uuid": str(uuid.uuid4()), "id": kind, "versioned_id": kind,
            "settings": settings, "mixers": 0 if kind == "scene" else 255, "sync": 0, "flags": 0, "volume": 1.0,
            "balance": 0.5, "enabled": True, "muted": False, "push-to-mute": False, "push-to-mute-delay": 0,
            "push-to-talk": False, "push-to-talk-delay": 0, "hotkeys": audio if hotkeys is None else hotkeys,
            "deinterlace_mode": 0, "deinterlace_field_order": 0, "monitoring_type": 0,
            "monitoring_enabled": False, "private_settings": {}}


def item(src, n, rect=None, fill=False):
    """A scene item; with rect the source is fitted into it (fill = cropped to cover it)."""
    x, y, w, h = rect or (0, 0, 0, 0)
    return {"name": src["name"], "source_uuid": src["uuid"], "visible": True, "locked": True, "rot": 0.0,
            "scale_ref": {"x": float(W), "y": float(H)}, "align": 5,
            "bounds_type": (3 if fill else 2) if rect else 0, "bounds_align": 0, "bounds_crop": bool(fill),
            "crop_left": 0, "crop_top": 0, "crop_right": 0, "crop_bottom": 0, "id": n, "group_item_backup": False,
            "pos": {"x": float(x), "y": float(y)},
            "pos_rel": {"x": (2 * x - W) / H, "y": 2 * y / H - 1},
            "scale": {"x": 1.0, "y": 1.0}, "scale_rel": {"x": 1.0, "y": 1.0},
            "bounds": {"x": float(w), "y": float(h)}, "bounds_rel": {"x": 2 * w / H, "y": 2 * h / H},
            "scale_filter": "disable", "blend_method": "default", "blend_type": "normal",
            "show_transition": {"duration": 300}, "hide_transition": {"duration": 300}, "private_settings": {}}


def scene(name, items):
    hot = {"OBSBasic.SelectScene": []}
    for it in items:
        hot[f"libobs.show_scene_item.{it['id']}"] = []
        hot[f"libobs.hide_scene_item.{it['id']}"] = []
    s = source(name, "scene", {"id_counter": len(items), "custom_size": False, "items": items}, hot)
    s["canvas_uuid"] = "6c69626f-6273-4c00-9d88-c5136d61696e"
    return s


def capture_device():
    """The capture card plugged in now (its id changes with the USB port), else the one OBS already has."""
    import subprocess
    try:
        cams = json.loads(subprocess.run(["system_profiler", "SPCameraDataType", "-json"], capture_output=True,
                                         text=True, timeout=30).stdout)["SPCameraDataType"]
        for c in cams:
            if "elgato" in c["_name"].lower():
                return {"device_name": c["_name"], "device": c["spcamera_unique-id"], "buffering": False}
    except Exception:
        pass
    for f in OBS.glob("*.json"):
        for s in json.loads(f.read_text()).get("sources", []):
            if s.get("id") == "macos-avcapture" and "elgato" in s["settings"].get("device_name", "").lower():
                return s["settings"]
    return {}


def collection(name, art, camera):
    img = lambda label, f: source(label, "image_source", {"file": str(art / f)})
    over = img("Moldura da câmera e foto", "moldura_camera.png")
    game = source("Jogo (placa de captura)", "macos-avcapture", capture_device())
    cam = source("Câmera", "macos-avcapture", camera)
    slides = {"Abertura": img("Arte abertura", "abertura.png"), "Pausa": img("Arte pausa", "pausa.png"),
              "Encerramento": img("Arte encerramento", "fim.png")}
    full = (0, 0, W, H)
    scenes = [
        scene("Jogo + Câmera", [item(game, 1, full), item(cam, 2, CAM, fill=True), item(over, 3)]),
        *[scene(label, [item(s, 1, full)]) for label, s in slides.items()],
    ]
    mic = source("Mic/Aux", "coreaudio_input_capture", {"device_id": "default"})
    return {"name": name, "AuxAudioDevice1": mic,
            "sources": [over, game, cam, *slides.values(), *scenes], "groups": [],
            "scene_order": [{"name": s["name"]} for s in scenes],
            "current_scene": scenes[0]["name"], "current_program_scene": scenes[0]["name"], "canvases": [],
            "current_transition": "Esmaecer", "transition_duration": 300, "transitions": [],
            "quick_transitions": [], "saved_projectors": [], "preview_locked": False, "scaling_enabled": False,
            "scaling_level": 5, "scaling_off_x": 0.0, "scaling_off_y": 0.0, "modules": {},
            "resolution": {"x": W, "y": H}, "version": 2}


if __name__ == "__main__":
    channel_id, name = sys.argv[1], sys.argv[2]
    camera = {"device": sys.argv[3], "device_name": sys.argv[4]} if len(sys.argv) > 4 else {}
    art = draw(channel_id, name)
    target = OBS / (name.replace(" ", "_") + ".json")
    if target.exists():
        target.replace(target.with_suffix(".json.antes"))
    target.write_text(json.dumps(collection(name, art, camera), ensure_ascii=False, indent=4))
    print(art, target, sep="\n")
