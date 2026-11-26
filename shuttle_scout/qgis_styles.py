"""QGIS .qml style sidecars.

QGIS auto-applies a style file named ``<layer basename>.qml`` sitting next
to the data file, so writing these alongside each output layer gives a
sane, consistent legend on first load with zero clicking. (The .qml files
are styles, not data — don't drag them into QGIS themselves.)

Scheme: every ranked pair gets its own color, sampled uniformly from the
Turbo colormap, HOTTER = BETTER (rank #1 red -> last rank blue/purple),
shared by its start marker (circle), end marker (square), and routes — so
you trace a pair by color. A pair's primary route draws solid, its
alternate route dashed. Hovering a point or route shows a map tip
"popup" with elevation, drop, grade, score, and shuttle time (enable
View > Show Map Tips in QGIS). Roads render as a dark-gray overlay,
hazards translucent red/orange.

The XML uses the classic ``<prop k= v=>`` symbol encoding, which every
QGIS 3.x release reads.
"""

from __future__ import annotations

from pathlib import Path

ZONE_TRAIL_RGB = "25,25,30"  # dark core; a light casing is drawn
                             # under it so it reads on any basemap
MARKER_SIZE_MM = 12.0  # start/end markers; sized to read on satellite imagery

# ---------------------------------------------------------------------------
# Turbo colormap (Google), 5th-order polynomial approximation.
# ---------------------------------------------------------------------------

_TURBO_COEFFS = {
    "r": (0.13572138, 4.61539260, -42.66032258, 132.13108234,
          -152.94239396, 59.28637943),
    "g": (0.09140261, 2.19418839, 4.84296658, -14.18503333,
          4.27729857, 2.82956604),
    "b": (0.10667330, 12.64194608, -60.58204836, 110.36276771,
          -89.90310912, 27.34824973),
}


def turbo_rgb(t: float) -> tuple[int, int, int]:
    """Turbo colormap sample at t in [0, 1] as 8-bit RGB."""
    t = min(max(t, 0.0), 1.0)
    out = []
    for ch in ("r", "g", "b"):
        c = _TURBO_COEFFS[ch]
        v = c[0] + t * (c[1] + t * (c[2] + t * (c[3] + t * (c[4] + t * c[5]))))
        out.append(int(round(255 * min(max(v, 0.0), 1.0))))
    return tuple(out)


def rank_colors(n: int) -> list[tuple[int, int, int]]:
    """One Turbo sample per rank, HOTTER = BETTER: rank #1 sits at the hot
    (red) end, the last rank at the cool (blue/purple) end, spread over the
    useful middle of the map (the extremes are near-black and read poorly
    on imagery). Index i corresponds to rank i+1."""
    if n <= 0:
        return []
    if n == 1:
        return [turbo_rgb(0.90)]
    lo, hi = 0.05, 0.95
    return [turbo_rgb(hi - (hi - lo) * i / (n - 1)) for i in range(n)]


# ---------------------------------------------------------------------------
# QML templates
# ---------------------------------------------------------------------------

_VECTOR_QML = """<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology|Labeling|MapTips" version="3.28.0" labelsEnabled="{labels_on}">
{renderer}
{maptip}
{labeling}
</qgis>
"""

_CATEGORIZED = """  <renderer-v2 type="categorizedSymbol" attr="rank" forceraster="0" enableorderby="0" symbollevels="0">
    <categories>
{categories}
    </categories>
    <symbols>
{symbols}
    </symbols>
  </renderer-v2>"""

_RULE_BASED = """  <renderer-v2 type="RuleRenderer" forceraster="0" enableorderby="0" symbollevels="0">
    <rules key="{{rules}}">
{rules}
    </rules>
    <symbols>
{symbols}
    </symbols>
  </renderer-v2>"""

_LABELING = """  <labeling type="simple">
    <settings calloutType="simple">
      <text-style fieldName="{label_expr}" isExpression="1" fontSize="10"
                  textColor="255,255,255,255" fontSizeUnit="Point"
                  multilineHeight="1">
        <text-buffer bufferDraw="1" bufferSize="1.2" bufferSizeUnits="MM"
                     bufferColor="0,0,0,255"/>
      </text-style>
    </settings>
  </labeling>"""

