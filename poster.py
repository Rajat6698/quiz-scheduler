"""
Stats Guru poster maker
-----------------------
Builds a 1080x1080 job/exam poster in the Stats Guru style:
a text-free background photo (student on the right) with the logo,
headline, vacancy count, key facts, an urgency ribbon, the date bar and
the footer drawn on top by code, so the text is always sharp and spelled
correctly.

    from poster import make_poster
    jpeg_bytes = make_poster({
        "category": "recruitment",          # recruitment, admit_card, result, answer_key, exam, syllabus, general
        "org": "HPRCA",                      # short name, big white headline line
        "exam": "JE Civil 2026",             # second headline line (yellow)
        "vacancies": "149",                  # optional
        "posts": "Junior Engineer (Civil)",  # optional, shown in the yellow strip
        "org_full": "HP Rajya Chayan Aayog", # optional, used when there are no posts
        "last_date": "2026-10-20",           # optional (YYYY-MM-DD or DD/MM/YYYY)
    })

Needs (in the same folder): logo.png, backgrounds/*.png, and the fonts
listed in FONT_URLS (downloaded automatically on first use).
"""

import io
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
FONT_DIR = HERE / "fonts"
BG_DIR = HERE / "backgrounds"
LOGO = HERE / "logo.png"
IST = ZoneInfo("Asia/Kolkata")

TELEGRAM = "hpgk_statsguru"
SOCIAL = "@stats_guru"

FONT_URLS = {
    "Poppins-Black.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Black.ttf",
    "Poppins-ExtraBold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-ExtraBold.ttf",
    "Poppins-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Bold.ttf",
    "Poppins-SemiBold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-SemiBold.ttf",
    "NotoSansDevanagari.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf",
    "Kalam-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/kalam/Kalam-Bold.ttf",
    "MaterialIcons.ttf": "https://raw.githubusercontent.com/google/material-design-icons/master/font/MaterialIcons-Regular.ttf",
}

# colours
NAVY = (14, 36, 86)
NAVY_LIGHT = (26, 64, 140)
NAVY_DARK = (9, 25, 62)
RED = (216, 38, 30)
RED_DARK = (160, 18, 14)
YELLOW = (255, 212, 0)
NOTE_YELLOW = (255, 230, 90)
WHITE = (255, 255, 255)
INK = (16, 24, 40)

ICONS = {
    "groups": "\uf233", "computer": "\ue30a", "location": "\ue0c8", "bank": "\ue84f", "event": "\ue878",
    "campaign": "\uef49", "doc": "\ue873", "check": "\uf0c5", "school": "\ue80c", "police": "\uef56",
    "trophy": "\uea23", "timer": "\ue425", "badge": "\uea67", "work": "\ue8f9", "send": "\ue163",
    "chevron": "\ue5cc", "download": "\uf090", "key": "\ue0da", "book": "\uea19", "calendar": "\uebcc",
    "bell": "\ue7f7", "assignment": "\ue85d", "public": "\ue80b",
}

S = 2          # draw at double size, then shrink: gives smooth edges
W = 1080

HINDI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]

# ---------------------------------------------------------------- content --

CATEGORY_TEXT = {
    # tagline (handwritten, top), sticky note, ribbon line 1 (white, yellow), ribbon line 2 (Hindi), button
    "recruitment": ("सरकारी नौकरी\nका सपना\nअब और करीब!", "Govt. Job\nKa Golden\nOpportunity", ("NOTIFICATION", "OUT!"), "भर्ती अधिसूचना जारी!", "APPLY NOW"),
    "admit_card": ("परीक्षा की\nतैयारी करें\nतेज़!", "Download\nAdmit Card\nToday!", ("ADMIT CARD", "OUT!"), "प्रवेश पत्र जारी!", "DOWNLOAD"),
    "result": ("मेहनत का\nफल आ गया!", "Check\nYour Result\nNow!", ("RESULT", "DECLARED!"), "परिणाम घोषित!", "CHECK NOW"),
    "answer_key": ("अपने उत्तर\nअभी मिलाएँ!", "Match\nYour\nAnswers!", ("ANSWER KEY", "OUT!"), "उत्तर कुंजी जारी!", "CHECK NOW"),
    "exam": ("तैयारी का\nसही समय!", "Plan Your\nPreparation\nNow!", ("EXAM", "UPDATE!"), "परीक्षा से जुड़ी नई सूचना!", "READ NOW"),
    "syllabus": ("सही दिशा में\nकरें तैयारी!", "Know Your\nSyllabus\nFirst!", ("SYLLABUS", "OUT!"), "पाठ्यक्रम जारी!", "READ NOW"),
    "general": ("नई अपडेट\nआपके लिए!", "Stay\nUpdated\nwith Us!", ("NEW", "UPDATE!"), "महत्वपूर्ण सूचना जारी!", "READ NOW"),
}


