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
from dataclasses import dataclass, field, replace
from decimal import ROUND_FLOOR, Decimal

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
    # repair needed / defects (MX phrasing)
    r"para\sreparar",
    r"a\sreparar",
    r"(?:necesita|requiere)\sreparacion",
    r"no\ssirve",
    r"con\sfallas?",
    r"fallas?\sde\s(?:senal|red|audio|camara|imagen|carga|bocina|microfono|touch|face\sid)",
    r"no\scarga",
    r"mojad[oa]",
    r"(?:cristal|vidrio|display|tapa(?:\strasera)?|camara)\s(?:rot[oa]|estrellad[oa]|quebrad[oa])",
    r"pantalla\s(?:estrellada|quebrada)",
    r"bateria\s(?:inflada|hinchada)",
    r"no\sda\s(?:imagen|video)",
    r"(?:en|por)\spartes",
    r"(?:sin|no)\s(?:face|touch)\sid",
    r"(?:face|touch)\sid\s(?:no\sfunciona|no\ssirve|fallando|danado|desactivado)",
    # US phrasing (eBay): carrier/blacklist locks make the phone unusable as an unlocked MX resale unit
    r"(?:carrier|network|sim|icloud|verizon|at\st|att|t\smobile|tmobile|sprint|cricket|metro(?:\spcs)?|"
    r"boost(?:\smobile)?|tracfone|straight\stalk|us\scellular|xfinity|visible)\slocked",
    r"locked\sto\s(?:carrier|network|verizon|at\st|att|t\smobile|tmobile|sprint|cricket)",
    r"blacklist(?:ed)?",
    r"bad\s(?:esn|imei)",
    r"cracked(?:\s(?:screen|glass|back|lcd|display))?",
    r"(?:broken|shattered)\s(?:screen|glass|lcd|display)",
    r"water\sdamaged?",
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
    r"para\sreparar",
    r"bateria\s(?:inflada|hinchada)",
    r"no\sda\s(?:imagen|video)",
    r"blacklist(?:ed)?",
    r"bad\s(?:esn|imei)",
)

#: Packaging words: a "dañado" right after one of them describes the box, not the item ("caja dañada").
PACKAGING_WORDS: frozenset[str] = frozenset({"caja", "empaque", "empaques", "embalaje", "envoltura", "box", "blister"})

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
    # opened / unboxed: not sealed-new stock ("nuevo abierto", "nuevo sin caja"), but "abierto a ofertas"
    # is about the price
    r"abiert[oa]s?(?!\s(?:a|al|para)(?!\w))",
    r"sin\scaja",
    r"sin\ssellos?",
)

NEW_CUES: tuple[str, ...] = (
    r"nuevo\sen\scaja",
    r"nuevo\ssellado",
    r"brand\snew",
    r"sellad[oa]s?",
    r"sealed",
    r"nuev[oa]s?(?!\s(?:leon|laredo)(?!\w))",  # "Nuevo León" / "Nuevo Laredo" are places
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
    "damaged": "for_parts",  # schema.org DamagedCondition (collectors/jsonld.py)
    "damaged condition": "for_parts",
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
    (r"(?:placa|tarjeta)\s(?:logica|madre|base)", "spare_part"),
    (r"logic\sboard", "spare_part"),
    (r"motherboard", "spare_part"),
    (r"chasis", "spare_part"),
    (r"izquierd[oa]", "spare_part"),  # a single earbud / Joy-Con side, not the set
    (r"derech[oa]", "spare_part"),
    (r"housing", "spare_part"),
    (r"helices?", "spare_part"),  # drone propellers
    (r"propelas?", "spare_part"),
    (r"propellers?", "spare_part"),
    (r"teclado", "keyboard"),
    (r"keyboard", "keyboard"),
)

#: Parts that carry the device's own specs (a logic board has the storage chip): after the device name
#: they mean a part listing even when storage or "celular"/"consola" is stated.
PART_WORDS_ANY_CONTEXT: tuple[str, ...] = (
    r"(?:placa|tarjeta)\s(?:logica|madre|base)",
    r"logic\sboard",
    r"motherboard",
    r"chasis",
    r"housing",
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
    (r"s\s?pen", "stylus"),
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
    r"(?<![\w.])(?:[2-9]|dos|tres|cuatro|cinco)\s(?:piezas|pzas?|pz|pcs|unidades|equipos)(?!\w)",  # "(2 piezas)"
    r"(?<!\w)(?:control(?:es)?|mandos?|joycons?|dualsense)\s(?:extra|adicional(?:es)?)(?!\w)",  # "con control extra"
    r"(?<!\w)(?:con|\+|y|mas|incluye)\s(?:el\s|un\s|unos\s|lentes\s)?(?:ps\s?vr|vr\s?2|playstation\svr)",  # + PS VR2
    r"^(?:un\s)?par\sde\s",  # "Par de AirPods": two units, or loose earbuds - never one standard unit
)

#: Leading quantity words ("2 PS5 Slim", "Dos iPhone 13"): a bundle when a brand/model follows.
LEADING_QUANTITIES: frozenset[str] = frozenset(
    {"2", "3", "4", "5", "6", "7", "8", "9", "10", "dos", "tres", "cuatro", "cinco", "seis"}
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
    (r"titanio\snatural|natural\stitanium|titanium\snatural", "natural titanium"),
    (r"titanium\sgr[ae]y|titanio\sgris|gris\stitanio", "gray titanium"),
    (r"titanium\sblack|negro\stitanio", "black titanium"),
    (r"titanium\sviolet|titanio\svioleta", "violet titanium"),
    (r"titanium\syellow|titanio\samarillo", "yellow titanium"),
    (r"titanium\ssilver\sblue|titanium\sblue", "blue titanium"),
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

#: Prefix tokens that do not count as "unknown words before the model" (see the ambiguity guard in extract._analyze_text).
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
        "tuf", "dual", "ventus", "oc", "eagle", "windforce", "aorus", "xfx", "sapphire",
        "powercolor", "rotomartillo", "taladro", "atornillador", "drone", "dron", "lego", "set", "dama",
        "hombre", "mujer", "caballero", "unisex", "nino", "nina",
    }
)


#: Words after a console name that still describe the console itself (condition, edition, revision,
#: standard parts).  Any other word after a bare console name ("Nintendo Switch Mario Kart 8 Deluxe",
#: "PS5 Spider-Man 2") makes the listing ambiguous: it is probably a game or an accessory.
SUFFIX_FILLERS: frozenset[str] = frozenset(
    {
        "usado", "usada", "usados", "usadas", "seminuevo", "seminueva", "seminuevos", "nuevo", "nueva", "nuevos",
        "sellado", "sellada", "original", "originales", "reacondicionado", "reacondicionada", "excelente",
        "excelentes", "estado", "buen", "buena", "bueno", "perfecto", "perfecta", "perfectas", "perfectamente",
        "impecable", "impecables", "funcionando", "funciona", "funcional", "bien", "detalle", "detalles",
        "garantia", "factura", "envio", "gratis", "completo", "completa", "completos", "poco", "uso",
        "oportunidad", "remate", "oferta", "barato", "barata", "liberado", "liberada", "desbloqueado",
        "desbloqueada", "version", "edicion", "modelo", "model", "color", "mexicana", "mexicano", "mx",
        "americana", "americano", "japonesa", "japones", "neon", "v1", "v2", "normal", "fat", "estandar",
        "standard", "lector", "discos", "disco", "disc", "digital", "sin", "no", "es", "ni", "en", "caja", "cajas",
        "accesorios", "listo", "lista", "como", "hdmi", "micro", "sd", "microsd", "joycon", "joycons",
        "dualsense", "dualshock", "heg", "hdh", "hac", "cfi", "001", "pulgadas", "pantalla", "oled", "lcd",
        "portatil", "consola", "videoconsola", "hibrida", "sus", "su", "todo", "todos", "solo", "unico", "unica",
        "dueno", "duena", "mi", "x", "s",
    }
)