_MARKER_SYMBOL = """      <symbol type="marker" name="{name}" alpha="1" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleMarker" enabled="1" locked="0" pass="0">
          <prop k="color" v="{rgb},255"/>
          <prop k="name" v="{shape}"/>
          <prop k="outline_color" v="255,255,255,255"/>
          <prop k="outline_style" v="solid"/>
          <prop k="outline_width" v="1.2"/>
          <prop k="outline_width_unit" v="MM"/>
          <prop k="size" v="{size}"/>
          <prop k="size_unit" v="MM"/>
        </layer>
      </symbol>"""

_LINE_SYMBOL = """      <symbol type="line" name="{name}" alpha="1" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleLine" enabled="1" locked="0" pass="0">
          <prop k="line_color" v="{rgb},255"/>
          <prop k="line_style" v="{style}"/>
          <prop k="line_width" v="{width}"/>
          <prop k="line_width_unit" v="MM"/>
          <prop k="capstyle" v="round"/>
          <prop k="joinstyle" v="round"/>
        </layer>
      </symbol>"""

_CASED_LINE_SYMBOL = """      <symbol type="line" name="{name}" alpha="1" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleLine" enabled="1" locked="0" pass="0">
          <prop k="line_color" v="255,255,255,200"/>
          <prop k="line_style" v="solid"/>
          <prop k="line_width" v="{casing}"/>
          <prop k="line_width_unit" v="MM"/>
          <prop k="capstyle" v="round"/>
          <prop k="joinstyle" v="round"/>
        </layer>
        <layer class="SimpleLine" enabled="1" locked="0" pass="0">
          <prop k="line_color" v="{rgb},255"/>
          <prop k="line_style" v="{style}"/>
          <prop k="line_width" v="{width}"/>
          <prop k="line_width_unit" v="MM"/>
          <prop k="capstyle" v="round"/>
          <prop k="joinstyle" v="round"/>
        </layer>
      </symbol>"""

_PALETTED_QML = """<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology" version="3.28.0">
  <pipe>
    <rasterrenderer type="paletted" band="1" opacity="{opacity}" nodataColor="">
      <rasterTransparency/>
      <colorPalette>
{entries}
      </colorPalette>
    </rasterrenderer>
    <brightnesscontrast brightness="0" contrast="0"/>
    <rasterresampler maxOversampling="2"/>
  </pipe>
</qgis>
"""


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _maptip(html: str) -> str:
    return f"  <mapTip enabled=\"1\">{_xml_escape(html)}</mapTip>"


def _point_maptip(which: str) -> str:
    """Hover popup for start/end points; ``which`` is 'start' or 'end'."""
    return _maptip(
        f"<b>[% \"ranks_label\" %] {which}</b><br/>"
        f"Elevation: [% format_number(\"{which}_elev_ft\", 0) %] ft<br/>"
        "Best pair drop: [% format_number(\"drop_ft\", 0) %] ft @ "
        "[% \"avg_grade_pct\" %]% avg<br/>"
        f"Road: [% format_number(\"{which}_road_dist_ft\", 0) %] ft away<br/>"
        "Score: [% \"score\" %] · Shuttle [% \"drive_time_min\" %] min"
    )


_ROUTE_MAPTIP = (
    "<b>#[% \"rank\" %] [% \"kind\" %]</b><br/>"
    "Drop: [% format_number(\"drop_ft\", 0) %] ft @ "
    "[% \"avg_grade_pct\" %]% avg<br/>"
    "Length: [% \"route_len_mi\" %] mi · Climb "
    "[% format_number(\"route_climb_ft\", 0) %] ft<br/>"
    "Elev: [% format_number(\"start_elev_ft\", 0) %] → "
    "[% format_number(\"end_elev_ft\", 0) %] ft<br/>"
    "Shuttle: [% \"drive_time_min\" %] min · Score [% \"score\" %]"
)


def _rank_label(rank: int, scores: list[float] | None) -> str:
    label = f"#{rank}"
    if scores is not None and rank <= len(scores):
        label += f" · score {scores[rank - 1]:.3f}"
    return label


