"""Vocabularies and model-family rules for Mexican marketplace listings (Spanish + English titles).

Everything in this module operates on text that already went through
:func:`flipradar.normalize.extract.normalize_text` (lowercase, no accents, units unified such as
``256gb`` / ``1tb`` / ``13in`` / ``45mm``).

Design rules
------------
* **Precision over recall.**  A false product match creates a fake arbitrage opportunity, so every
  model rule returns ``model=None`` (family recognised, exact product ambiguous) whenever the title
  does not pin down the generation / chip / variant that changes the price.
* **Deterministic.**  Pure regular expressions and lookup tables; no randomness, no I/O.
* Nothing here *invents* facts: inferences (e.g. "PS5 with 1TB is the Slim") are only made where
  the hardware line-up makes them unambiguous, and they are reported as notes by the caller.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

# ------------------------------------------------------------------------------------------------
# Categories
# ------------------------------------------------------------------------------------------------

CATEGORIES: tuple[str, ...] = (
    "phone",
    "console",
    "handheld_pc",
    "laptop",
    "tablet",
    "audio",
    "smartwatch",
    "camera",
    "drone",
    "gpu",
    "tool",
    "toy",
    "sneakers",
    "ereader",
    "other",
)

#: Categories where the storage capacity changes the price, so it must be part of the cluster key.
STORAGE_CATEGORIES: frozenset[str] = frozenset({"phone", "tablet", "console", "handheld_pc", "laptop"})

#: Models sold in a single storage configuration: a storage mention (often a bundled SD card) must
#: not split or block the cluster.
FIXED_STORAGE_MODELS: frozenset[str] = frozenset(
    {"switch", "switch lite", "switch oled", "switch 2", "ps5 pro", "playstation portal", "rog ally x"}
)

#: Console models that exist both with a disc drive and as a digital-only edition.
EDITION_MODELS: frozenset[str] = frozenset({"ps5", "ps5 slim", "xbox series x"})

# Keyword -> category (used when no model rule recognised the product).  Earliest match wins.
CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    (r"telefono\scelular|celular|smartphone|telefono|movil", "phone"),
    (r"consola\sportatil|pc\sportatil\sgamer", "handheld_pc"),
    (r"consola|videoconsola|console", "console"),
    (r"laptop|notebook|computadora\sportatil|ultrabook|chromebook", "laptop"),
    (r"tablet|tableta", "tablet"),
    (r"audifonos|auriculares|headphones|earbuds|bocina|speaker|barra\sde\ssonido", "audio"),
    (r"smartwatch|smart\swatch|reloj\sinteligente", "smartwatch"),
    (r"tarjeta\sde\svideo|tarjeta\sgrafica|tarjeta\sde\sgraficos|gpu", "gpu"),
    (r"camara|camera|mirrorless|reflex", "camera"),
    (r"drone|dron", "drone"),
    (
        r"taladro|rotomartillo|atornillador|llave\sde\simpacto|esmeriladora|sierra\scircular|pulidora|"
        r"herramienta",
        "tool",
    ),
    (r"juguete|lego", "toy"),
    (r"tenis|sneakers|zapatillas", "sneakers"),
    (r"kindle|e\s?reader|lector\sde\slibros", "ereader"),
)

# ------------------------------------------------------------------------------------------------
# Brands
# ------------------------------------------------------------------------------------------------

#: canonical brand -> alias regex fragments (matched on normalized text with word boundaries).
BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "apple": ("apple", "iphone", "ipad", "macbook", "airpods", "imac", r"mac\smini", "iwatch"),
    "samsung": ("samsung", "galaxy"),
    "xiaomi": ("xiaomi", "redmi", r"poco\s[xfmc]\s?\d"),
    "motorola": ("motorola", r"moto\s?(?:g|e)\s?\d{1,3}", r"moto\sedge", "razr"),
    "huawei": ("huawei",),
    "honor": ("honor",),
    "oppo": ("oppo",),
    "realme": ("realme",),
    "google": (r"google\spixel", r"pixel\s?\d{1,2}a?", "google"),
    "oneplus": ("oneplus",),
    "sony": ("sony", "playstation", "ps5", "ps4", "dualsense", "dualshock", r"w[hf]\s?1000\s?xm\d"),
    "microsoft": ("microsoft", "xbox"),
    "nintendo": ("nintendo", "joycon", r"switch\s(?:oled|lite)"),
    "valve": ("valve", r"steam\sdeck"),
    "asus": ("asus", "rog", "zenbook", "vivobook"),
    "lenovo": ("lenovo", r"legion\sgo", "legion", "thinkpad", "ideapad"),
    "hp": ("hp", r"hewlett\spackard", "omen", "victus", "pavilion"),
    "dell": ("dell", "alienware", "inspiron", "latitude"),
    "acer": ("acer", "aspire", "predator"),
    "msi": ("msi",),
    "bose": ("bose",),
    "jbl": ("jbl",),
    "beats": ("beats", "powerbeats"),
    "sennheiser": ("sennheiser",),
    "garmin": ("garmin", "forerunner", "fenix"),
    "gopro": ("gopro",),
    "dji": ("dji",),
    "canon": ("canon",),
    "nikon": ("nikon",),
    "fujifilm": ("fujifilm", "fuji", "instax"),
    "amazon": ("amazon", "kindle", r"echo\s(?:dot|show|pop|studio|spot)"),
    "nvidia": ("nvidia", "geforce", "rtx"),
    "amd": ("amd", "radeon", "ryzen"),
    "dewalt": ("dewalt",),
    "makita": ("makita",),
    "milwaukee": ("milwaukee",),
    "bosch": ("bosch",),
    "lego": ("lego",),
    "nike": ("nike", r"air\sjordan", "jordan"),
    "adidas": ("adidas", "yeezy"),
    "logitech": ("logitech",),
}

#: Brands that (in a resale context) imply a category when no model rule matched.
BRAND_DEFAULT_CATEGORY: dict[str, str] = {
    "lego": "toy",
    "dewalt": "tool",
    "makita": "tool",
    "milwaukee": "tool",
    "gopro": "camera",
    "dji": "drone",
    "canon": "camera",
    "nikon": "camera",
    "fujifilm": "camera",
    "nike": "sneakers",
    "adidas": "sneakers",
    "garmin": "smartwatch",
    "bose": "audio",
    "jbl": "audio",
    "beats": "audio",
    "sennheiser": "audio",
    "valve": "handheld_pc",
    "nintendo": "console",
}

#: Brand values sources use for "no brand" - never treated as a brand.
GENERIC_BRANDS: frozenset[str] = frozenset(
    {"generico", "generica", "sin marca", "otra", "otro", "otros", "n/a", "na", "none", "unbranded", "generic"}
)

_BRAND_ALT: list[tuple[str, str]] = sorted(
    ((alias, brand) for brand, aliases in BRAND_ALIASES.items() for alias in aliases),
    key=lambda pair: (-len(pair[0]), pair[0]),
)
BRAND_RE = re.compile(r"\b(?:" + "|".join(f"(?P<b{i}>{alias})" for i, (alias, _) in enumerate(_BRAND_ALT)) + r")(?!\w)")
_BRAND_BY_GROUP: dict[str, str] = {f"b{i}": brand for i, (_, brand) in enumerate(_BRAND_ALT)}


def find_brand(text: str) -> tuple[str, int] | None:
    """Earliest brand alias in normalized ``text`` -> (canonical brand, position), or None."""
    m = BRAND_RE.search(text)
    if not m or m.lastgroup is None:
        return None
    return _BRAND_BY_GROUP[m.lastgroup], m.start()


def canonical_brand(value: str | None) -> str | None:
    """Map a source-reported brand (already normalized text) to a canonical brand name."""
    if not value:
        return None
    value = value.strip()
    if not value or value in GENERIC_BRANDS:
        return None
    found = find_brand(value)
    if found and found[1] == 0:
        return found[0]
    return value


# ------------------------------------------------------------------------------------------------
# Condition vocabulary (Spanish + English).  Checked on normalized text, in this priority order.
# ------------------------------------------------------------------------------------------------

FOR_PARTS_CUES: tuple[str, ...] = (
    r"para\spiezas",
    r"solo\spiezas",
    r"para\srefacciones",
    r"refacciones",
    r"for\sparts",
    r"parts\sonly",
    r"not\sworking",
    r"no\senciende",
    r"no\sprende",
    r"no\sfunciona",
    r"danad[oa]",
    r"pantalla\srota",
    r"placa\sdanada",
    r"bloquead[oa]\spor\sicloud",
    r"icloud\sbloqueado",
    r"cuenta\sicloud",
    r"imei\sreportado",
    r"reportad[oa]",
)

#: Weaker subset used for descriptions (long free text produces more false positives).
FOR_PARTS_DESCRIPTION_CUES: tuple[str, ...] = (
    r"para\spiezas",
    r"solo\spiezas",
    r"para\srefacciones",
    r"for\sparts",
    r"not\sworking",
    r"no\senciende",
    r"no\sprende",
    r"imei\sreportado",
    r"bloquead[oa]\spor\sicloud",
)

REFURBISHED_CUES: tuple[str, ...] = (
    r"reacondicionad[oa]s?",
    r"refurbished",
    r"renewed",
    r"renovad[oa]",
    r"certified\srefurbished",
)

USED_CUES: tuple[str, ...] = (
    r"seminuev[oa]s?",
    r"semi\snuev[oa]",
    r"como\snuev[oa]",
    r"like\snew",
    r"usad[oa]s?",
    r"used",
    r"open\sbox",
    r"caja\sabierta",
    r"de\sexhibicion",
    r"exhibicion",
    r"pre\s?owned",
    r"segunda\smano",
    r"buen\sestado",
    r"detalles\sesteticos",
)

NEW_CUES: tuple[str, ...] = (
    r"nuevo\sen\scaja",
    r"nuevo\ssellado",
    r"brand\snew",
    r"sellad[oa]s?",
    r"sealed",
    r"nuev[oa]s?",
    r"new",
)

#: Mercado Libre / generic source condition strings (normalized) -> Condition value.
SOURCE_CONDITION_MAP: dict[str, str] = {
    "new": "new",
    "nuevo": "new",
    "nueva": "new",
    "brand new": "new",
    "new with box": "new",
    "new with tags": "new",
    "new without tags": "used",  # eBay: worn/opened apparel; never priced as sealed new
    "new other": "used",
    "new other see details": "used",
    "new with defects": "used",
    "open box": "used",
    "caja abierta": "used",
    "used": "used",
    "usado": "used",
    "usada": "used",
    "seminuevo": "used",
    "seminueva": "used",
    "like new": "used",
    "como nuevo": "used",
    "pre owned": "used",
    "very good": "used",
    "good": "used",
    "acceptable": "used",
    "refurbished": "refurbished",
    "reacondicionado": "refurbished",
    "reacondicionada": "refurbished",
    "renewed": "refurbished",
    "certified refurbished": "refurbished",
    "certified   refurbished": "refurbished",
    "excellent refurbished": "refurbished",
    "very good refurbished": "refurbished",
    "good refurbished": "refurbished",
    "seller refurbished": "refurbished",
    "manufacturer refurbished": "refurbished",
    "for parts or not working": "for_parts",
    "for parts": "for_parts",
    "para piezas": "for_parts",
    "para refacciones": "for_parts",
}

#: eBay numeric conditionId -> Condition value (https://developer.ebay.com/api-docs/sell/static/metadata/condition-id-values.html)
EBAY_CONDITION_IDS: dict[str, str] = {
    "1000": "new",
    "1500": "used",  # New other / open box
    "1750": "used",  # New with defects
    "2000": "refurbished",  # Certified refurbished
    "2010": "refurbished",  # Excellent - refurbished
    "2020": "refurbished",  # Very good - refurbished
    "2030": "refurbished",  # Good - refurbished
    "2500": "refurbished",  # Seller refurbished
    "2750": "used",  # Like new
    "2990": "used",  # Pre-owned excellent
    "3000": "used",
    "3010": "used",  # Pre-owned fair
    "4000": "used",
    "5000": "used",
    "6000": "used",
    "7000": "for_parts",
}

#: Source values that carry no information (fall through to title cues).
UNSPECIFIED_CONDITIONS: frozenset[str] = frozenset({"not specified", "not_specified", "unspecified", "unknown", ""})

# ------------------------------------------------------------------------------------------------
# Accessories, software, bundles
# ------------------------------------------------------------------------------------------------

#: Strong accessory words: when they appear *after* the device name they still indicate an accessory
#: listing unless introduced by an inclusion word ("con funda", "incluye cargador").
STRONG_ACCESSORY_WORDS: tuple[tuple[str, str], ...] = (
    (r"fundas?", "case"),
    (r"cases?", "case"),
    (r"carcasas?", "case"),
    (r"cover", "case"),
    (r"micas?", "screen_protector"),
    (r"cristal\stemplado", "screen_protector"),
    (r"vidrio\stemplado", "screen_protector"),
    (r"protector(?:es)?(?:\sde\spantalla)?", "screen_protector"),
    (r"screen\sprotector", "screen_protector"),
    (r"hidrogel", "screen_protector"),
    (r"cargador(?:es)?", "charger"),
    (r"charger", "charger"),
    (r"eliminador", "charger"),
    (r"cables?", "cable"),
    (r"adaptador(?:es)?", "adapter"),
    (r"adapter", "adapter"),
    (r"soportes?", "mount"),
    (r"holder", "mount"),
    (r"skins?", "skin"),
    (r"vinil", "skin"),
    (r"calcomanias?", "skin"),
    (r"stickers?", "skin"),
    (r"repuestos?", "spare_part"),
    (r"refaccion(?:es)?", "spare_part"),
    (r"flex", "spare_part"),
    (r"teclado", "keyboard"),
    (r"keyboard", "keyboard"),
)

#: Weak accessory words: only count when they *lead* the title (before the device name) or when the
#: device is introduced with "para"/"compatible con" (e.g. "Bateria para iPhone 11").
WEAK_ACCESSORY_WORDS: tuple[tuple[str, str], ...] = (
    (r"estuches?", "case"),
    (r"bolsa", "case"),
    (r"correas?", "strap"),
    (r"extensibles?", "strap"),
    (r"pulsos?", "strap"),
    (r"malla", "strap"),
    (r"band", "strap"),
    (r"strap", "strap"),
    (r"baterias?", "battery"),
    (r"pila", "battery"),
    (r"pantallas?", "screen"),
    (r"display", "screen"),
    (r"modulo", "screen"),
    (r"tapa(?:\strasera)?", "spare_part"),
    (r"bases?(?:\sde\scarga)?", "dock"),
    (r"dock", "dock"),
    (r"docking", "dock"),
    (r"controles?", "controller"),
    (r"control", "controller"),
    (r"joysticks?", "controller"),
    (r"mandos?", "controller"),
    (r"gamepad", "controller"),
    (r"lentes?", "lens"),
    (r"lapiz", "stylus"),
    (r"pencil", "stylus"),
    (r"stylus", "stylus"),
    (r"memoria", "memory_card"),
    (r"micro\s?sd", "memory_card"),
    (r"tarjeta\sde\smemoria", "memory_card"),
    (r"codigo", "digital_code"),
    (r"gift\scard", "digital_code"),
    (r"tarjeta\sde\sregalo", "digital_code"),
    (r"membresia", "digital_code"),
    (r"suscripcion", "digital_code"),
    (r"juegos?", "software"),
    (r"videojuegos?", "software"),
    (r"games?", "software"),
    (r"audifonos", "audio"),
    (r"caja", "empty_box"),
)

#: Anywhere in the title these make the listing an accessory regardless of position.
EMPTY_BOX_CUES: tuple[str, ...] = (
    r"solo\s(?:la\s)?caja",
    r"caja\svacia",
    r"empty\sbox",
    r"box\sonly",
    r"unicamente\s(?:la\s)?caja",
    r"solo\sempaque",
)

#: "Funda para iPhone 15", "Mica compatible con Galaxy S24", "Case for PS5".
COMPAT_BEFORE_DEVICE_RE = re.compile(
    r"(?:\bpara|\bcompatible\scon|\bcompatibles\scon|\bcompatible|\bfor|\bp)"
    r"(?:\s(?:el|la|los|las|tu|su|celular|consola|telefono|reloj|apple|samsung|sony|nintendo|xiaomi|"
    r"motorola|google|microsoft|galaxy|modelo|modelos))*\s$"
)

#: Words that, right before an accessory word, mean the accessory is *included* with the device.
INCLUSION_WORDS: frozenset[str] = frozenset(
    {"con", "y", "incluye", "incluido", "incluida", "incluidos", "mas", "+", "regalo", "e", "sus", "su", "c", "extra"}
)

#: Words that describe the listing as a physical device (an accessory word after them is incidental).
DEVICE_NOUNS: frozenset[str] = frozenset(
    {
        "celular",
        "smartphone",
        "telefono",
        "consola",
        "console",
        "videoconsola",
        "laptop",
        "notebook",
        "computadora",
        "tablet",
        "tableta",
        "smartwatch",
        "reloj",
        "equipo",
        "dron",
        "drone",
    }
)

#: Software (games) cue for consoles: "Juego Spider-Man 2 PS5", "God of War Ragnarok Fisico PS5".
SOFTWARE_CUES_RE = re.compile(r"\bvideojuego\b|(?<!estado\s)\bfisic[oa]\b|\bformato\sfisico\b|\bversion\sfisica\b")

BUNDLE_CUES: tuple[str, ...] = (
    r"(?:^|\s)\+\s?\w",  # "ps5 slim + 2 controles"
    r"\w\+[a-z]{3,}",  # "ps5+juegos" (but not "8+256gb" or "s24+ ")
    r"con\s(?:\d+\s)?juegos?",
    r"(?:\d+|dos|tres)\s(?:juegos|controles|controls)",
    r"combo",
    r"lote",
    r"kit",
    r"paquete",
    r"bundle",
    r"pack",
    r"fly\smore",
    r"^(?:pack\s|paquete\s|lote\s|set\s)?\d{1,2}\s?x(?!\w)",
)

#: "incluye ..." is a bundle unless it only lists in-box standard items.
STANDARD_INCLUSIONS: frozenset[str] = frozenset(
    {
        "cable",
        "cables",
        "cargador",
        "caja",
        "funda",
        "mica",
        "accesorios",
        "manual",
        "manuales",
        "original",
        "originales",
        "usb",
        "c",
        "tipo",
        "de",
        "y",
        "su",
        "sus",
        "correa",
        "estuche",
        "carga",
        "adaptador",
        "factura",
        "garantia",
    }
)

# ------------------------------------------------------------------------------------------------
# Colors (multi-word first)
# ------------------------------------------------------------------------------------------------

COLORS: tuple[tuple[str, str], ...] = (
    (r"titanio\snatural|natural\stitanium", "natural titanium"),
    (r"titanio\snegro|black\stitanium", "black titanium"),
    (r"titanio\sblanco|white\stitanium", "white titanium"),
    (r"titanio\sazul|blue\stitanium", "blue titanium"),
    (r"titanio\sdesierto|desert\stitanium|titanio\sarena", "desert titanium"),
    (r"gris\sespacial|space\sgr[ae]y", "space gray"),
    (r"negro\sespacial|space\sblack", "space black"),
    (r"blanco\sestelar|luz\sestelar|starlight", "starlight"),
    (r"medianoche|midnight", "midnight"),
    (r"azul\scielo|sky\sblue", "sky blue"),
    (r"oro\srosa|rose\sgold", "rose gold"),
    (r"negro|negra|black", "black"),
    (r"blanco|blanca|white", "white"),
    (r"azul|blue", "blue"),
    (r"rojo|roja", "red"),
    (r"verde|green|menta|mint", "green"),
    (r"rosa|rosado|pink", "pink"),
    (r"morado|morada|purpura|purple|lila|violeta|lavanda|lavender", "purple"),
    (r"amarillo|amarilla|yellow", "yellow"),
    (r"gris|gray|grey|grafito|graphite", "gray"),
    (r"plata|plateado|silver", "silver"),
    (r"dorado|dorada|gold", "gold"),
    (r"naranja|orange", "orange"),
    (r"turquesa|turquoise", "turquoise"),
    (r"coral", "coral"),
    (r"cafe|marron|brown", "brown"),
    (r"beige|crema|cream", "beige"),
    (r"titanio|titanium", "titanium"),
)

# ------------------------------------------------------------------------------------------------
# Variant tokens
# ------------------------------------------------------------------------------------------------

#: Descriptive tokens reported in NormalizedAttributes.variant_tokens (not all are identity-relevant).
VARIANT_WORDS: frozenset[str] = frozenset(
    {
        "pro",
        "max",
        "plus",
        "ultra",
        "mini",
        "lite",
        "oled",
        "lcd",
        "slim",
        "5g",
        "4g",
        "lte",
        "fe",
        "ti",
        "super",
        "se",
        "air",
        "edge",
        "fold",
        "flip",
        "xl",
        "neo",
        "classic",
    }
)

#: Prefix tokens that do not count as "unknown words before the model" (see extract._uncertain_prefix).
PREFIX_FILLERS: frozenset[str] = frozenset(
    set(DEVICE_NOUNS)
    | {
        "nuevo", "nueva", "nuevos", "sellado", "sellada", "original", "originales", "oferta", "remate",
        "liquidacion", "promocion", "promo", "mega", "super", "gran", "barato", "barata", "usado", "usada",
        "seminuevo", "seminueva", "reacondicionado", "reacondicionada", "renewed", "new", "used",
        "libre", "liberado", "desbloqueado", "de", "la", "el", "los", "las", "un", "una", "the", "a",
        "inalambrico", "inalambricos", "inalambrica", "inalambricas", "bluetooth", "wireless", "envio",
        "gratis", "inteligente", "audifonos", "auriculares", "portatil", "gamer", "gaming", "tarjeta",
        "video", "grafica", "camara", "digital", "reloj", "videojuegos", "videoconsola", "smart", "watch",
        "tenis", "zapatillas", "kit", "1", "lector", "libros", "electronico", "e", "reader", "ereader",
        "modelo", "version", "edicion", "msi", "gigabyte", "zotac", "evga", "pny", "asus", "rog", "strix",
        "tuf", "dual", "ventus", "gaming", "oc", "eagle", "windforce", "aorus", "xfx", "sapphire",
        "powercolor", "rotomartillo", "taladro", "atornillador", "drone", "dron", "lego", "set", "dama",
        "hombre", "mujer", "caballero", "unisex", "nino", "nina",
    }
)


# ------------------------------------------------------------------------------------------------
# Generation / ordinal helpers
# ------------------------------------------------------------------------------------------------

ORDINAL_WORDS: dict[str, int] = {
    "primera": 1, "primer": 1, "segunda": 2, "segundo": 2, "tercera": 3, "tercer": 3, "cuarta": 4,
    "quinta": 5, "sexta": 6, "septima": 7, "octava": 8, "novena": 9, "decima": 10, "onceava": 11,
    "undecima": 11, "doceava": 12, "duodecima": 12, "first": 1, "second": 2, "third": 3, "fourth": 4,
    "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
}
_ORDINAL_RE = re.compile(r"(\d{1,2})(?:a|da|ra|era|ta|va|ma|na|o|ava|th|st|nd|rd)")
_GEN_WORDS = frozenset({"gen", "generacion", "generation", "generaciones"})


def ordinal_value(token: str) -> int | None:
    """'2da' -> 2, 'segunda' -> 2, '10' -> 10, '4th' -> 4; None if not an ordinal/number."""
    if token in ORDINAL_WORDS:
        return ORDINAL_WORDS[token]
    if token.isdigit():
        value = int(token)
        return value if 1 <= value <= 20 else None
    m = _ORDINAL_RE.fullmatch(token)
    if m:
        value = int(m.group(1))
        return value if 1 <= value <= 20 else None
    return None


def find_generation(tokens: list[str]) -> int | None:
    """Find an explicit generation mention ('2da generacion', 'gen 10', '10th gen') in ``tokens``."""
    for i, tok in enumerate(tokens):
        if tok in _GEN_WORDS:
            if i > 0:
                prev = ordinal_value(tokens[i - 1])
                if prev:
                    return prev
            if i + 1 < len(tokens):
                nxt = ordinal_value(tokens[i + 1])
                if nxt:
                    return nxt
        m = re.fullmatch(r"(\d{1,2})(?:a|da|ra|ta|va|ma|na|th|st|nd|rd)?gen", tok)
        if m:
            return int(m.group(1))
    return None


def next_tokens(text: str, pos: int, n: int = 4) -> list[str]:
    """The ``n`` whitespace tokens of ``text`` that start at or after ``pos``."""
    return text[pos:].split()[:n]


def has(text: str, pattern: str) -> bool:
    """Whole-word regex search on normalized text."""
    return re.search(r"(?<!\w)(?:" + pattern + r")(?!\w)", text) is not None


# ------------------------------------------------------------------------------------------------
# Model rules
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Built:
    """Result of a model rule builder.  ``model=None`` means: family recognised, product ambiguous."""

    model: str | None
    tokens: tuple[str, ...] = ()
    note: str | None = None


Builder = Callable[[re.Match[str], str], "Built | None"]


@dataclass(frozen=True)
class ModelRule:
    name: str
    brand: str
    category: str
    pattern: re.Pattern[str]
    build: Builder
    accessory_type: str | None = None  # the recognised model *is* an accessory (e.g. DualSense)


@dataclass(frozen=True)
class ModelHit:
    rule: str
    brand: str
    category: str
    model: str | None
    start: int
    end: int
    tokens: tuple[str, ...] = ()
    accessory_type: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