def default_chips(d):
    cat = d.get("category", "general")
    org = d.get("org", "")
    vac = d.get("vacancies")
    if cat == "recruitment":
        return [
            ("groups", (f"{vac}+" if vac else "Multiple"), "Total Posts" if vac else "Posts"),
            ("computer", "Online", "Application"),
            ("location", *(d.get("location") or "Himachal Pradesh").split(" ", 1)) if " " in (d.get("location") or "Himachal Pradesh")
            else ("location", d.get("location"), "Location"),
            ("bank", org or "Govt.", "Recruitment"),
        ]
    if cat == "admit_card":
        return [("download", "Download", "Admit Card"), ("badge", "Carry", "Photo ID"),
                ("event", "Check", "Exam Date"), ("location", "Check Exam", "Centre")]
    if cat == "result":
        return [("trophy", "Result", "Declared"), ("check", "Check", "Roll No."),
                ("doc", "Merit", "List"), ("event", "Next", "Stage")]
    if cat == "answer_key":
        return [("key", "Provisional", "Answer Key"), ("check", "Match Your", "Answers"),
                ("doc", "Raise", "Objections"), ("timer", "Check", "Last Date")]
    if cat == "syllabus":
        return [("book", "Complete", "Syllabus"), ("assignment", "Exam", "Pattern"),
                ("check", "Topic-wise", "Plan"), ("school", "Start", "Preparing")]
    return [("bell", "Latest", "Update"), ("doc", "Official", "Notice"),
            ("check", "Read", "Carefully"), ("public", org or "Official", "Website")]


def pick_background(d):
    """Chooses the background photo from the organisation / exam words."""
    text = " ".join(str(d.get(k, "")) for k in ("org", "exam", "posts", "org_full")).lower()
    rules = [
        (r"\b(police|constable|home guard|jail|warder|fire\s*man)", "police"),
        (r"\b(bank|banking|ibps|sbi|rrb|cooperative|nabard|lic)\b", "banking"),
        (r"\b(teachers?|tet|jbt|tgt|pgt|lecturer|school|shastri|c&v|drawing master|education)\b", "teaching"),
    ]
    for pattern, name in rules:
        if re.search(pattern, text):
            break
    else:
        name = "library" if d.get("category") in ("result", "admit_card", "answer_key", "syllabus", "general") else "himachal"
    if d.get("background"):
        name = d["background"]
    for ext in (".png", ".jpg", ".jpeg"):
        p = BG_DIR / (name + ext)
        if p.exists():
            return p
    found = sorted(BG_DIR.glob("*.*"))
    return found[0] if found else None


# ------------------------------------------------------------------ fonts --


def ensure_fonts():
    FONT_DIR.mkdir(exist_ok=True)
    for name, url in FONT_URLS.items():
        path = FONT_DIR / name
        if not path.exists():
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)


DEVANAGARI = re.compile(r"[\u0900-\u097F]")


@lru_cache(maxsize=256)
def _font(name, px, weight=None):
    f = ImageFont.truetype(str(FONT_DIR / name), px)
    if weight:
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
    return f


def font(kind, size, text=""):
    """kind: black, xbold, bold, semi, hand, icon. Hindi text switches to Noto automatically."""
    px = int(size * S)
    if kind == "icon":
        return _font("MaterialIcons.ttf", px)
    if kind == "hand":
        return _font("Kalam-Bold.ttf", px)
    if DEVANAGARI.search(text or ""):
        weight = {"black": "Black", "xbold": "ExtraBold", "bold": "Bold", "semi": "SemiBold"}[kind]
        return _font("NotoSansDevanagari.ttf", px, weight)
    return _font({"black": "Poppins-Black.ttf", "xbold": "Poppins-ExtraBold.ttf",
                  "bold": "Poppins-Bold.ttf", "semi": "Poppins-SemiBold.ttf"}[kind], px)


def p(v):
    return int(round(v * S))


def box(b):
    return tuple(p(v) for v in b)


# ---------------------------------------------------------------- drawing --


