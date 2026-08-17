#!/usr/bin/env python3
"""§3.3 -- threshold into links, and emit the match-weight histogram.

Named alts seam (docs/steps/s3-3.md):
    Resolver_resolve([ScoredPair]) -> [PersonCluster]

    weight >= match_threshold          -> link
    clerical_threshold <= w < match    -> CLERICAL review band, reported not linked
    weight <  clerical_threshold       -> distinct

Contract (docs/steps/s3-3.md):
    IN   link/scores.parquet
    OUT  link/links.parquet        (person_id, cohort_id, [source_row_id])
         link/match_histogram.png  both thresholds marked
         link/link_report.json     { n_in, n_persons, n_clerical, collapse_ratio }
    SIDE none

The histogram is a mixture of two distributions, and the overlap region is
the only honest picture of what a threshold choice costs. It is emitted
UNCONDITIONALLY -- §3.3's nogo, "never suppress the histogram when the run
looks clean" -- because the run that most needs it is the run nobody thought
to ask for it.

The Trap: tuning the threshold until N looks right. N is the thing being
measured; using it as the target makes the linkage unfalsifiable. Nothing in
this file reads n_persons before choosing a cut, and nothing can: both
thresholds are params, fixed before the first pair is read.

Not here (nogo): the clerical band is never auto-resolved. Pairs in it are
counted and written out for review, never linked. And no threshold is ever
chosen by a statistic computed on the outcome -- there is no outcome input
to this script at all.

Why the PNG is written by hand. §0.8 pins the toolchain and the runtime
image is duckdb + PyYAML + procps; matplotlib is not in it. Adding it would
mean rebuilding and re-pinning the image across nine module copies to draw
one bar chart. A PNG is zlib plus a header, the stdlib has zlib, and the
result is byte-deterministic -- which a rasterised matplotlib figure is
not.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
import zlib

import duckdb

# ---------------------------------------------------------------------------
# A minimal PNG writer. Truecolour (RGB), 8 bits per channel, filter type 0.
# ---------------------------------------------------------------------------
_WIDTH, _HEIGHT = 720, 360
_MARGIN_L, _MARGIN_R, _MARGIN_T, _MARGIN_B = 56, 24, 40, 40
_N_BINS = 60

_BG = (255, 255, 255)
_AXIS = (60, 60, 60)
_BAR = (120, 144, 168)
_CLERICAL_RGB = (214, 148, 30)   # the review band's lower edge
_MATCH_RGB = (24, 122, 60)       # the link cut
_GRID = (222, 222, 222)

# A 3x5 bitmap font, enough to label two thresholds and an axis. Columns are
# bit-packed per row, most significant bit leftmost.
_FONT = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    ".": ("000", "000", "000", "000", "010"),
    "-": ("000", "000", "111", "000", "000"),
    " ": ("000", "000", "000", "000", "000"),
}


class Canvas:
    def __init__(self, width: int, height: int, background: tuple[int, int, int]):
        self.width, self.height = width, height
        self.pixels = bytearray(bytes(background) * width * height)

    def set(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.pixels[offset : offset + 3] = bytes(rgb)

    def vline(self, x: int, y0: int, y1: int, rgb: tuple[int, int, int], thickness: int = 1) -> None:
        for dx in range(thickness):
            for y in range(min(y0, y1), max(y0, y1) + 1):
                self.set(x + dx, y, rgb)

    def hline(self, y: int, x0: int, x1: int, rgb: tuple[int, int, int]) -> None:
        for x in range(min(x0, x1), max(x0, x1) + 1):
            self.set(x, y, rgb)

    def rect(self, x0: int, y0: int, x1: int, y1: int, rgb: tuple[int, int, int]) -> None:
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for x in range(min(x0, x1), max(x0, x1) + 1):
                self.set(x, y, rgb)

    def text(self, x: int, y: int, message: str, rgb: tuple[int, int, int], scale: int = 1) -> None:
        cursor = x
        for char in message:
            glyph = _FONT.get(char, _FONT[" "])
            for row, bits in enumerate(glyph):
                for col, bit in enumerate(bits):
                    if bit == "1":
                        self.rect(
                            cursor + col * scale,
                            y + row * scale,
                            cursor + col * scale + scale - 1,
                            y + row * scale + scale - 1,
                            rgb,
                        )
            cursor += (3 + 1) * scale

    def to_png(self) -> bytes:
        raw = bytearray()
        for y in range(self.height):
            raw.append(0)  # filter type 0 (None) on every scanline
            start = y * self.width * 3
            raw += self.pixels[start : start + self.width * 3]

        def chunk(tag: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + tag
                + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
            )

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b"")
        )


def _fmt(value: float) -> str:
    return f"{value:.1f}"


def render_histogram(
    weights: list[float],
    match_threshold: float,
    clerical_threshold: float,
    out_path: str,
) -> dict:
    """Draw the match-weight histogram with BOTH thresholds marked, and
    return where they were drawn.

    The returned pixel columns go into link_report.json. "Both thresholds
    are drawn on it" is §3.3's done-when, and an image nobody can assert on
    turns that into an eyeball check that quietly stops happening -- so the
    positions are published and a test reads the pixels back.
    """
    canvas = Canvas(_WIDTH, _HEIGHT, _BG)
    plot_left, plot_right = _MARGIN_L, _WIDTH - _MARGIN_R
    plot_top, plot_bottom = _MARGIN_T, _HEIGHT - _MARGIN_B

    # The domain always CONTAINS both thresholds, even when no pair scores
    # anywhere near them. A histogram that cropped a threshold out of frame
    # would satisfy "the file exists" and answer none of the question the
    # figure is for.
    lo = min(weights + [clerical_threshold, match_threshold])
    hi = max(weights + [clerical_threshold, match_threshold])
    if math.isclose(lo, hi):
        lo, hi = lo - 1.0, hi + 1.0
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad

    counts = [0] * _N_BINS
    for weight in weights:
        index = int((weight - lo) / (hi - lo) * _N_BINS)
        counts[min(max(index, 0), _N_BINS - 1)] += 1
    peak = max(counts) if counts else 0

    def to_x(value: float) -> int:
        return plot_left + int((value - lo) / (hi - lo) * (plot_right - plot_left))

    for fraction in (0.25, 0.5, 0.75, 1.0):
        y = plot_bottom - int(fraction * (plot_bottom - plot_top))
        canvas.hline(y, plot_left, plot_right, _GRID)

    bin_width = (plot_right - plot_left) / _N_BINS
    for index, count in enumerate(counts):
        if not count or not peak:
            continue
        x0 = plot_left + int(index * bin_width)
        x1 = plot_left + int((index + 1) * bin_width) - 1
        height = int(count / peak * (plot_bottom - plot_top))
        canvas.rect(x0, plot_bottom - height, max(x1, x0), plot_bottom - 1, _BAR)

    canvas.hline(plot_bottom, plot_left, plot_right, _AXIS)
    canvas.vline(plot_left, plot_top, plot_bottom, _AXIS)

    clerical_x, match_x = to_x(clerical_threshold), to_x(match_threshold)
    canvas.vline(clerical_x, plot_top, plot_bottom, _CLERICAL_RGB, thickness=2)
    canvas.vline(match_x, plot_top, plot_bottom, _MATCH_RGB, thickness=2)

    # Legend: a colour swatch and the numeric value for each threshold, so
    # the marks are readable without the report open beside them.
    canvas.rect(plot_left + 6, plot_top - 26, plot_left + 16, plot_top - 18, _CLERICAL_RGB)
    canvas.text(plot_left + 22, plot_top - 27, _fmt(clerical_threshold), _AXIS, scale=2)
    canvas.rect(plot_left + 150, plot_top - 26, plot_left + 160, plot_top - 18, _MATCH_RGB)
    canvas.text(plot_left + 166, plot_top - 27, _fmt(match_threshold), _AXIS, scale=2)

    canvas.text(plot_left, plot_bottom + 10, _fmt(lo), _AXIS, scale=2)
    canvas.text(plot_right - 60, plot_bottom + 10, _fmt(hi), _AXIS, scale=2)

    with open(out_path, "wb") as handle:
        handle.write(canvas.to_png())

    return {
        "width": _WIDTH,
        "height": _HEIGHT,
        "n_bins": _N_BINS,
        "weight_axis_min": lo,
        "weight_axis_max": hi,
        "clerical_threshold_px_x": clerical_x,
        "match_threshold_px_x": match_x,
        "clerical_threshold_rgb": list(_CLERICAL_RGB),
        "match_threshold_rgb": list(_MATCH_RGB),
        "plot_top_px_y": plot_top,
        "plot_bottom_px_y": plot_bottom,
    }


# ---------------------------------------------------------------------------
# §3.3 -- the Resolver alts seam. Today's only implementation: connected
# components, which is what splink does by default and what the
# Alternatives table names first. Its cost is written down rather than
# hidden: ONE bad edge chains two people into one cluster transitively, and
# collapse_ratio is the number that shows it happening.
# ---------------------------------------------------------------------------
def Resolver_resolve(record_ids: list[str], links: list[tuple[str, str]]) -> dict[str, list[str]]:
    parent = {record_id: record_id for record_id in record_ids}

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for left, right in links:
        if left not in parent or right not in parent:
            continue
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            # Deterministic merge direction: the lexicographically smaller
            # root wins, so the clustering does not depend on the order
            # links arrive in.
            if left_root < right_root:
                parent[right_root] = left_root
            else:
                parent[left_root] = right_root

    clusters: dict[str, list[str]] = {}
    for record_id in record_ids:
        clusters.setdefault(find(record_id), []).append(record_id)
    return {root: sorted(members) for root, members in clusters.items()}


def _person_id(cohort_id: str, members: list[str]) -> str:
    """A content hash of the cluster's sorted membership, not a counter.

    Same reasoning as §5.2's rule_id: an id derived from position or time
    makes an audit against an older output resolve to the wrong person,
    which is worse than failing to resolve. Re-running on the same records
    with the same links yields the same person_id; adding an unrelated
    cohort does not renumber anybody.
    """
    payload = "\x1f".join([cohort_id] + members).encode("utf-8")
    return "P-" + hashlib.sha256(payload).hexdigest()[:16]


def _write_links_parquet(con: duckdb.DuckDBPyConnection, rows: list[dict], out_path: str) -> None:
    """links.parquet carries a LIST column: source_row_id is bracketed in
    §3.3's own OUT slot, because a person IS its set of source records and
    splitting them across rows would lose which records were resolved
    together -- the one fact the table exists to record."""
    con.execute(
        """
        CREATE OR REPLACE TABLE links (
            person_id     VARCHAR,
            cohort_id     VARCHAR,
            source_row_id VARCHAR[]
        )
        """
    )
    # Staged flat, converted once -- same reasoning as link_score.py's own
    # writer. Every record that no rule ever blocked is still a person, so
    # this table has a row per person and a long input means a long table;
    # one round trip per row is one round trip too many.
    con.execute(
        "CREATE OR REPLACE TABLE links_staging (person_id VARCHAR, cohort_id VARCHAR, source_json VARCHAR)"
    )
    if rows:
        con.executemany(
            "INSERT INTO links_staging VALUES (?, ?, ?)",
            [(row["person_id"], row["cohort_id"], json.dumps(row["source_row_id"])) for row in rows],
        )
        con.execute(
            """
            INSERT INTO links
            SELECT person_id, cohort_id, from_json(source_json::JSON, '["VARCHAR"]')
            FROM links_staging
            """
        )
    con.execute(f"COPY (SELECT * FROM links ORDER BY cohort_id, person_id) TO '{out_path}' (FORMAT PARQUET)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="§3.3 thresholding and resolution")
    parser.add_argument("--scores", required=True)
    parser.add_argument("--tables", required=True)
    parser.add_argument("--match-threshold", type=float, required=True)
    parser.add_argument("--clerical-threshold", type=float, required=True)
    parser.add_argument("--max-collapse-ratio-drop", type=float, required=True)
    parser.add_argument("--out-links", required=True)
    parser.add_argument("--out-histogram", required=True)
    parser.add_argument("--out-report", required=True)
    parser.add_argument("--out-clerical", required=True)
    args = parser.parse_args(argv)

    if args.clerical_threshold > args.match_threshold:
        raise ValueError(
            f"--clerical_threshold ({args.clerical_threshold}) is above --match_threshold "
            f"({args.match_threshold}). The review band is the interval BETWEEN them; inverted, "
            "there is no band and every pair is either linked or distinct with nothing reported."
        )

    con = duckdb.connect()
    tables = json.loads(args.tables)

    # Every record in the run, whether or not blocking ever paired it. A
    # record no rule blocked is still a person -- a singleton cluster -- and
    # leaving it out would inflate collapse_ratio by shrinking the
    # denominator, hiding exactly the over-linkage that ratio exists to
    # expose.
    record_ids: list[str] = []
    cohort_of: dict[str, str] = {}
    for spec in tables:
        n_rows = con.execute(
            "SELECT count(*) FROM read_csv(?, header=true, all_varchar=true, sample_size=-1)", [spec["path"]]
        ).fetchone()[0]
        for index in range(1, int(n_rows) + 1):
            record_id = f"{spec['dataset_id']}#{index}"
            record_ids.append(record_id)
            cohort_of[record_id] = spec["cohort_id"]

    scored = con.execute(
        f"SELECT cohort_id, left_id, right_id, match_weight FROM read_parquet('{args.scores}') "
        "ORDER BY cohort_id, left_id, right_id"
    ).fetchall()
    weights = [float(row[3]) for row in scored]

    links = [(row[1], row[2]) for row in scored if row[3] >= args.match_threshold]
    clerical = [row for row in scored if args.clerical_threshold <= row[3] < args.match_threshold]

    clusters = Resolver_resolve(record_ids, links)

    rows = []
    for members in clusters.values():
        cohort_id = cohort_of[members[0]]
        rows.append({"person_id": _person_id(cohort_id, members), "cohort_id": cohort_id, "source_row_id": members})
    rows.sort(key=lambda r: (r["cohort_id"], r["person_id"]))

    n_in = len(record_ids)
    n_persons = len(rows)
    collapse_ratio = (n_persons / n_in) if n_in else 1.0

    histogram = render_histogram(weights, args.match_threshold, args.clerical_threshold, args.out_histogram)

    # The clerical band, written out rather than resolved (§3.3 nogo: "do
    # not auto-resolve the clerical band"). The card's Trap says to tune on
    # the band's CONTENTS, which requires the contents to exist as a file
    # somebody can open.
    with open(args.out_clerical, "w", encoding="utf-8") as handle:
        json.dump(
            [
                {"cohort_id": c, "left_id": l, "right_id": r, "match_weight": float(w)}
                for c, l, r, w in clerical
            ],
            handle,
            indent=2,
        )
        handle.write("\n")

    report = {
        "n_in": n_in,
        "n_persons": n_persons,
        "n_clerical": len(clerical),
        "collapse_ratio": collapse_ratio,
        "n_scored_pairs": len(scored),
        "n_linked_pairs": len(links),
        "match_threshold": args.match_threshold,
        "clerical_threshold": args.clerical_threshold,
        "histogram": histogram,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out_report)) or ".", exist_ok=True)
    with open(args.out_report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")

    _write_links_parquet(con, rows, args.out_links)

    # The alarm, fired AFTER every output is written. A run that collapsed
    # more than max_collapse_ratio_drop of its records has almost certainly
    # chained two people through one bad edge (the connected-components cost
    # the Alternatives table names) -- and the histogram and the clerical
    # band are exactly what someone needs to see to diagnose it, so they are
    # on disk before this raises.
    if collapse_ratio < args.max_collapse_ratio_drop:
        raise SystemExit(
            f"Linkage collapsed {n_in} records into {n_persons} persons (collapse_ratio "
            f"{collapse_ratio:.4f}), below --max_collapse_ratio_drop {args.max_collapse_ratio_drop}. "
            "Connected components chains transitively, so a single spurious edge can merge two people "
            f"and everything they touch. Inspect {args.out_histogram} for a missing overlap region and "
            f"{args.out_clerical} for what sits just under the cut before lowering the threshold."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