#: Condition, cosmetic-defect, paperwork and logistics words of Mexican listings ("bien cuidado", "sin
#: rayones", "buenas condiciones", "con ticket de compra", "entrega inmediata CDMX", "precio negociable").
#: They describe the unit or the sale, never another product, so after a console name they keep the
#: listing a console (extract._known_token).  Deliberately no game/franchise words: an unknown word after
#: a bare console name still means "possibly a game or accessory".
LISTING_DESCRIPTION_WORDS: frozenset[str] = frozenset(
    {
        # condition
        "semi", "seminuevas", "cuidado", "cuidada", "cuidados", "cuidadas", "cuidadisimo", "cuidadisima",
        "conservado", "conservada", "buenas", "buenos", "condicion", "condiciones", "estetico", "estetica",
        "esteticos", "esteticas", "ligero", "ligera", "ligeros", "ligeras", "leve", "leves", "minimo", "minima",
        "minimos", "minimas", "pequeno", "pequena", "pequenos", "pequenas", "muy", "casi", "poquito", "usos",
        "limpio", "limpia", "intacto", "intacta", "nuevecito", "nuevecita", "flamante", "abierto", "abierta",
        "revisado", "revisada", "probado", "probada", "drift",
        # cosmetic defects ("con un rayon", "mini detalle", "marcas de uso")
        "rayon", "rayones", "rayoncito", "rayoncitos", "rayita", "rayitas", "raya", "rayas", "rayado", "rayada",
        "rayadura", "rayaduras", "rasguno", "rasgunos", "golpe", "golpes", "golpecito", "golpecitos", "marca",
        "marcas", "marquita", "marquitas", "desgaste", "mancha", "manchas", "detallito", "detallitos", "esquina",
        "esquinas",
        # paperwork
        "ticket", "tickets", "compra", "nota", "facturada", "facturado", "facturable", "garantizado",
        "garantizada",
        # logistics and sale terms
        "cdmx", "df", "edomex", "gdl", "guadalajara", "monterrey", "mty", "puebla", "queretaro", "entrega",
        "entregas", "inmediata", "inmediato", "personal", "envios", "hoy", "mismo", "dia", "precio",
        "negociable", "negociables", "trato", "acepto", "cambios", "ofertas", "urge", "vendo", "remato",
        "ocasion", "msi", "meses", "intereses", "ano", "anos", "semana", "semanas", "dias",
        # grammar words inside such phrases ("todo lo original", "de compra", "en pantalla")
        "lo", "de", "del", "a", "al", "un", "una", "el", "la", "los", "las",
    }
)

#: In-box items that may follow "con"/"y" after a console ("con caja y manuales", "con grip").  Not in
#: LISTING_DESCRIPTION_WORDS: right after a bare console name they are the item sold ("Nintendo Switch OLED
#: Grip").
INBOX_ITEM_WORDS: frozenset[str] = frozenset(
    {"manual", "manuales", "instructivo", "instructivos", "empaque", "empaques", "grip", "correas"}
)