class Poster:
    def __init__(self, background):
        size = W * S
        if background:
            bg = Image.open(background).convert("RGB")
            scale = size / min(bg.size)
            bg = bg.resize((round(bg.width * scale), round(bg.height * scale)), Image.LANCZOS)
            left, top = (bg.width - size) // 2, (bg.height - size) // 2
            bg = bg.crop((left, top, left + size, top + size))
        else:
            bg = Image.new("RGB", (size, size), (238, 242, 248))
        self.img = bg.convert("RGBA")
        # soft white haze on the left keeps text areas calm
        haze = Image.new("L", (size, size), 0)
        hd = ImageDraw.Draw(haze)
        for x in range(0, size, 4):
            a = int(70 * max(0, 1 - x / (size * 0.7)))
            hd.rectangle((x, 0, x + 4, size), fill=a)
        self.img.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (size, size), 255)] * 3, haze)))
        self.d = ImageDraw.Draw(self.img)

    # shapes -----------------------------------------------------------
    def shadow(self, shape_box, radius, blur=14, offset=(0, 8), alpha=110, polygon=None):
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        if polygon:
            ld.polygon([(p(x + offset[0]), p(y + offset[1])) for x, y in polygon], fill=(0, 0, 0, alpha))
        else:
            b = shape_box
            ld.rounded_rectangle(box((b[0] + offset[0], b[1] + offset[1], b[2] + offset[0], b[3] + offset[1])),
                                 radius=p(radius), fill=(0, 0, 0, alpha))
        self.img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(p(blur))))
        self.d = ImageDraw.Draw(self.img)

    def rrect(self, b, r, fill, outline=None, width=0, shadow=True, gradient=None):
        if shadow:
            self.shadow(b, r)
        if gradient:
            top, bottom = gradient
            w, h = p(b[2] - b[0]), p(b[3] - b[1])
            grad = Image.new("RGBA", (w, h))
            gd = ImageDraw.Draw(grad)
            for y in range(h):
                t = y / max(1, h - 1)
                gd.line((0, y, w, y), fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=p(r), fill=255)
            self.img.paste(grad, (p(b[0]), p(b[1])), mask)
            self.d = ImageDraw.Draw(self.img)
            if outline:
                self.d.rounded_rectangle(box(b), radius=p(r), outline=outline, width=p(width))
        else:
            self.d.rounded_rectangle(box(b), radius=p(r), fill=fill, outline=outline, width=p(width) if width else 0)

    def text(self, xy, s, f, fill, anchor="la", stroke=0, stroke_fill=None):
        self.d.text((p(xy[0]), p(xy[1])), s, font=f, fill=fill, anchor=anchor,
                    stroke_width=p(stroke) if stroke else 0, stroke_fill=stroke_fill)

    def width(self, s, f):
        return self.d.textlength(s, font=f) / S

    def fit(self, s, kind, max_size, max_width, min_size=18):
        size = max_size
        while size > min_size and self.width(s, font(kind, size, s)) > max_width:
            size -= 1
        return font(kind, size, s), size

    def icon_circle(self, cx, cy, r, colour, name, icon_colour=WHITE, icon_size=None):
        self.d.ellipse(box((cx - r, cy - r, cx + r, cy + r)), fill=colour)
        self.text((cx, cy), ICONS[name], font("icon", icon_size or r * 1.15), icon_colour, anchor="mm")

    def rotated_layer(self, size, draw_fn, angle, pos):
        layer = Image.new("RGBA", (p(size[0]), p(size[1])), (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(layer), layer)
        layer = layer.rotate(angle, resample=Image.BICUBIC, expand=True)
        self.img.alpha_composite(layer, (p(pos[0]), p(pos[1])))
        self.d = ImageDraw.Draw(self.img)

    def done(self):
        out = self.img.convert("RGB").resize((W, W), Image.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, "JPEG", quality=93, optimize=True)
        return buf.getvalue()


# ------------------------------------------------------------------ parts --


def parse_date(v):
    if not v:
        return None
    if isinstance(v, date):
        return v
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            pass
    return None


def hindi_date(dt):
    return f"{dt.day} {HINDI_MONTHS[dt.month - 1]} {dt.year}"


def draw_logo(ps):
    if not LOGO.exists():
        return
    size = 196
    ps.d.ellipse(box((18, 14, 18 + size + 8, 14 + size + 8)), fill=WHITE)
    logo = Image.open(LOGO).convert("RGBA").resize((p(size), p(size)), Image.LANCZOS)
    ps.shadow((22, 18, 22 + size, 18 + size), size / 2, blur=10, offset=(0, 6), alpha=90)
    ps.d.ellipse(box((17, 13, 27 + size, 23 + size)), fill=WHITE)
    ps.img.alpha_composite(logo, (p(22), p(18)))
    ps.d = ImageDraw.Draw(ps.img)


def draw_tagline(ps, text):
    lines = text.split("\n")
    def draw(ld, layer):
        f = font("hand", 46)
        y = 10
        for line in lines:
            ld.text((p(190), p(y)), line, font=f, fill=(24, 40, 92), anchor="ma")
            y += 54
        last_w = ld.textlength(lines[-1], font=f) / S
        ld.arc(box((190 - last_w / 2, y + 2, 190 + last_w / 2 + 10, y + 30)), 200, 340, fill=RED, width=p(5))
    ps.rotated_layer((380, 84 + 54 * len(lines)), draw, 6, (262, 14))


def draw_note(ps, text):
    lines = text.split("\n")
    def draw(ld, layer):
        ld.rectangle(box((10, 10, 206, 196)), fill=NOTE_YELLOW)
        ld.polygon([(p(206), p(166)), (p(206), p(196)), (p(176), p(196))], fill=(232, 200, 40))
        f = font("hand", 34)
        y = 34
        for i, line in enumerate(lines):
            ld.text((p(108), p(y)), line, font=f, fill=RED if i == len(lines) - 1 else (24, 40, 92), anchor="ma")
            y += 42
        ld.ellipse(box((96, 0, 120, 24)), fill=RED)
        ld.ellipse(box((102, 5, 110, 13)), fill=(255, 150, 150))
    # soft shadow behind the note
    ps.shadow((872, 46, 1062, 236), 4, blur=10, offset=(4, 8), alpha=80)
    ps.rotated_layer((216, 206), draw, -7, (852, 30))


def draw_headline(ps, d, top, bottom):
    org, exam = d.get("org", "").strip(), d.get("exam", "").strip()
    lines = [l for l in (org, exam) if l]
    if not lines:
        lines = ["NEW UPDATE"]
    ps.rrect((28, top, 700, bottom), 28, NAVY, outline=WHITE, width=5, gradient=(NAVY_LIGHT, NAVY_DARK))
    max_w = 620
    if len(lines) == 1:
        f1, s1 = ps.fit(lines[0], "black", 118, max_w, 42)
        ps.text((364, (top + bottom) / 2 - (18 if bottom - top > 200 else 0)), lines[0], f1, WHITE, anchor="mm")
        return
    f1, s1 = ps.fit(lines[0], "black", 120, max_w, 46)
    f2, s2 = ps.fit(lines[1], "black", 88, max_w, 36)
    gap = 8
    block = s1 * 0.95 + gap + s2 * 0.95
    y1 = top + (bottom - top - block) / 2 + s1 * 0.47 - (14 if d.get("vacancies") else 0)
    ps.text((364, y1), lines[0], f1, WHITE, anchor="mm")
    ps.text((364, y1 + s1 * 0.48 + gap + s2 * 0.5), lines[1], f2, YELLOW, anchor="mm")


def draw_vacancies(ps, d, y):
    num = str(d["vacancies"]).strip()
    num = num if num.endswith("+") else num + "+"
    label = d.get("vacancy_label") or "पदों पर भर्ती"
    fnum = font("black", 76, num)
    nw = ps.width(num, fnum)
    x0 = 50
    ps.rrect((x0, y, x0 + nw + 44, y + 86), 18, WHITE, outline=YELLOW, width=3)
    ps.text((x0 + 22 + nw / 2, y + 45), num, fnum, RED, anchor="mm")
    flab, _ = ps.fit(label, "black", 52, 690 - (x0 + nw + 60) - 36, 28)
    lw = ps.width(label, flab)
    lx = x0 + nw + 56
    ps.rrect((lx, y + 4, lx + lw + 40, y + 82), 16, RED, outline=WHITE, width=3)
    ps.text((lx + 20 + lw / 2, y + 42), label, flab, WHITE, anchor="mm")


def draw_strip(ps, text, y):
    f, _ = ps.fit(text, "bold", 32, 600, 20)
    ps.rrect((44, y, 690, y + 56), 28, YELLOW)
    ps.text((367, y + 29), text, f, NAVY_DARK, anchor="mm")


def draw_chips(ps, chips, y):
    ps.rrect((28, y, 690, y + 138), 22, (255, 255, 255, 238))
    colours = [(226, 44, 74), (32, 90, 200), (26, 128, 70), (104, 44, 170)]
    col_w = 662 / 4
    for i, (icon, l1, l2) in enumerate(chips[:4]):
        cx = 28 + col_w * i + col_w / 2
        if i:
            ps.d.line(box((28 + col_w * i, y + 20, 28 + col_w * i, y + 118)), fill=(214, 220, 230), width=p(2))
        ps.icon_circle(cx, y + 40, 30, colours[i], icon, icon_size=36)
        f1, _ = ps.fit(l1, "bold", 23, col_w - 14, 15)
        f2, _ = ps.fit(l2, "bold", 21, col_w - 14, 14)
        ps.text((cx, y + 84), l1, f1, RED_DARK if i == 0 else INK, anchor="mm")
        ps.text((cx, y + 111), l2, f2, INK, anchor="mm")


def draw_ribbon(ps, line1, line2, y):
    poly = [(0, y), (730, y), (690, y + 116), (0, y + 116)]
    ps.shadow(None, 0, polygon=poly, blur=12, alpha=120)
    layer = Image.new("RGBA", (p(740), p(120)), (0, 0, 0, 0))
    gd = ImageDraw.Draw(layer)
    for yy in range(p(120)):
        t = yy / p(120)
        gd.line((0, yy, p(740), yy), fill=(int(RED[0] - 30 * t), int(RED[1] - 20 * t), int(RED[2] - 14 * t), 255))
    mask = Image.new("L", layer.size, 0)
    ImageDraw.Draw(mask).polygon([(0, 0), (p(730), 0), (p(690), p(116)), (0, p(116))], fill=255)
    ps.img.paste(layer, (0, p(y)), mask)
    ps.d = ImageDraw.Draw(ps.img)
    ps.d.line(box((0, y + 4, 728, y + 4)), fill=(255, 120, 110), width=p(2))
    ps.icon_circle(72, y + 58, 46, WHITE, "campaign", icon_colour=RED, icon_size=56)
    white, yellow = line1
    f1, s1 = ps.fit(white + " " + yellow, "black", 58, 560, 34)
    ww = ps.width(white + " ", f1)
    x = 140
    ps.text((x, y + 42), white + " ", f1, WHITE, anchor="lm", stroke=1, stroke_fill=RED_DARK)
    ps.text((x + ww, y + 42), yellow, f1, YELLOW, anchor="lm", stroke=1, stroke_fill=RED_DARK)
    f2, _ = ps.fit(line2, "black", 40, 540, 24)
    ps.text((x, y + 92), line2, f2, WHITE, anchor="lm")


def draw_date_bar(ps, label, value, button, y):
    ps.rrect((30, y, 820, y + 80), 20, NAVY, outline=(255, 255, 255, 180), width=2, gradient=(NAVY_LIGHT, NAVY_DARK))
    ps.rrect((48, y + 12, 104, y + 68), 12, WHITE, shadow=False)
    ps.text((76, y + 40), ICONS["calendar"], font("icon", 42), NAVY, anchor="mm")
    fl = font("semi", 24, label)
    ps.text((124, y + 22), label, fl, WHITE, anchor="lm")
    fv, _ = ps.fit(value, "black", 38, 420, 24)
    ps.text((124, y + 56), value, fv, YELLOW, anchor="lm")
    fb, _ = ps.fit(button, "black", 34, 170, 22)
    bw = ps.width(button, fb)
    bx1 = 806
    bx0 = bx1 - bw - 90
    ps.rrect((bx0, y + 10, bx1, y + 70), 30, RED, outline=WHITE, width=3, gradient=((236, 60, 48), RED_DARK))
    ps.text((bx0 + 24, y + 41), button, fb, WHITE, anchor="lm")
    cx = bx1 - 34
    ps.d.ellipse(box((cx - 17, y + 23, cx + 17, y + 57)), fill=WHITE)
    ps.text((cx, y + 40), ICONS["chevron"], font("icon", 32), RED, anchor="mm")


def draw_footer(ps, y=992):
    ps.d.rectangle(box((0, y, W, W)), fill=NAVY_DARK)
    ps.d.rectangle(box((0, y, W, y + 4)), fill=RED)
    cy = y + (W - y) / 2 + 2
    # Telegram
    ps.d.ellipse(box((24, cy - 26, 76, cy + 26)), fill=(38, 165, 228))
    ps.text((52, cy + 1), ICONS["send"], font("icon", 28), WHITE, anchor="mm")
    f = font("semi", 21)
    msg = "Updates, Study Material & PDFs:"
    ps.text((90, cy), msg, f, WHITE, anchor="lm")
    x = 90 + ps.width(msg, f) + 12
    fp = font("xbold", 23)
    tw = ps.width(TELEGRAM, fp)
    ps.rrect((x, cy - 22, x + tw + 32, cy + 22), 22, YELLOW, shadow=False)
    ps.text((x + 16 + tw / 2, cy + 1), TELEGRAM, fp, NAVY_DARK, anchor="mm")
    x += tw + 52
    ps.d.line(box((x, cy - 24, x, cy + 24)), fill=(90, 110, 150), width=p(2))
    x += 20
    # Instagram
    ig = Image.new("RGBA", (p(44), p(44)))
    igd = ImageDraw.Draw(ig)
    for i in range(p(44)):
        t = i / p(44)
        igd.line((0, i, p(44), i), fill=(int(131 + 122 * t), int(58 + 40 * t), int(180 - 130 * t), 255))
    m = Image.new("L", ig.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, p(44) - 1, p(44) - 1), radius=p(12), fill=255)
    ps.img.paste(ig, (p(x), p(cy - 22)), m)
    ps.d = ImageDraw.Draw(ps.img)
    ps.d.rounded_rectangle(box((x + 9, cy - 13, x + 35, cy + 13)), radius=p(8), outline=WHITE, width=p(3))
    ps.d.ellipse(box((x + 15, cy - 7, x + 29, cy + 7)), outline=WHITE, width=p(3))
    ps.d.ellipse(box((x + 29, cy - 11, x + 33, cy - 7)), fill=WHITE)
    x += 54
    # YouTube
    ps.d.rounded_rectangle(box((x, cy - 16, x + 46, cy + 16)), radius=p(9), fill=(255, 0, 0))
    ps.d.polygon([(p(x + 18), p(cy - 9)), (p(x + 18), p(cy + 9)), (p(x + 32), p(cy))], fill=WHITE)
    x += 56
    ps.text((x, cy), SOCIAL, font("bold", 23), WHITE, anchor="lm")