def _points_qml(shape: str, which: str, n_ranks: int,
                scores: list[float] | None) -> str:
    colors = rank_colors(n_ranks)
    cats, syms = [], []
    for i, (r, g, b) in enumerate(colors):
        cats.append(
            f'      <category render="true" symbol="{i}" value="{i + 1}" '
            f'label="{_rank_label(i + 1, scores)}"/>'
        )
        syms.append(_MARKER_SYMBOL.format(
            name=i, rgb=f"{r},{g},{b}", shape=shape, size=MARKER_SIZE_MM))
    label_expr = _xml_escape(
        f"\"ranks_label\" || ' · ' || format_number(\"{which}_elev_ft\", 0) "
        f"|| ' ft\\n' || format_number(\"{which}_road_dist_ft\", 0) "
        "|| ' ft to rd'"
    )
    return _VECTOR_QML.format(
        labels_on="1",
        renderer=_CATEGORIZED.format(categories="\n".join(cats),
                                     symbols="\n".join(syms)),
        maptip=_point_maptip(which),
        labeling=_LABELING.format(label_expr=label_expr),
    )


def _routes_qml(n_ranks: int, scores: list[float] | None) -> str:
    """Rule-based: per rank, primary route (kind=route_1) solid, alternate
    (route_2) dashed — same Turbo color."""
    colors = rank_colors(n_ranks)
    rules, syms = [], []
    sym_i = 0
    for i, (r, g, b) in enumerate(colors):
        rank = i + 1
        for kind, style, width, suffix in (
            ("route_1", "solid", "1.1", ""),
            ("route_2", "dash", "0.9", " (alt)"),
        ):
            flt = _xml_escape(f'"rank" = {rank} AND "kind" = \'{kind}\'')
            rules.append(
                f'      <rule key="r{sym_i}" symbol="{sym_i}" filter="{flt}" '
                f'label="{_rank_label(rank, scores)}{suffix}"/>'
            )
            syms.append(_LINE_SYMBOL.format(
                name=sym_i, rgb=f"{r},{g},{b}", style=style, width=width))
            sym_i += 1
    return _VECTOR_QML.format(
        labels_on="0",
        renderer=_RULE_BASED.format(rules="\n".join(rules),
                                    symbols="\n".join(syms)),
        maptip=_maptip(_ROUTE_MAPTIP),
        labeling="",
    )


_ELEVATION_QML = """<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology" version="3.28.0">
  <pipe>
    <rasterrenderer type="singlebandpseudocolor" band="1" opacity="0.55"
                    classificationMin="{vmin:.0f}" classificationMax="{vmax:.0f}">
      <rastershader>
        <colorrampshader classificationMode="1" clip="0" colorRampType="INTERPOLATED">
{items}
        </colorrampshader>
      </rastershader>
    </rasterrenderer>
    <brightnesscontrast brightness="0" contrast="0"/>
  </pipe>
</qgis>
"""


def write_elevation_sidecar(path: Path, vmin: float = 0.0,
                            vmax: float = 11000.0) -> None:
    """Turbo elevation ramp for elevation_ft.tif, normalized to this scene's
    min/max feet so the full color range is used everywhere."""
    if vmax - vmin < 1.0:
        vmax = vmin + 1.0
    n = 12
    items = []
    for i in range(n + 1):
        v = vmin + (vmax - vmin) * i / n
        r, g, b = turbo_rgb(i / n)
        items.append(
            f'          <item value="{v:.1f}" color="#{r:02x}{g:02x}{b:02x}" '
            f'label="{v:.0f} ft" alpha="255"/>'
        )
    path.write_text(_ELEVATION_QML.format(vmin=vmin, vmax=vmax,
                                          items="\n".join(items)))


_DRIVE_MAPTIP = (
    "<b>#[% \"rank\" %] shuttle drive</b><br/>"
    "Time: [% \"drive_time_min\" %] min<br/>"
    "Pickup: [% format_number(\"end_elev_ft\", 0) %] ft &rarr; drop-off "
    "[% format_number(\"start_elev_ft\", 0) %] ft"
)


def _drive_qml(n_ranks: int, scores: list[float] | None) -> str:
    """Shuttle drive lines: same per-rank Turbo color as the descent, but
    heavy and dashed so the drive reads as road, not trail."""
    colors = rank_colors(n_ranks)
    cats, syms = [], []
    for i, (r, g, b) in enumerate(colors):
        cats.append(
            f'      <category render="true" symbol="{i}" value="{i + 1}" '
            f'label="{_rank_label(i + 1, scores)} drive"/>'
        )
        syms.append(_LINE_SYMBOL.format(
            name=i, rgb=f"{r},{g},{b}", style="dash", width="1.8"))
    return _VECTOR_QML.format(
        labels_on="0",
        renderer=_CATEGORIZED.format(categories="\n".join(cats),
                                     symbols="\n".join(syms)),
        maptip=_maptip(_DRIVE_MAPTIP),
        labeling="",
    )


