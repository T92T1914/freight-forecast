"""Thin CSS/SVG adapter for the pinned Clair and Obscur role subset."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACES = (
    (400, "normal", "Inter Regular", "Inter-Regular"),
    (400, "italic", "Inter Italic", "Inter-Italic"),
    (600, "normal", "Inter SemiBold", "Inter-SemiBold"),
    (600, "italic", "Inter SemiBold Italic", "Inter-SemiBoldItalic"),
    (700, "normal", "Inter Bold", "Inter-Bold"),
    (700, "italic", "Inter Bold Italic", "Inter-BoldItalic"),
)
FONT_STACK = '"Freight Inter", Inter, system-ui, "Segoe UI", sans-serif'


def load_tokens():
    tokens = json.loads((ROOT / "presentation/tokens.json").read_text())
    for theme in tokens["themes"].values():
        if not all(re.fullmatch(r"#[0-9a-f]{6}", value) for value in theme.values()):
            raise ValueError("Appearance roles must be explicit RGB colors")
    return tokens


def font_css():
    return "\n".join(
        '@font-face{font-family:"Freight Inter";'
        f'src:local("{full}"),local("{postscript}");'
        f"font-weight:{weight};font-style:{style};font-display:swap}}"
        for weight, style, full, postscript in FACES
    )


def declarations(tokens, appearance):
    roles = tokens["themes"][appearance]
    colors = "".join(f"--{name}:{color};" for name, color in roles.items())
    colors += "".join(
        f"--series-{name}:{series[appearance]};"
        for name, series in tokens["series"].items()
    )
    return f"color-scheme:{'light' if appearance == 'clair' else 'dark'};" + colors


def appearance_css(tokens):
    """CSS media queries remain the source of Auto, including without JavaScript."""
    light, dark = (declarations(tokens, mode) for mode in ("clair", "obscur"))
    return (
        font_css()
        + f"\n:root{{{light}}}\n"
        + '@media(prefers-color-scheme:dark){:root:not([data-appearance="clair"])'
        + f"{{{dark}}}}}\n"
        + f':root[data-appearance="obscur"]{{{dark}}}\n'
        + f':root[data-appearance="clair"]{{{light}}}\n'
        + f"@media print{{:root,:root[data-appearance]{{{light}}}}}\n"
    )


def appearance_control():
    return (
        '<div class="appearance-control"><label for="appearance">Appearance</label>'
        '<select id="appearance" disabled><option value="auto">Auto</option>'
        '<option value="clair">Clair</option><option value="obscur">Obscur</option>'
        "</select><noscript><small>Auto follows your system appearance.</small>"
        "</noscript></div>"
    )