# ------------------------------------------------------------------- main --


def make_poster(d):
    """d: dict as described at the top of this file. Returns JPEG bytes."""
    ensure_fonts()
    cat = d.get("category", "general")
    if cat not in CATEGORY_TEXT:
        cat = "general"
    tagline, note, ribbon1, ribbon2, button = CATEGORY_TEXT[cat]
    today = datetime.now(IST).date()
    last = parse_date(d.get("last_date"))

    if cat == "recruitment" and last:
        days = (last - today).days
        if days == 0:
            ribbon1, ribbon2 = ("LAST DAY", "TODAY!"), "आज आवेदन की अंतिम तिथि!"
        elif 0 < days <= 5:
            ribbon1 = ("ONLY", f"{days} DAY{'S' if days > 1 else ''} LEFT!")
            ribbon2 = f"सिर्फ {days} दिन बाकी!"
        elif days > 5:
            ribbon2 = "ऑनलाइन आवेदन शुरू!"

    ps = Poster(pick_background({**d, "category": cat}))
    draw_logo(ps)
    draw_tagline(ps, d.get("tagline") or tagline)
    draw_note(ps, d.get("note") or note)

    has_vac = bool(d.get("vacancies"))
    draw_headline(ps, d, 240, 488 if has_vac else 530)
    if has_vac:
        draw_vacancies(ps, d, 450)
    strip = d.get("posts") or d.get("org_full")
    if strip:
        draw_strip(ps, strip, 552)
    draw_chips(ps, d.get("chips") or default_chips({**d, "category": cat}), 622)
    draw_ribbon(ps, ribbon1, ribbon2, 774)
    if last:
        label = "आवेदन की अंतिम तिथि" if cat == "recruitment" else "अंतिम तिथि"
        draw_date_bar(ps, label, hindi_date(last), d.get("button") or button, 900)
    else:
        draw_date_bar(ps, "जारी होने की तिथि", hindi_date(parse_date(d.get("date")) or today),
                      d.get("button") or button, 900)
    draw_footer(ps)
    return ps.done()