_FILL_SYMBOL = """      <symbol type="fill" name="{name}" alpha="0.45" clip_to_extent="1" force_rhr="0">
        <layer class="SimpleFill" enabled="1" locked="0" pass="0">
          <prop k="color" v="{rgb},110"/>
          <prop k="outline_color" v="{rgb},255"/>
          <prop k="outline_style" v="solid"/>
          <prop k="outline_width" v="0.8"/>
          <prop k="outline_width_unit" v="MM"/>
          <prop k="style" v="solid"/>
        </layer>
      </symbol>"""

_ZONE_MAPTIP = (
    "<b>#[% \"rank\" %] [% \"kind\" %]</b><br/>"
    "Elevation band: [% \"elev_lo_ft\" %]&ndash;[% \"elev_hi_ft\" %] ft<br/>"
    "Best connection: [% format_number(\"drop_ft\", 0) %] ft @ "
    "[% \"avg_grade_pct\" %]%<br/>"
    "Collapses [% \"n_collapsed\" %] point-pairs · shuttle "
    "[% \"drive_time_min\" %] min"
)


def write_zone_sidecars(out_dir: Path, n_ranks: int,
                        scores: list[float] | None = None) -> None:
    """Styles for the zone model: translucent Turbo blobs + trail lines."""
    colors = rank_colors(n_ranks)
    for kind in ("start_zones", "end_zones"):
        cats, syms = [], []
        for i, (r, g, b) in enumerate(colors):
            cats.append(
                f'      <category render="true" symbol="{i}" value="{i + 1}" '
                f'label="{_rank_label(i + 1, scores)}"/>')
            syms.append(_FILL_SYMBOL.format(name=i, rgb=f"{r},{g},{b}"))
        (out_dir / f"{kind}.qml").write_text(_VECTOR_QML.format(
            labels_on="1",
            renderer=_CATEGORIZED.format(categories="\n".join(cats),
                                         symbols="\n".join(syms)),
            maptip=_maptip(_ZONE_MAPTIP),
            labeling=_LABELING.format(label_expr=_xml_escape(
"'#' || \"rank\" || '  ' || \"elev_label\"")),
        ))
    # One trail per connection, all in a single neutral colour: the ZONES
    # carry the rank colour, so the trail just shows what connects to what.
    # Each trail takes its zone pair's colour — a trail links exactly two
    # zones, so matching is unambiguous and lets you trace a connection.
    # A light casing keeps it legible on pale topo and dark satellite alike.
    rules, syms, i = [], [], 0
    for k, (r, g, b) in enumerate(colors):
        for variant, style, width in (("primary", "solid", "0.9"),
                                      ("distinct", "dash", "0.7")):
            flt = _xml_escape(f'"rank" = {k + 1} AND "kind" = \'{variant}\'')
            rules.append(
                f'      <rule key="z{i}" symbol="{i}" filter="{flt}" '
                f'label="{_rank_label(k + 1, scores)}'
                f'{"" if variant == "primary" else " (alt line)"}"/>')
            syms.append(_CASED_LINE_SYMBOL.format(
                name=i, rgb=f"{r},{g},{b}", style=style, width=width,
                casing=f"{float(width) + 0.8:.1f}"))
            i += 1
    (out_dir / "zone_routes.qml").write_text(_VECTOR_QML.format(
        labels_on="0",
        renderer=_RULE_BASED.format(rules="\n".join(rules),
                                    symbols="\n".join(syms)),
        maptip=_maptip(_ROUTE_MAPTIP), labeling=""))


_USED_ROADS_QML = """<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis styleCategories="Symbology|Labeling|MapTips" version="3.28.0" labelsEnabled="1">
  <renderer-v2 type="graduatedSymbol" attr="n_uses" graduatedMethod="GraduatedSize">
    <ranges>
      <range lower="1" upper="1" symbol="0" label="used by 1 route"/>
      <range lower="2" upper="3" symbol="1" label="used by 2-3"/>
      <range lower="4" upper="99" symbol="2" label="shared artery (4+)"/>
    </ranges>
    <symbols>
{symbols}
    </symbols>
  </renderer-v2>
  <mapTip enabled="1">&lt;b&gt;Shuttle road&lt;/b&gt;&lt;br/&gt;Used by [% "ranks" %]</mapTip>
  <labeling type="simple">
    <settings calloutType="simple">
      <text-style fieldName="ranks" isExpression="0" fontSize="9"
                  textColor="20,20,20,255" fontSizeUnit="Point">
        <text-buffer bufferDraw="1" bufferSize="1" bufferSizeUnits="MM"
                     bufferColor="255,255,255,230"/>
      </text-style>
      <placement placement="2" placementFlags="2" repeatDistance="120"
                 repeatDistanceUnits="MM"/>
    </settings>
  </labeling>
</qgis>
"""