#: Cosmetic-defect nouns.  "mini" right before one of them is the Spanish adjective ("mini detalle",
#: "mini rayon": a tiny defect), not the iPhone/iPad mini model word.
DEFECT_NOUNS: frozenset[str] = frozenset(
    {
        "detalle", "detalles", "detallito", "detallitos", "rayon", "rayones", "rayoncito", "rayoncitos", "rayita",
        "rayitas", "raya", "rayas", "rayadura", "rayaduras", "rasguno", "rasgunos", "golpe", "golpes", "golpecito",
        "golpecitos", "marca", "marcas", "marquita", "marquitas", "desgaste", "mancha", "manchas", "fisura",
        "fisuras", "despostillado", "despostille", "defecto", "defectos",
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


#: Words that negate a following variant word ("no es OLED", "sin cancelacion de ruido", "no lite").
_VARIANT_NEGATORS = frozenset({"no", "sin", "ni", "not", "non"})
#: Words allowed between the negator and the variant word ("no es la OLED", "no es modelo lite").
_VARIANT_NEGATION_GLUE = frozenset(
    {"es", "esta", "son", "is", "la", "el", "una", "un", "modelo", "version", "edicion", "model", "the", "a"}
)


def is_negated(text: str, pos: int) -> bool:
    """True when the word at ``pos`` is negated by up to three preceding words ("no es la OLED")."""
    for tok in reversed(text[:pos].split()[-3:]):
        if tok in _VARIANT_NEGATORS:
            return True
        if tok not in _VARIANT_NEGATION_GLUE:
            return False
    return False


def affirmed(text: str, pattern: str) -> bool:
    """Like :func:`has` but ignores negated mentions: a seller writing "no es OLED" or "no lite" says the
    unit is NOT that variant, so the word must never promote the model to it."""
    return any(
        not is_negated(text, m.start()) for m in re.finditer(r"(?<!\w)(?:" + pattern + r")(?!\w)", text)
    )


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


def _amb(note: str) -> Built:
    """Family recognised but the exact sellable product is ambiguous -> never clustered."""
    return Built(None, (), note)


def _suffix(text: str, pos: int, options: tuple[str, ...], n: int = 3) -> str:
    """Return ' <option>' when the tokens right after ``pos`` start with one of ``options``."""
    nxt = next_tokens(text, pos, n)
    for opt in sorted(options, key=lambda o: (-len(o.split()), o)):
        parts = opt.split()
        if nxt[: len(parts)] == parts:
            return " " + opt
    return ""


def _net(text: str, pos: int) -> str:
    """' 5g' / ' 4g' when the network generation is stated after the model name.

    The whole rest of the title is scanned: sellers often put it last ("Redmi Note 13 Pro 8gb Ram 256gb
    Rom 5G"), and the 4G and 5G versions are different products with different prices.
    """
    rest = text[pos:]
    if affirmed(rest, "5g"):
        return " 5g"
    if affirmed(rest, "4g"):
        return " 4g"
    return ""


def _attached_plus(text: str, pos: int) -> bool:
    return text[pos : pos + 1] == "+"


# --- Apple ---------------------------------------------------------------------------------------

_IPHONE_VALID: dict[str, frozenset[str]] = {
    "11": frozenset({"", "pro", "pro max"}),
    "12": frozenset({"", "mini", "pro", "pro max"}),
    "13": frozenset({"", "mini", "pro", "pro max"}),
    "14": frozenset({"", "plus", "pro", "pro max"}),
    "15": frozenset({"", "plus", "pro", "pro max"}),
    "16": frozenset({"", "plus", "pro", "pro max", "e"}),
    "17": frozenset({"", "pro", "pro max", "e"}),
}


def _iphone(m: re.Match[str], text: str) -> Built:
    num = m["num"]
    pos = m.end()
    plus = _attached_plus(text, pos)
    if plus:
        pos += 1
    nxt = next_tokens(text, pos, 3)
    if num == "air":
        return Built("iphone air")
    if num in ("x", "xr", "xs"):
        if num == "xs" and nxt[:1] == ["max"]:
            return Built("iphone xs max")
        return Built(f"iphone {num}")
    if num == "se":
        years = {"2016": 1, "2020": 2, "2022": 3}
        gen = years.get(nxt[0]) if nxt else None
        gen = gen or find_generation(nxt) or (ordinal_value(nxt[0]) if nxt else None)
        if gen in (1, 2, 3):
            return Built(f"iphone se {gen}")
        return _amb("iPhone SE generation not stated")
    if m["e"] or nxt[:1] == ["e"]:
        suffix = "e"
    elif plus:  # "iphone 15+" (attached); a spaced " + " is a bundle separator, not the Plus model
        suffix = "plus"
    elif nxt[:2] == ["pro", "max"] or nxt[:1] == ["pm"]:  # "PM": common MX shorthand for Pro Max
        suffix = "pro max"
    elif nxt[:1] == ["pro"]:
        suffix = "pro"
    elif nxt[:1] == ["plus"]:
        suffix = "plus"
    elif nxt[:1] == ["mini"]:
        suffix = "mini"
    elif nxt[:1] == ["max"]:
        return _amb(f"'iPhone {num} max' is ambiguous (Pro Max or Plus?)")
    else:
        suffix = ""
    if suffix not in _IPHONE_VALID[num]:
        return _amb(f"'iPhone {num} {suffix}' is not a model Apple sells")
    if suffix == "e":
        return Built(f"iphone {num}e")
    return Built(f"iphone {num}" + (f" {suffix}" if suffix else ""))


def _airpods(m: re.Match[str], text: str) -> Built:
    line = m["line"]
    nxt = next_tokens(text, m.end(), 4)
    gen = find_generation(nxt) or (ordinal_value(nxt[0]) if nxt else None)
    if line == "max":
        return Built("airpods max")
    if line == "pro":
        if gen in (1, 2, 3):
            return Built(f"airpods pro {gen}")
        return _amb("AirPods Pro generation not stated (Pro 1/2/3 differ a lot in price)")
    if gen in (1, 2, 3, 4):
        if gen == 4 and affirmed(text, r"anc|cancelacion(?:\sactiva)?(?:\sde\sruido)?|noise\scancell?ing"):
            return Built("airpods 4 anc")
        return Built(f"airpods {gen}")
    return _amb("AirPods generation not stated")


def _apple_watch(m: re.Match[str], text: str) -> Built:
    rest = " ".join(next_tokens(text, m.end(), 5))
    s = re.match(r"(?:series?\s?|s)(\d{1,2})(?![\w.])", rest) or re.match(r"(\d{1,2})(?![\w.])", rest)
    if s and 1 <= int(s.group(1)) <= 11:
        return Built(f"apple watch series {int(s.group(1))}")
    se = re.match(r"se(?:\s(\S+))?(?:\s(\S+))?", rest)
    if se:
        toks = [t for t in (se.group(1), se.group(2)) if t]
        years = {"2020": 1, "2022": 2, "2023": 2, "2025": 3}
        gen = (years.get(toks[0]) if toks else None) or find_generation(toks) or (
            ordinal_value(toks[0]) if toks else None
        )
        if gen in (1, 2, 3):
            return Built("apple watch se" + (f" {gen}" if gen > 1 else ""))
        return _amb("Apple Watch SE generation not stated")
    ultra = re.match(r"ultra(?:\s(\d))?(?!\w)", rest)
    if ultra:
        if ultra.group(1) in ("1", "2", "3"):
            return Built(f"apple watch ultra {ultra.group(1)}")
        return _amb("Apple Watch Ultra generation not stated")
    return _amb("Apple Watch series not stated")


_IPAD_A_CHIPS = {"a16": "ipad 11", "a14": "ipad 10", "a13": "ipad 9", "a12": "ipad 8"}
_IPAD_AIR_GEN_CHIP = {5: "m1", 6: "m2", 7: "m3"}

#: Words after a bare number that make it something other than a screen size ("16 ram", "8 nucleos").
_NOT_SIZE_AFTER = frozenset({"ram", "gb", "tb", "ssd", "hdd", "nucleos", "cores", "core", "gpu", "cpu", "horas",
                             "ciclos", "meses", "anos", "dias", "gen", "generacion", "generation"})


def inch_token(value: str) -> str:
    """'12.9' -> '13in', '10.9' -> '11in', '13.6' -> '13in', '15.3' -> '15in' (marketing size class)."""
    number = Decimal(value)
    whole = number.to_integral_value(rounding=ROUND_FLOOR)
    if number - whole >= Decimal("0.85"):
        whole += 1
    return f"{int(whole)}in"


def _bare_sizes(toks: list[str], allowed: frozenset[str]) -> list[str]:
    """Screen sizes written as bare numbers ("iPad Pro M4 11", "MacBook Air 15.3") -> inch tokens."""
    sizes = []
    for i, tok in enumerate(toks):
        if tok in allowed and not (i + 1 < len(toks) and toks[i + 1] in _NOT_SIZE_AFTER):
            sizes.append(inch_token(tok))
    return sizes


_IPAD_BARE_SIZES = frozenset({"11", "13", "12.9", "10.9", "10.5", "9.7", "8.3"})
_MACBOOK_BARE_SIZES = frozenset({"13", "13.3", "13.6", "14", "14.2", "15", "15.3", "16", "16.2"})


def _ipad(m: re.Match[str], text: str) -> Built:
    line = m["line"] or "base"
    toks = next_tokens(text, m.end(), 8)
    joined = " ".join(toks)
    chip_m = re.search(r"(?<!\w)m([1-5])(?!\w)", joined)
    chip_a = re.search(r"(?<!\w)a(1[2-9])(?:\s?(?:pro|bionic))?(?!\w)", joined)
    gen = find_generation(toks)
    direct = int(toks[0]) if toks and toks[0].isdigit() else None
    tokens: tuple[str, ...] = ()
    if line == "base":
        gen = gen or (direct if direct and direct <= 11 else None)
        if gen:
            return Built(f"ipad {gen}")
        if chip_a and f"a{chip_a.group(1)}" in _IPAD_A_CHIPS:
            return Built(_IPAD_A_CHIPS[f"a{chip_a.group(1)}"])
        return _amb("iPad generation not stated")
    if line in ("pro", "air"):
        # The size may follow the chip ("iPad Pro M4 11"): bare 11/13/12.9 are sizes for these lines.
        sizes = sorted(set(_bare_sizes(toks[:6], _IPAD_BARE_SIZES)))
        tokens = tuple(sizes)
        if direct in (11, 13):
            direct = None
    if line == "air":
        if chip_m:
            return Built(f"ipad air m{chip_m.group(1)}", tokens)
        gen = gen or (direct if direct and direct <= 7 else None)
        if gen in _IPAD_AIR_GEN_CHIP:
            return Built(f"ipad air {_IPAD_AIR_GEN_CHIP[gen]}", tokens)
        if gen:
            return Built(f"ipad air {gen}", tokens)
        return _amb("iPad Air generation/chip not stated")
    if line == "pro":
        if chip_m:
            return Built(f"ipad pro m{chip_m.group(1)}", tokens)
        if gen:
            return Built(f"ipad pro gen {gen}", tokens)
        return _amb("iPad Pro chip/generation not stated")
    # mini
    if chip_a and chip_a.group(1) == "17":
        return Built("ipad mini a17 pro")
    gen = gen or (direct if direct and direct <= 7 else None)
    if gen:
        return Built(f"ipad mini {gen}")
    return _amb("iPad mini generation not stated")


def _macbook(m: re.Match[str], text: str) -> Built:
    line = m["line"]
    if not line:
        return _amb("MacBook line (Air/Pro) not stated")
    toks = next_tokens(text, m.end(), 8)
    chip = re.search(r"(?<!\w)m([1-5])(?:\s(pro|max|ultra))?(?!\w)", " ".join(toks)) or re.search(
        r"(?<!\w)(?:chip\s)?m([1-5])(?:\s(pro|max|ultra))?(?!\w)", text
    )
    if not chip:
        return _amb("MacBook chip not stated (Intel or unknown Apple silicon)")
    # Bare sizes anywhere near the name ("MacBook Air 15.3 M2", "MacBook Pro M3 Pro 14"); several different
    # sizes -> no screen identity (and the key requirement below leaves multi-size lines unclustered).
    tokens = tuple(sorted(set(_bare_sizes(toks, _MACBOOK_BARE_SIZES))))
    tier = f" {chip.group(2)}" if chip.group(2) else ""
    return Built(f"macbook {line} m{chip.group(1)}{tier}", tokens)


def _apple_pencil(m: re.Match[str], text: str) -> Built:
    nxt = next_tokens(text, m.end(), 3)
    if nxt[:1] == ["pro"]:
        return Built("apple pencil pro")
    if nxt[:2] == ["usb", "c"]:
        return Built("apple pencil usb c")
    gen = find_generation(nxt) or (ordinal_value(nxt[0]) if nxt else None)
    if gen in (1, 2):
        return Built(f"apple pencil {gen}")
    return _amb("Apple Pencil generation not stated")


# --- Samsung -------------------------------------------------------------------------------------


def _galaxy_s(m: re.Match[str], text: str) -> Built:
    num = m["num"]
    nxt = next_tokens(text, m.end(), 2)
    if m["plus"] or nxt[:1] == ["plus"]:
        return Built(f"galaxy s{num}+")
    for suffix in ("ultra", "fe", "edge"):
        if nxt[:1] == [suffix]:
            return Built(f"galaxy s{num} {suffix}")
    return Built(f"galaxy s{num}")


def _galaxy_am(m: re.Match[str], text: str) -> Built:
    return Built(f"galaxy {m['line']}{m['num']}{m['s'] or ''}{_net(text, m.end())}")


def _galaxy_z(m: re.Match[str], text: str) -> Built:
    return Built(f"galaxy z {m['kind']}{m['num']}{_suffix(text, m.end(), ('fe',))}")


def _galaxy_tab(m: re.Match[str], text: str) -> Built:
    plus = "+" if m["plus"] else ""
    suffix = "" if plus else _suffix(text, m.end(), ("ultra", "fe+", "fe plus", "fe", "plus", "lite", "+"))
    if suffix in (" plus", " +"):
        suffix, plus = "", "+"
    if suffix == " fe plus":
        suffix = " fe+"  # Tab S9 FE+ / S10 FE+: a different (cheaper) model than the flagship Tab S9+
    return Built(f"galaxy tab {m['line']}{m['num']}{plus}{suffix}")


def _galaxy_watch(m: re.Match[str], text: str) -> Built:
    num = m["num"]
    suffix = _suffix(text, m.end(), ("classic", "pro", "fe", "ultra"))
    if num:
        return Built(f"galaxy watch {num}{suffix}")
    if suffix in (" ultra", " fe"):
        return Built(f"galaxy watch{suffix}")
    return _amb("Galaxy Watch generation not stated")


def _galaxy_buds(m: re.Match[str], text: str) -> Built:
    num = m["num"]
    if m["plus"] and not num:
        return Built("galaxy buds+")
    suffix = _suffix(text, m.end(), ("pro", "fe", "live", "core"))
    if num:
        return Built(f"galaxy buds {num}{suffix}")
    if suffix:
        return Built(f"galaxy buds{suffix}")
    return _amb("Galaxy Buds generation not stated")


# --- Xiaomi / Motorola / Google / other Android -------------------------------------------------


def _redmi_note(m: re.Match[str], text: str) -> Built:
    suffix = _suffix(text, m.end(), ("pro+", "pro plus", "pro", "plus"))
    if suffix == " pro plus":
        suffix = " pro+"
    return Built(f"redmi note {m['num']}{m['s'] or ''}{suffix}{_net(text, m.end())}")


def _redmi(m: re.Match[str], text: str) -> Built:
    return Built(f"redmi {m['num']}{_net(text, m.end())}")


def _poco(m: re.Match[str], text: str) -> Built:
    suffix = _suffix(text, m.end(), ("pro", "gt", "ultra", "neo"))
    return Built(f"poco {m['line']}{m['num']}{suffix}{_net(text, m.end())}")


def _xiaomi_num(m: re.Match[str], text: str) -> Built:
    suffix = _suffix(text, m.end(), ("pro", "ultra", "lite"))
    return Built(f"xiaomi {m['num']}{m['t'] or ''}{suffix}")


def _moto_g(m: re.Match[str], text: str) -> Built:
    suffix = _suffix(text, m.end(), ("power", "play", "stylus", "plus", "fast", "pure", "neo"))
    return Built(f"moto g{m['num']}{suffix}{_net(text, m.end())}")


def _moto_edge(m: re.Match[str], text: str) -> Built:
    if not m["num"]:
        return _amb("Motorola Edge generation not stated")
    suffix = _suffix(text, m.end(), ("pro", "ultra", "fusion", "neo", "plus"))
    return Built(f"motorola edge {m['num']}{suffix}")


def _razr(m: re.Match[str], text: str) -> Built:
    if not m["num"]:
        return _amb("Motorola razr generation not stated")
    plus = "+" if m["plus"] else ""
    suffix = "" if plus else _suffix(text, m.end(), ("ultra", "plus"))
    return Built(f"motorola razr {m['num']}{plus}{suffix}")


def _pixel(m: re.Match[str], text: str) -> Built:
    a = "a" if m["a"] else ""
    pos = m.end()
    if not a and next_tokens(text, pos, 1) == ["a"]:
        a = "a"
        pos = text.index(" a", pos) + 2
    suffix = "" if a else _suffix(text, pos, ("pro xl", "pro fold", "pro", "xl", "fold"))
    return Built(f"pixel {m['num']}{a}{suffix}")


def _huawei(m: re.Match[str], text: str) -> Built:
    line = m["line"]
    base = f"p{m['num']}" if line == "p" else f"{line} {m['num']}"
    suffix = _suffix(text, m.end(), ("pro+", "pro", "plus", "lite", "se", "ultra", "rs"))
    return Built(f"huawei {base}{m['i'] or ''}{suffix}{_net(text, m.end())}")


def _honor(m: re.Match[str], text: str) -> Built:
    if m["mnum"]:
        base = f"magic {m['mnum']}"
    elif m["xnum"]:
        base = f"x{m['xnum']}{m['xs'] or ''}"
    else:
        base = m["num"]
    suffix = _suffix(text, m.end(), ("pro", "lite", "plus", "ultra"))
    return Built(f"honor {base}{suffix}{_net(text, m.end())}")


def _oppo(m: re.Match[str], text: str) -> Built:
    if m["rnum"]:
        base = f"reno {m['rnum']}{m['rf'] or ''}"
    elif m["anum"]:
        base = f"a{m['anum']}{m['as'] or ''}"
    else:
        base = f"find x{m['fnum']}"
    suffix = _suffix(text, m.end(), ("pro+", "pro", "plus", "ultra"))
    return Built(f"oppo {base}{suffix}{_net(text, m.end())}")


def _realme(m: re.Match[str], text: str) -> Built:
    if m["num"]:
        base = f"{m['num']}{m['suf'] or ''}"
    elif m["cnum"]:
        base = f"c{m['cnum']}"
    elif m["gnum"]:
        base = f"gt {m['gnum']}"
    else:
        base = f"note {m['nnum']}"
    suffix = _suffix(text, m.end(), ("pro+", "pro plus", "pro"))
    if suffix == " pro plus":
        suffix = " pro+"
    return Built(f"realme {base}{suffix}{_net(text, m.end())}")


def _oneplus(m: re.Match[str], text: str) -> Built:
    if m["nnum"]:
        ce = " ce" if " ce" in m.group(0) else ""
        lite = _suffix(text, m.end(), ("lite",))  # Nord CE4 Lite is a different (cheaper) phone
        return Built(f"oneplus nord{ce} {m['nnum']}{lite}{_net(text, m.end())}")
    suffix = _suffix(text, m.end(), ("pro",))
    return Built(f"oneplus {m['num']}{m['t'] or ''}{suffix}")


# --- Consoles & handheld PCs ---------------------------------------------------------------------


def _pro_after(text: str, pos: int) -> bool:
    """'pro' among the two words after ``pos`` and not negated ("PS5 no pro")."""
    return affirmed(" ".join(next_tokens(text, pos, 2)), "pro")


def _ps5(m: re.Match[str], text: str) -> Built:
    if _pro_after(text, m.end()) or has(text, r"cfi\s?7\d{0,3}\w?"):
        return Built("ps5 pro")
    if affirmed(text, "slim") or has(text, r"cfi\s?2\d{0,3}\w?"):
        return Built("ps5 slim")
    return Built("ps5")


def _ps4(m: re.Match[str], text: str) -> Built:
    if _pro_after(text, m.end()):
        return Built("ps4 pro")
    if affirmed(text, "slim"):
        return Built("ps4 slim")
    return Built("ps4")


def _const(model: str | None, note: str | None = None) -> Builder:
    def build(_m: re.Match[str], _text: str) -> Built:
        return Built(model, (), note)

    return build


def _dualsense(m: re.Match[str], _text: str) -> Built:
    return Built("dualsense edge" if m["edge"] else "dualsense")


def _xbox_series(m: re.Match[str], _text: str) -> Built:
    return Built(f"xbox series {m['v']}")


def _xbox_one(m: re.Match[str], _text: str) -> Built:
    return Built(f"xbox one {m['v']}" if m["v"] else "xbox one")


_SWITCH_QUANTITY_NOUNS = re.compile(r"juegos?|controles|controls|joycons?|mandos|piezas|unidades|accesorios")


def _switch(m: re.Match[str], text: str) -> Built:
    after = [t for t in next_tokens(text, m.end(), 4) if t not in ("modelo", "version", "edicion", "model")]
    two = bool(after[:1] == ["2"] and not (len(after) > 1 and _SWITCH_QUANTITY_NOUNS.fullmatch(after[1])))
    oled = affirmed(text, "oled")  # "(no es OLED)" / "no lite" say the unit is NOT that model
    lite = affirmed(text, "lite")
    if sum((two, oled, lite)) > 1:
        return _amb("conflicting Nintendo Switch models in title")
    if two:
        return Built("switch 2")
    if oled:
        return Built("switch oled")
    if lite:
        return Built("switch lite")
    return Built("switch")


def _steam_deck(_m: re.Match[str], text: str) -> Built:
    oled = affirmed(text, "oled")
    lcd = affirmed(text, "lcd")
    if oled and lcd:
        return _amb("Steam Deck: both LCD and OLED mentioned")
    if oled:
        return Built("steam deck oled")
    if lcd:
        return Built("steam deck lcd")
    return Built("steam deck")  # refined by storage in refine_model()


def _rog_ally(m: re.Match[str], text: str) -> Built:
    if m["x"]:
        return Built("rog ally x")
    if has(text, r"z1\sextreme|z1e"):
        return Built("rog ally z1 extreme")
    if has(text, "z1"):
        return Built("rog ally z1")
    return _amb("ROG Ally chip (Z1 / Z1 Extreme) not stated")


def _legion_go(m: re.Match[str], _text: str) -> Built:
    return Built(f"legion go {m['v']}" if m["v"] else "legion go")


# --- GPUs ----------------------------------------------------------------------------------------

_PC_CONTEXT = (
    r"laptop|notebook|portatil|computadora|pc\sgamer|cpu|gabinete|ryzen\s[3579]|core\si[3579]|"
    r"i[3579]\s\d{4,5}\w*|desktop|torre|all\sin\sone|equipo\sgamer"
)


def _rtx(m: re.Match[str], text: str) -> Built:
    if has(text, _PC_CONTEXT):
        return _amb("GPU named inside a laptop/PC listing (the listing is not a graphics card)")
    suffix = _suffix(text, m.end(), ("ti super", "ti", "super"))
    return Built(f"rtx {m['num']}{suffix}")


def _radeon(m: re.Match[str], text: str) -> Built:
    if has(text, _PC_CONTEXT):
        return _amb("GPU named inside a laptop/PC listing (the listing is not a graphics card)")
    suffix = _suffix(text, m.end(), ("xtx", "xt", "gre"))
    return Built(f"radeon rx {m['num']}{suffix}")


# --- Cameras, drones, audio, wearables, e-readers -------------------------------------------------


def _gopro(m: re.Match[str], text: str) -> Built:
    suffix = _suffix(text, m.end(), ("mini", "creator"))
    if suffix == " creator":
        suffix = " creator edition"
    return Built(f"gopro hero {int(m['num'])}{suffix}")


def _gopro_max(m: re.Match[str], text: str) -> Built:
    nxt = next_tokens(text, m.end(), 1)
    return Built("gopro max 2" if nxt == ["2"] else "gopro max")


def _dji(m: re.Match[str], text: str) -> Built:
    line = m["line"]
    num = m["num"]
    if line in ("neo", "flip"):
        return Built(f"dji {line}")
    if not num:
        return _amb(f"DJI {line} generation not stated")
    suffix = ""
    if line == "mini":
        suffix = _suffix(text, m.end(), ("pro", "se"))
    elif line == "mavic":
        suffix = _suffix(text, m.end(), ("pro", "classic", "cine"))
    elif line == "osmo action":
        suffix = _suffix(text, m.end(), ("pro",))
    return Built(f"dji {line} {num}{suffix}")


def _kindle(m: re.Match[str], text: str) -> Built:
    line = m["line"]
    toks = next_tokens(text, m.end(), 6)
    gen = find_generation(toks)
    direct = int(toks[0]) if toks and toks[0].isdigit() and 1 <= int(toks[0]) <= 12 else None
    years = [t for t in toks if re.fullmatch(r"20[12]\d", t)]
    signature = " signature" if has(text, "signature") else ""
    if line == "colorsoft":
        return Built(f"kindle colorsoft{signature}")
    if line == "paperwhite":
        gen = gen or direct or {"2021": 11, "2024": 12}.get(years[0] if years else "")
        if gen:
            return Built(f"kindle paperwhite {gen}{signature}")
        return _amb("Kindle Paperwhite generation not stated")
    if line == "scribe":
        gen = gen or {"2022": 1, "2024": 2}.get(years[0] if years else "")
        if gen:
            return Built(f"kindle scribe {gen}")
        return _amb("Kindle Scribe generation not stated")
    if line == "oasis":
        if gen:
            return Built(f"kindle oasis {gen}")
        return _amb("Kindle Oasis generation not stated")
    if gen:
        return Built(f"kindle {gen}")
    return _amb("Kindle model/generation not stated")


def _echo(m: re.Match[str], text: str) -> Built:
    line = m["line"]
    gen = find_generation(next_tokens(text, m.end(), 5))
    num = m["num"]
    if line == "show" and num:
        return Built(f"echo show {num}")
    if gen or (num and line == "dot"):
        return Built(f"echo {line} {gen or int(num)}")
    if line in ("pop", "studio", "spot"):
        return Built(f"echo {line}")
    return _amb(f"Echo {line} generation not stated")


def _sony_wh(m: re.Match[str], _text: str) -> Built:
    return Built(f"{m['t']}-1000xm{m['n']}")


def _bose(m: re.Match[str], text: str) -> Built:
    rest = m["rest"]
    if not rest:
        return _amb("Bose QuietComfort model not stated")
    nxt = next_tokens(text, m.end(), 2)
    if rest == "ultra":
        return Built("bose quietcomfort ultra" + (" earbuds" if nxt[:1] == ["earbuds"] else ""))
    if rest == "earbuds":
        return Built("bose quietcomfort earbuds" + (" ii" if nxt[:1] in (["ii"], ["2"]) else ""))
    if rest == "35" and nxt[:1] in (["ii"], ["2"]):
        return Built("bose quietcomfort 35 ii")
    return Built(f"bose quietcomfort {rest}")


def _jbl(m: re.Match[str], text: str) -> Built:
    if not m["num"]:
        return _amb(f"JBL {m['line']} generation not stated")
    suffix = _suffix(text, m.end(), ("essential", "pro", "plus"))
    return Built(f"jbl {m['line']} {m['num']}{suffix}")


def _beats(m: re.Match[str], _text: str) -> Built:
    rest = re.sub(r"(solo|studio)\s?(\d)", r"\1 \2", m["rest"])
    return Built(f"beats {rest}" if not rest.startswith("powerbeats") else rest)


def _garmin(m: re.Match[str], text: str) -> Built:
    if m["line"] in ("instinct", "venu") and not has(text, "garmin"):
        return _amb("generic word without Garmin brand")
    if not m["num"]:
        return _amb(f"Garmin {m['line']} model number not stated")
    suffix = _suffix(text, m.end(), ("pro", "plus", "solar", "sq", "crossover", "amoled"))
    return Built(f"garmin {m['line']} {m['num']}{suffix}")


def _canon(m: re.Match[str], _text: str) -> Built:
    ii = " ii" if m["ii"] else ""
    if m["rnum"]:
        return Built(f"canon eos r{m['rnum']}{ii}")
    if m["reb"]:
        return Built(f"canon eos rebel {m['reb']}")
    return Built(f"canon eos {m['dnum']}{ii}")


def _nikon(m: re.Match[str], _text: str) -> Built:
    gen = f" {m['ii']}" if m["ii"] else ""
    if m["z"]:
        return Built(f"nikon z{m['z']}{gen}")
    return Built(f"nikon d{m['d']}")


def _fuji(m: re.Match[str], _text: str) -> Built:
    return Built(f"fujifilm x{m['m']}")


def _instax(m: re.Match[str], _text: str) -> Built:
    if m["mini"]:
        return Built(f"instax mini {m['mini']}")
    if m["wide"]:
        return Built(f"instax wide {m['wide']}")
    return Built(f"instax square {m['sq']}")


def _sony_alpha(m: re.Match[str], _text: str) -> Built:
    gen = f" {m['gen']}" if m["gen"] else ""
    return Built(f"sony a{m['num']}{gen}")


def _sony_zv(m: re.Match[str], _text: str) -> Built:
    return Built(f"sony zv-{m['m']}" + (" ii" if m["ii"] else ""))


# --- Tools, toys, sneakers (exact SKU / set / style codes) ---------------------------------------


def _code_if_brand(brand_word: str, fmt: Callable[[re.Match[str]], str]) -> Builder:
    def build(m: re.Match[str], text: str) -> Built | None:
        if not has(text, brand_word):
            return None  # code-like token without the brand: not this family
        return Built(fmt(m))

    return build


def _lego(m: re.Match[str], text: str) -> Built:
    for num in re.finditer(r"(?<![\w.])(\d{4,6})(?![\w.])(?!\s?(?:piezas|pzs|pz|pcs|pieces|pza))", text):
        value = num.group(1)
        if len(value) == 4 and 2000 <= int(value) <= 2035:
            continue  # a year, not a set number
        return Built(f"lego {value}")
    return _amb("LEGO set number not stated")


# ------------------------------------------------------------------------------------------------
# Rule table.  Order matters only for hits that start at the same position.
# ------------------------------------------------------------------------------------------------

_SAMSUNG = r"(?:\bgalaxy\s|\bsamsung\s(?:galaxy\s)?)"


def _r(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


MODEL_RULES: tuple[ModelRule, ...] = (
    # Apple
    ModelRule("iphone", "apple", "phone", _r(r"\biphone\s?(?P<num>1[1-7]|xs|xr|x|se|air)(?P<e>e)?(?!\w)"), _iphone),
    ModelRule("airpods", "apple", "audio", _r(r"\bairpods(?:\s(?P<line>pro|max))?(?!\w)"), _airpods),
    ModelRule("apple_watch", "apple", "smartwatch", _r(r"\b(?:apple\swatch|iwatch)(?!\w)"), _apple_watch),
    ModelRule("ipad", "apple", "tablet", _r(r"\bipad(?:\s(?P<line>air|pro|mini))?(?!\w)"), _ipad),
    ModelRule("macbook", "apple", "laptop", _r(r"\bmacbook(?:\s(?P<line>air|pro))?(?!\w)"), _macbook),
    ModelRule(
        "apple_pencil", "apple", "tablet", _r(r"\bapple\spencil(?!\w)"), _apple_pencil, accessory_type="stylus"
    ),
    # Samsung
    ModelRule("galaxy_s", "samsung", "phone", _r(_SAMSUNG + r"s\s?(?P<num>[12]\d)(?P<plus>\+)?(?!\w)"), _galaxy_s),
    ModelRule(
        "galaxy_am", "samsung", "phone", _r(_SAMSUNG + r"(?P<line>[am])\s?(?P<num>\d{2})(?P<s>s)?(?!\w)"), _galaxy_am
    ),
    ModelRule(
        "galaxy_z",
        "samsung",
        "phone",
        _r(r"(?:\bgalaxy\s(?:z\s)?|\bsamsung\s(?:galaxy\s)?(?:z\s)?|\bz\s)(?P<kind>fold|flip)\s?(?P<num>\d)(?!\w)"),
        _galaxy_z,
    ),
    ModelRule(
        "galaxy_tab",
        "samsung",
        "tablet",
        _r(_SAMSUNG + r"tab\s(?P<line>s|a)\s?(?P<num>\d{1,2})(?P<plus>\+)?(?!\w)"),
        _galaxy_tab,
    ),
    ModelRule("galaxy_watch", "samsung", "smartwatch", _r(_SAMSUNG + r"watch\s?(?P<num>\d)?(?!\w)"), _galaxy_watch),
    ModelRule("galaxy_buds", "samsung", "audio", _r(_SAMSUNG + r"buds\s?(?P<num>\d)?(?P<plus>\+)?(?!\w)"), _galaxy_buds),
    # Xiaomi
    ModelRule("redmi_note", "xiaomi", "phone", _r(r"\bredmi\snote\s?(?P<num>\d{1,2})(?P<s>s|t)?(?!\w)"), _redmi_note),
    ModelRule("redmi", "xiaomi", "phone", _r(r"\bredmi\s(?!note)(?P<num>a?\d{1,2}c?)(?!\w)"), _redmi),
    ModelRule("poco", "xiaomi", "phone", _r(r"\bpoco\s(?P<line>[xfmc])\s?(?P<num>\d)(?!\w)"), _poco),
    ModelRule("xiaomi_num", "xiaomi", "phone", _r(r"\bxiaomi\s(?:mi\s)?(?P<num>1[0-7])(?P<t>t)?(?!\w)"), _xiaomi_num),
    # Motorola
    ModelRule(
        "moto_g", "motorola", "phone", _r(r"\b(?:motorola\s(?:moto\s)?|moto\s?)g\s?(?P<num>\d{1,3})(?!\w)"), _moto_g
    ),
    ModelRule(
        "moto_edge",
        "motorola",
        "phone",
        _r(r"\b(?:motorola\s|moto\s)(?:moto\s)?edge(?:\s(?P<num>\d{2,4}))?(?!\w)"),
        _moto_edge,
    ),
    ModelRule(
        "razr", "motorola", "phone", _r(r"\b(?:motorola\s|moto\s)?razr(?:\s?(?P<num>\d{2}))?(?P<plus>\+)?(?!\w)"), _razr
    ),
    # Google / others
    ModelRule("pixel", "google", "phone", _r(r"\bpixel\s?(?P<num>\d{1,2})(?P<a>a)?(?!\w)"), _pixel),
    ModelRule(
        "huawei",
        "huawei",
        "phone",
        _r(r"\bhuawei\s(?P<line>nova|mate|pura|p)\s?(?P<num>\d{1,2})(?P<i>i|e|s|x)?(?!\w)"),
        _huawei,
    ),
    ModelRule(
        "honor",
        "honor",
        "phone",
        _r(r"\bhonor\s(?:magic\s?(?P<mnum>\d)|x\s?(?P<xnum>\d{1,2})(?P<xs>[a-z])?|(?P<num>\d{2,3}))(?!\w)"),
        _honor,
    ),
    ModelRule(
        "oppo",
        "oppo",
        "phone",
        _r(
            r"\boppo\s(?:reno\s?(?P<rnum>\d{1,2})(?P<rf>[fz])?|a\s?(?P<anum>\d{1,2})(?P<as>[a-z])?"
            r"|find\sx\s?(?P<fnum>\d))(?!\w)"
        ),
        _oppo,
    ),
    ModelRule(
        "realme",
        "realme",
        "phone",
        _r(
            r"\brealme\s(?:(?P<num>\d{1,2})(?P<suf>i|t|x|s)?|c\s?(?P<cnum>\d{2})|gt\s?(?P<gnum>\d)"
            r"|note\s(?P<nnum>\d{2}))(?!\w)"
        ),
        _realme,
    ),
    ModelRule(
        "oneplus",
        "oneplus",
        "phone",
        _r(r"\boneplus\s(?:nord\s(?:ce\s?)?(?P<nnum>\d)|(?P<num>\d{1,2})(?P<t>[tr])?)(?!\w)"),
        _oneplus,
    ),
    # Consoles
    ModelRule("ps5", "sony", "console", _r(r"\bps5(?!\w)"), _ps5),
    ModelRule("ps4", "sony", "console", _r(r"\bps4(?!\w)"), _ps4),
    ModelRule(
        "ps_portal", "sony", "console", _r(r"\b(?:playstation|ps)\s?portal(?!\w)"), _const("playstation portal")
    ),
    ModelRule(
        "dualsense",
        "sony",
        "console",
        _r(r"\bdualsense(?:\s(?P<edge>edge))?(?!\w)"),
        _dualsense,
        accessory_type="controller",
    ),
    ModelRule(
        "dualshock", "sony", "console", _r(r"\bdualshock\s?4?(?!\w)"), _const("dualshock 4"), accessory_type="controller"
    ),
    ModelRule("xbox_series", "microsoft", "console", _r(r"\bxbox\sseries\s(?P<v>x|s)(?!\w)"), _xbox_series),
    ModelRule("xbox_one", "microsoft", "console", _r(r"\bxbox\sone(?:\s(?P<v>x|s))?(?!\w)"), _xbox_one),
    ModelRule(
        "switch", "nintendo", "console", _r(r"\bnintendo\sswitch(?!\w)|\bswitch\s(?=oled|lite|2(?!\d))"), _switch
    ),
    ModelRule(
        "joycon",
        "nintendo",
        "console",
        _r(r"\bjoycons?(?!\w)"),
        _const(None, "Joy-Con: single controller vs pair cannot be told apart reliably"),
        accessory_type="controller",
    ),
    ModelRule("steam_deck", "valve", "handheld_pc", _r(r"\bsteam\sdeck(?!\w)"), _steam_deck),
    ModelRule("rog_ally", "asus", "handheld_pc", _r(r"\brog\sally(?:\s(?P<x>x))?(?!\w)"), _rog_ally),
    ModelRule("legion_go", "lenovo", "handheld_pc", _r(r"\blegion\sgo(?:\s(?P<v>s|2))?(?!\w)"), _legion_go),
    # GPUs
    ModelRule("rtx", "nvidia", "gpu", _r(r"\b(?:geforce\s)?rtx\s?(?P<num>[2-5]0[5-9]0)(?!\w)"), _rtx),
    ModelRule("radeon", "amd", "gpu", _r(r"\b(?:radeon\s)?rx\s?(?P<num>[5-9]\d{3})(?!\w)"), _radeon),
    # Cameras / drones
    ModelRule("gopro", "gopro", "camera", _r(r"\bgopro\s(?:hero\s?)?(?P<num>\d{1,2})(?!\w)"), _gopro),
    ModelRule("gopro_max", "gopro", "camera", _r(r"\bgopro\smax(?!\w)"), _gopro_max),
    ModelRule(
        "dji",
        "dji",
        "drone",
        _r(
            r"\bdji\s(?P<line>mini|air|avata|mavic|neo|flip|osmo\spocket|osmo\saction|osmo\smobile|osmo\s360)"
            r"(?:\s?(?P<num>\d{1,2}s?))?(?!\w)"
        ),
        _dji,
    ),
    ModelRule(
        "canon",
        "canon",
        "camera",
        _r(
            r"\bcanon\s(?:eos\s)?(?:r\s?(?P<rnum>\d{1,3}|p)|rebel\s(?P<reb>t\d{1,2}i?|sl\d)|(?P<dnum>\d{2,4}d))"
            r"(?:\s(?P<ii>ii|mark\sii|mark\s2))?(?!\w)"
        ),
        _canon,
    ),
    ModelRule(
        "nikon",
        "nikon",
        "camera",
        _r(r"\bnikon\s(?:z\s?(?P<z>\d{1,2}|fc|f)|d\s?(?P<d>\d{3,4}))(?:\s(?P<ii>iii|ii))?(?!\w)"),
        _nikon,
    ),
    ModelRule(
        "fujifilm",
        "fujifilm",
        "camera",
        _r(r"\bfuji(?:film)?\sx\s?(?P<m>t\d{1,2}|s\d{1,2}|e\d|100vi|100v|100f|h2s|h2|m5)(?!\w)"),
        _fuji,
    ),
    ModelRule(
        "instax",
        "fujifilm",
        "camera",
        _r(r"\binstax\s(?:mini\s(?P<mini>\d{1,2}\+?|evo)|wide\s(?P<wide>\d{3})|square\s(?P<sq>sq\d{1,2}))(?!\w)"),
        _instax,
    ),
    ModelRule(
        "sony_alpha",
        "sony",
        "camera",
        _r(r"\bsony\s(?:alpha\s)?a\s?(?P<num>7c|7r|7s|7|9|1|6[0-7]00)(?:\s?(?P<gen>iv|iii|ii|v))?(?!\w)"),
        _sony_alpha,
    ),
    ModelRule("sony_zv", "sony", "camera", _r(r"\bzv\s?(?P<m>e10|e1|1f|1)(?:\s?(?P<ii>ii))?(?!\w)"), _sony_zv),
    # Audio / wearables / e-readers
    ModelRule("sony_wh", "sony", "audio", _r(r"\b(?P<t>wh|wf)\s?1000\s?xm\s?(?P<n>[2-6])(?!\w)"), _sony_wh),
    ModelRule(
        "bose", "bose", "audio", _r(r"\bbose\s(?:quiet\s?comfort|qc)\s?(?P<rest>45|35|ultra|earbuds|se)?(?!\w)"), _bose
    ),
    ModelRule(
        "jbl",
        "jbl",
        "audio",
        _r(
            r"\bjbl\s(?P<line>flip|charge|go|clip|xtreme|boombox|partybox|tune|live|vibe|wave)"
            r"\s?(?P<num>\d{1,3})?(?:bt|nc)?(?!\w)"
        ),
        _jbl,
    ),
    ModelRule(
        "beats",
        "beats",
        "audio",
        _r(
            r"\b(?:beats\s)?(?P<rest>studio\spro|studio\sbuds\+?|solo\s?(?:4|3|buds)|fit\spro|pill|"
            r"powerbeats\spro(?:\s2)?)(?!\w)"
        ),
        _beats,
    ),
    ModelRule(
        "sennheiser",
        "sennheiser",
        "audio",
        _r(r"\bsennheiser\smomentum\s(?P<num>4|3)(?!\w)"),
        lambda m, _t: Built(f"sennheiser momentum {m['num']}"),
    ),
    ModelRule(
        "garmin",
        "garmin",
        "smartwatch",
        _r(r"\b(?:garmin\s)?(?P<line>forerunner|fenix|venu|instinct|epix|vivoactive)\s?(?P<num>\d{1,3}[sx]?)?(?!\w)"),
        _garmin,
    ),
    ModelRule(
        "kindle",
        "amazon",
        "ereader",
        _r(r"\bkindle(?:\s(?P<line>paperwhite|oasis|scribe|colorsoft|basic))?(?!\w)"),
        _kindle,
    ),
    ModelRule(
        "echo", "amazon", "audio", _r(r"\becho\s(?P<line>dot|pop|show|studio|spot)(?:\s(?P<num>\d{1,2}))?(?!\w)"), _echo
    ),
    # Tools / toys / sneakers
    ModelRule(
        "dewalt",
        "dewalt",
        "tool",
        _r(r"\b(?P<code>dc[a-z]{1,3}\d{3,4}[a-z]{0,2}\d?)(?!\w)"),
        _code_if_brand("dewalt", lambda m: m["code"]),
    ),
    ModelRule(
        "makita",
        "makita",
        "tool",
        _r(r"\b(?P<code>(?:x[a-z]{2,3}\d{2,3}|d[a-z]{2}\d{3}|g[a-z]{2}\d{2,3})[a-z]{0,3}\d?)(?!\w)"),
        _code_if_brand("makita", lambda m: m["code"]),
    ),
    ModelRule(
        "milwaukee",
        "milwaukee",
        "tool",
        _r(r"\b(?P<a>2\d{3})\s(?P<b>2\d)(?!\w)"),
        _code_if_brand("milwaukee", lambda m: f"{m['a']}-{m['b']}"),
    ),
    ModelRule("lego", "lego", "toy", _r(r"\blego(?!\w)"), _lego),
    ModelRule(
        "nike_style",
        "nike",
        "sneakers",
        _r(r"\b(?P<a>[a-z]{2}\d{4})\s(?P<b>\d{3})(?!\w)"),
        _code_if_brand(r"nike|jordan", lambda m: f"{m['a']}-{m['b']}"),
    ),
    ModelRule(
        "adidas_style",
        "adidas",
        "sneakers",
        _r(r"\b(?P<code>[a-z]{2}\d{4})(?!\w)"),
        _code_if_brand(r"adidas|yeezy", lambda m: m["code"]),
    ),
)

#: Rules whose match is an exact product code (SKU / style code / set number).  These codes often come
#: late in the title, so the "unknown words before the model" ambiguity guard does not apply.
EXACT_CODE_RULES: frozenset[str] = frozenset({"dewalt", "makita", "milwaukee", "lego", "nike_style", "adidas_style"})

#: model name -> accessory type, for rules whose recognised model is itself an accessory product.
ACCESSORY_MODELS: dict[str, str] = {
    "dualsense": "controller",
    "dualsense edge": "controller",
    "dualshock 4": "controller",
    "apple pencil 1": "stylus",
    "apple pencil 2": "stylus",
    "apple pencil pro": "stylus",
    "apple pencil usb c": "stylus",
}


#: rule name -> words that turn the family into a DIFFERENT (differently priced) model.  The builders
#: only look right after the model number; when one of these words appears further on ("Galaxy S24 256gb
#: Ultra", "RTX 4070 12gb Super", "iPhone 15 256gb Pro Max") and the built model did not consume it, the
#: title is ambiguous and the hit loses its model (precision first).
SUFFIX_CHANGERS: dict[str, frozenset[str]] = {
    "iphone": frozenset({"pro", "max", "plus", "mini", "pm"}),
    "galaxy_s": frozenset({"ultra", "plus", "fe", "edge"}),
    "galaxy_z": frozenset({"fe"}),
    "galaxy_tab": frozenset({"ultra", "fe", "fe+", "plus", "lite"}),
    "galaxy_watch": frozenset({"classic", "pro", "fe", "ultra"}),
    "galaxy_buds": frozenset({"pro", "fe", "live", "core"}),
    "redmi_note": frozenset({"pro", "plus", "pro+"}),
    "poco": frozenset({"pro", "gt", "ultra", "neo"}),
    "xiaomi_num": frozenset({"pro", "ultra", "lite"}),
    "moto_edge": frozenset({"pro", "ultra", "fusion", "neo", "plus"}),
    "razr": frozenset({"ultra", "plus"}),
    "pixel": frozenset({"pro", "xl", "fold"}),
    "huawei": frozenset({"pro", "pro+", "plus", "lite", "ultra", "rs"}),
    "honor": frozenset({"pro", "lite", "plus", "ultra"}),
    "oppo": frozenset({"pro", "pro+", "plus", "ultra"}),
    "realme": frozenset({"pro", "pro+", "plus"}),
    "oneplus": frozenset({"pro", "lite"}),
    "rtx": frozenset({"ti", "super"}),
    "radeon": frozenset({"xt", "xtx", "gre"}),
}

#: The suffix window ends at a separator: what follows describes another, included item.
_SUFFIX_WINDOW_STOP = frozenset(
    {"+", "con", "y", "e", "mas", "incluye", "incluido", "incluida", "para", "compatible", "for", "with", "and",
     "o", "vs", "regalo"}
)
_SUFFIX_WINDOW = 6
#: Changer words that are also ordinary Spanish adjectives of the word after them.  Only checked when the
#: changer does NOT come right after the model name ("iPhone 13 mini detalle" still reads as the 13 mini;
#: "iPhone 13 128gb mini detalle en pantalla" is a 13 with a tiny defect).  "max" is not listed: "iPhone 15
#: Pro 256gb Max ..." can be a Pro Max written in another order.
_CHANGER_ADJECTIVES: dict[str, frozenset[str]] = {"mini": DEFECT_NOUNS}


def _model_words(model: str) -> set[str]:
    """Words a built model name 'consumes' ('galaxy s24+' also consumes 'plus' and '+')."""
    words: set[str] = set()
    for tok in model.split():
        words.add(tok)
        if tok.endswith("+"):
            words.update({tok[:-1], "plus", "+"})
    if "pro max" in model:
        words.add("pm")
    return words


def _unconsumed_suffix(hit: ModelHit, text: str, window_end: int) -> str | None:
    changers = SUFFIX_CHANGERS.get(hit.rule)
    if not changers or hit.model is None:
        return None
    consumed = _model_words(hit.model)
    matches = list(re.finditer(r"\S+", text[hit.end : window_end]))
    words = [tok.group(0) for tok in matches]
    for count, word in enumerate(words):
        if count >= _SUFFIX_WINDOW or word in _SUFFIX_WINDOW_STOP:
            break
        if word not in changers or word in consumed or is_negated(text, hit.end + matches[count].start()):
            continue
        if count > 0 and word in _CHANGER_ADJECTIVES and count + 1 < len(words) and (
            words[count + 1] in _CHANGER_ADJECTIVES[word]
        ):
            continue  # "iPhone 13 128gb mini detalle": a tiny defect, not the 13 mini
        return word
    return None


def find_model_hits(text: str) -> list[ModelHit]:
    """All non-overlapping model-family hits in normalized ``text``, ordered by position."""
    raw_hits: list[tuple[int, int, ModelHit]] = []
    for order, rule in enumerate(MODEL_RULES):
        for m in rule.pattern.finditer(text):
            built = rule.build(m, text)
            if built is None:
                continue
            raw_hits.append(
                (
                    m.start(),
                    order,
                    ModelHit(
                        rule=rule.name,
                        brand=rule.brand,
                        category=rule.category,
                        model=built.model,
                        start=m.start(),
                        end=m.end(),
                        tokens=built.tokens,
                        accessory_type=rule.accessory_type,
                        notes=(built.note,) if built.note else (),
                    ),
                )
            )
    raw_hits.sort(key=lambda item: (item[0], item[1]))
    hits: list[ModelHit] = []
    last_end = -1
    for _start, _order, hit in raw_hits:
        if hit.start < last_end:
            continue  # overlaps an earlier (or same-position, higher-priority) hit
        hits.append(hit)
        last_end = hit.end
    for i, hit in enumerate(hits):
        window_end = hits[i + 1].start if i + 1 < len(hits) else len(text)
        word = _unconsumed_suffix(hit, text, window_end)
        if word:
            note = f"'{word}' after {hit.model} does not belong to that model name: exact model ambiguous"
            hits[i] = replace(hit, model=None, notes=(*hit.notes, note))
    return hits


def refine_model(model: str | None, storage_gb: int | None) -> tuple[str | None, str | None]:
    """Storage-based disambiguation that the hardware line-up makes unambiguous.

    Returns (model, note).  Only facts about the official configurations are used:
    the original PS5 shipped with 825GB (Slim: 1TB); Steam Deck LCD came in 64/256/512GB and the
    OLED in 512GB/1TB.
    """
    if model == "ps5" and storage_gb == 1024:
        return "ps5 slim", "1TB PS5 inferred as Slim (the original PS5 shipped with 825GB)"
    if model == "steam deck":
        if storage_gb in (64, 256):
            return "steam deck lcd", f"{storage_gb}GB Steam Deck inferred as LCD (OLED starts at 512GB)"
        if storage_gb == 1024:
            return "steam deck oled", "1TB Steam Deck inferred as OLED (LCD tops out at 512GB)"
        return None, "Steam Deck display (LCD/OLED) not stated"
    return model, None


# ------------------------------------------------------------------------------------------------
# Identity dimensions: attributes that make two listings of the same model different products.
# ------------------------------------------------------------------------------------------------

IDENTITY_LABELS: dict[str, str] = {
    "edition": "edition",
    "connectivity": "connectivity",
    "screen": "screen size",
    "case_size": "case size",
    "ram": "RAM",
    "vram": "VRAM",
    "lens": "lens kit",
    "power": "battery kit",
    "size": "size",
    "material": "case material",
}

#: Apple Watch case materials sold as separate, more expensive products than the aluminium case.  An
#: unstated material and "aluminio" both mean "no premium material stated" (no key dimension).
PREMIUM_WATCH_MATERIALS: frozenset[str] = frozenset({"steel", "titanium", "hermes"})


def has_material_dimension(model: str | None) -> bool:
    """Apple Watch Series models come in aluminium and stainless steel/titanium/Hermes cases."""
    return bool(model) and str(model).startswith("apple watch series")


def material_conflict(model: str | None, tokens: list[str] | tuple[str, ...]) -> bool:
    """True when the title states contradictory case materials (aluminium and steel, steel and titanium)."""
    if not has_material_dimension(model):
        return False
    premium = PREMIUM_WATCH_MATERIALS & set(tokens)
    return len(premium) > 1 or (len(premium) == 1 and "aluminum" in tokens)


def requires_screen(model: str | None) -> bool:
    """Lines sold in several screen sizes at different prices: the size must be stated to cluster.

    iPad Pro (every generation), iPad Air M2 and later (11"/13"), MacBook Air M2 and later (13"/15") and
    MacBook Pro with an M-series Pro/Max chip (14"/16").  Base M1/M2 MacBook Pros (13" only), base
    M3/M4 MacBook Pros (14" only) and the M1 MacBook Air (13" only) come in one size.
    """
    if not model:
        return False
    model = str(model)
    return bool(
        model.startswith("ipad pro")
        or re.fullmatch(r"ipad air m[2-9]", model)
        or re.fullmatch(r"macbook air m[2-9]", model)
        or re.fullmatch(r"macbook pro m\d (?:pro|max)", model)
    )


_INCH_TOKEN = re.compile(r"\d{1,2}in")
_MM_TOKEN = re.compile(r"\d{2}mm")
_VRAM_TOKEN = re.compile(r"vram\d{1,2}gb")
_SIZE_TOKEN = re.compile(r"talla\d{2}(?:\.5)?")


def identity_dims(
    category: str | None, model: str | None, tokens: list[str] | tuple[str, ...], ram_gb: int | None = None
) -> dict[str, str]:
    """Map variant tokens to identity dimensions for ``category`` (dimension -> value).

    Only dimensions that change what the product *is* (and therefore its price) are returned, e.g.
    PS5 digital vs disc, iPad Wi-Fi vs cellular, Apple Watch 41mm vs 45mm, MacBook RAM.
    """
    toks = set(tokens)
    dims: dict[str, str] = {}
    if category == "console" and model in EDITION_MODELS:
        if "digital" in toks:
            dims["edition"] = "digital"
        elif "disc" in toks:
            dims["edition"] = "disc"
    if category in ("tablet", "smartwatch"):
        if "cellular" in toks:
            dims["connectivity"] = "cellular"
        elif category == "tablet" and "wifi" in toks:
            dims["connectivity"] = "wifi"
        elif category == "smartwatch" and "gps" in toks:
            dims["connectivity"] = "gps"
    if category in ("tablet", "laptop"):
        sizes = sorted(t for t in toks if _INCH_TOKEN.fullmatch(t))
        if len(sizes) == 1:
            dims["screen"] = sizes[0]
    if category == "smartwatch":
        mms = sorted(t for t in toks if _MM_TOKEN.fullmatch(t))
        if len(mms) == 1:
            dims["case_size"] = mms[0]
        premium = sorted(PREMIUM_WATCH_MATERIALS & toks)
        if has_material_dimension(model) and len(premium) == 1 and "aluminum" not in toks:
            dims["material"] = premium[0]
    if category == "laptop":
        if ram_gb:
            dims["ram"] = f"ram{ram_gb}gb"
        else:
            rams = sorted(t for t in toks if re.fullmatch(r"ram\d{1,3}gb", t))
            if len(rams) == 1:
                dims["ram"] = rams[0]
    if category == "gpu":
        vrams = sorted(t for t in toks if _VRAM_TOKEN.fullmatch(t))
        if len(vrams) == 1:
            dims["vram"] = vrams[0]
    if category == "camera":
        if "body_only" in toks and "kit" not in toks:
            dims["lens"] = "body_only"
        elif "lens_kit" in toks and "body_only" not in toks:
            dims["lens"] = "lens_kit"
    if category == "tool":
        if "tool_only" in toks and "with_battery" not in toks:
            dims["power"] = "tool_only"
        elif "with_battery" in toks and "tool_only" not in toks:
            dims["power"] = "with_battery"
    if category == "sneakers":
        sizes = sorted(t for t in toks if _SIZE_TOKEN.fullmatch(t))
        if len(sizes) == 1:
            dims["size"] = sizes[0]
    return dims