def write_used_roads_sidecar(path: Path) -> None:
    """Shuttle-drive roads: width grows with how many connections share the
    segment, labelled with the ranks that use it."""
    syms = [
        _LINE_SYMBOL.format(name=0, rgb="120,72,20", style="solid", width="0.7"),
        _LINE_SYMBOL.format(name=1, rgb="150,80,10", style="solid", width="1.4"),
        _LINE_SYMBOL.format(name=2, rgb="180,60,0", style="solid", width="2.4"),
    ]
    path.write_text(_USED_ROADS_QML.format(symbols="\n".join(syms)))


def _paletted(path: Path, entries: list[tuple[int, str, str]],
              opacity: float) -> None:
    rows = "\n".join(
        f'        <paletteEntry value="{v}" color="{color}" label="{label}" alpha="255"/>'
        for v, color, label in entries
    )
    path.write_text(_PALETTED_QML.format(opacity=opacity, entries=rows))


def write_sidecars(out_dir: Path, n_ranks: int,
                   scores: list[float] | None = None) -> None:
    """Write .qml files matching the basenames output.write_all produces."""
    (out_dir / "start_points.qml").write_text(
        _points_qml("circle", "start", n_ranks, scores))
    (out_dir / "end_points.qml").write_text(
        _points_qml("square", "end", n_ranks, scores))
    (out_dir / "routes.qml").write_text(_routes_qml(n_ranks, scores))
    (out_dir / "drive_routes.qml").write_text(_drive_qml(n_ranks, scores))
    _paletted(
        out_dir / "roads_overlay.qml",
        [(5, "#2b2b2b", "highway (paved arterial)"),
         (4, "#57407a", "paved local road"),
         (3, "#5c2e00", "gravel (passenger car)"),
         (2, "#9c5a1d", "dirt (high clearance)"),
         (1, "#d1a05a", "jeep/barely (OML1, closed, grade 4-5)"),
         (6, "#d6336c", "too steep to drive")],
        opacity=1.0,
    )
    _paletted(
        out_dir / "hazards_overlay.qml",
        [(1, "#e03131", "hazard (lake/cliff)"),
         (2, "#f59f00", "too steep to ride"),
         (3, "#4dabf7", "stream (crossable at cost)")],
        opacity=0.45,
    )


def write_color_legend(out_dir: Path, rows: list[dict]) -> None:
    """Standalone color-bar legend (color_legend.svg): one swatch per rank
    with score / drop / drive time, hottest at the top. ``rows`` are dicts
    with rank, score, drop_ft, drive_time_min (rank-ascending)."""
    colors = rank_colors(len(rows))
    row_h, top, width = 34, 54, 460
    height = top + row_h * len(rows) + 16
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" font-family="Menlo, monospace" font-size="13">',
        f'<rect width="{width}" height="{height}" fill="#1c1c1c"/>',
        '<text x="16" y="26" fill="#ffffff" font-size="15" '
        'font-weight="bold">Shuttle Scout — rank color bar (hotter = better)</text>',
        f'<text x="16" y="44" fill="#9a9a9a">rank   score   drop     shuttle</text>',
    ]
    for i, row in enumerate(rows):
        r, g, b = colors[i]
        y = top + i * row_h
        text = (f"#{row['rank']:<4} {row['score']:.3f}  "
                f"{row['drop_ft']:>5.0f} ft  {row['drive_time_min']:>4.0f} min")
        parts.append(
            f'<rect x="16" y="{y}" width="46" height="{row_h - 8}" rx="4" '
            f'fill="rgb({r},{g},{b})" stroke="#ffffff" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="76" y="{y + 18}" fill="#ffffff" '
            f'xml:space="preserve">{text}</text>'
        )
    parts.append("</svg>")
    (out_dir / "color_legend.svg").write_text("\n".join(parts))
