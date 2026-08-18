#!/usr/bin/env python3
"""§6.3 -- map value vocabularies, and show every collapse.

Named alts seam (docs/steps/s6-3.md):
    ValueMapper -- mapValue(str, Rule) -> concept_id?

IN   mapped rows with categorical values + value-map rules
OUT  same rows, value_as_concept_id set
     qc/value_collapse.json  [{rule_id, from:[str], to:str, n_rows, fan_in}]
     qc/alluvial_<variable>.png   raw value -> canonical concept
SIDE none under --unmapped_value_policy manifest; the run FAILS under
     `fail` when a value-mapped column carries a value no group claims.

Where the value-map rules come from
-----------------------------------
Nowhere, until this phase. bin/compile_rules.py emitted only kind
"column_map" and said why: a value_map "needs a value-level mapping table
nothing in ledger.confirmed.yaml's Contract offers". §6.3 therefore did not
start in §6 -- it started in §5.1, whose confirmed row now carries an
optional `value_map:` list of collapse groups, because a value collapse is a
harmonization decision and this pipeline takes those from a human at a gate
or not at all (§5.1's Why). §5.2 compiles one rule per group.

So the input here is rules/ruleset.json, exactly as §6.1's and §6.2's is,
and this script infers nothing: it never reads §2.1's observed value sets,
never proposes a grouping, and has no code path that can produce a mapping
without a rule to read.

The Trap (this card's own): best response is not a value to map
--------------------------------------------------------------
"It is an endpoint derived from a scan sequence under a criteria version
(§7.3); mapping the source's stated string silently imports whichever
criteria that cohort used, unrecorded."

Guarded structurally rather than by naming any variable (Global Constraint
4 forbids a clinical term in bin/ as much as in modules/): a value_map rule
whose pack variable is domain `derived` is REFUSED here, naming §7.3. An
endpoint is a derived variable by construction -- the pack's own schema
requires a `derivation` for one -- so the refusal catches the whole class
without knowing what a response criterion is. It cannot be reached by
accident either, because §4.1 cannot propose a derived variable (it has no
source column) and §5 cannot confirm what was never proposed; it is here for
the ledger that names one deliberately.

Why `to` is checked against the pack's domain_values
----------------------------------------------------
The pack IS the target variable set, and assets/schema_pack.json's own word
for `domain_values` is a variable's "enumerated legal values". A canonical
value invented at the gate is therefore a target vocabulary nothing
declared -- the value-level form of §6.2's nogo, "Never convert to a unit
the pack does not declare", and refused for the same reason: the alternative
is an artefact whose value set is whatever the last reviewer typed. A
variable declaring no domain_values has nothing to check against, which is
recorded by the absence of the check rather than by a silent pass.

What is written onto the row, and what deliberately is not
----------------------------------------------------------
Two columns, appended:

  value_as_concept_id  the standard concept the canonical value denotes,
                       taken from the rule's `to.concept_id`. NULL -- never
                       0 -- on a row no value-map rule claims: 0 is OMOP's
                       designated "no matching concept" and would be a claim
                       about a value that was never looked up, while NULL
                       says no value mapping applies to this row at all. A
                       rule whose reviewer supplied no concept id writes 0,
                       which is that claim, made honestly (Ruling R14: no
                       vocabulary is vendored, so nothing here can resolve
                       "0-1" to anything).
  value_rule_id        the rule that set it. §5.2's Why is that "every
                       emitted cell name the rule that produced it", and
                       this stage writes a cell. It IS derivable from
                       (rule_id, source value) plus the ruleset -- the value
                       sets cannot overlap, §5.1 refuses that -- but
                       provenance that has to be recomputed by re-running
                       the mapping is not provenance.

NOT written: the canonical value itself, as a second column beside the raw
one. That is the card's own Alternatives table ("emit both raw and canonical
columns"), listed there with its cost -- "widens every domain table and
pushes the decision to every consumer" -- so it is the swap, not the
implementation. The verbatim source value stays where §6.1 put it, the
canonical value lives on the rule the row now names, and
qc/value_collapse.json is the join between them.

Where an unmappable value is recorded, and why it needs its own file
--------------------------------------------------------------------
`--unmapped_value_policy`'s effect column is "whether an unmappable value
stops the run or is RECORDED", so `manifest` needs somewhere to record to,
and neither artefact the OUT slot names can hold it. qc/value_collapse.json
is a list of collapse groups whose done-when `jq` indexes it as one
(`[.[] | select(.fan_in > 1)]`), and a value that reached no group is not a
group. mapped/_unmapped.parquet is §6.1's record of source values with no
standard concept, and these rows DO have one -- their column was mapped;
moving them there would also delete them from a domain table, and the OUT
slot's first clause is "same rows".

So: qc/value_unmapped.json, written unconditionally (empty list included, so
"nothing was unmappable" and "the stage never ran" are not the same
observation), and written BEFORE the exit under `fail` so the file exists in
the failed task's work directory -- §6.2's own discipline, and the exit
message carries the same values so an operator reading a red run does not
have to find a work directory to learn which ones.

The alluvial plot, and why it is drawn by hand
-----------------------------------------------
§0.8 pins the toolchain and the runtime image is duckdb + PyYAML + procps;
matplotlib is not in it and adding a plotting dependency needs saying so in
the report. bin/link_resolve.py already hand-writes a PNG with a zlib/CRC32
encoder for §3.3's histogram, so its Canvas is imported rather than copied
-- the same sibling import bin/propose_ledger.py already makes of
bin/propose_channels.py, and for the same reason: two encoders in one repo
are two things that can disagree. The FONT is this file's own, because
link_resolve's covers digits and a decimal point (it labels two thresholds)
and this one labels arbitrary source values.

The pixel geometry of every ribbon is published into
qc/value_collapse.json, exactly as §3.3 publishes its threshold columns into
link_report.json, so a test asserts on the image rather than on the
filesystem. "The plot exists" is not an assertion anyone re-checks, and this
card's nogo -- "Never suppress the alluvial plot because the collapse looked
obvious" -- is unenforceable against a check that never looks at the pixels.

Every source value appears on the left, including the ones no group claims:
they are drawn in the unmapped colour with no ribbon leaving them. A value
that goes nowhere is precisely what the picture exists to make visible, and
omitting it would draw a collapse that looked complete.
"""
from __future__ import annotations

import argparse
import glob as globlib
import json
import os
import sys

import duckdb

# bin/ is on PATH inside the task and its scripts sit beside each other, so
# a sibling import resolves the same way bin/propose_ledger.py's import of
# bin/propose_channels.py does. Canvas is the PNG writer §3.3 wrote (zlib +
# CRC32, truecolour, filter type 0) and is imported rather than duplicated.
from link_resolve import Canvas

# OMOP's designated "no matching concept", same constant and same reason as
# bin/map_concepts.py's and bin/convert_units.py's. Ruling R14: recorded,
# never resolved. Used ONLY for a rule whose reviewer supplied no concept id
# -- a row no rule claims gets NULL instead (see the module docstring).
_NO_MATCHING_CONCEPT = 0

# Copied through verbatim, for the reason bin/convert_units.py copies it:
# mapped/_unmapped.parquet is a record of source values that reached no
# domain table (§6.1), so it carries no rule_id to match a value map against
# and no row for a value map to rewrite.
_PASSTHROUGH_BASENAMES = {"_unmapped.parquet"}

# §6.2 appended this beside every converted value. It ends in the same
# suffix as a domain's own source-value column, so it is excluded by name
# when the domain column is discovered.
_UNIT_SOURCE_VALUE = "unit_source_value"
_SOURCE_VALUE_SUFFIX = "_source_value"

_WIDTH, _HEIGHT = 760, 420
_MARGIN_L, _MARGIN_R, _MARGIN_T, _MARGIN_B = 150, 150, 46, 30
_NODE_W = 14
_NODE_GAP = 8

_BG = (255, 255, 255)
_INK = (60, 60, 60)
# A value no collapse group claims. Deliberately not one of the ribbon
# colours: it is the absence of a mapping, not another mapping.
_UNMAPPED_RGB = (176, 176, 176)
# One colour per collapse group, in the order the groups are written. Eight,
# and cycled beyond that -- a variable with more than eight canonical values
# is not collapsing much, and a ninth colour distinguishable from the first
# eight at this size does not exist.
_PALETTE = [
    (31, 119, 180),
    (255, 127, 14),
    (44, 160, 44),
    (214, 39, 40),
    (148, 103, 189),
    (140, 86, 75),
    (227, 119, 194),
    (23, 190, 207),
]

# A 3x5 bitmap font over the characters a source value can plausibly be
# spelled with. Lowercase is folded to uppercase before lookup: a 3x5
# lowercase glyph is not legible, and a label nobody can read is the same
# problem as no label. An unknown character renders as '?' rather than as
# blank, so a mangled label looks mangled.
#
# M, N and W are the three that have to be drawn deliberately rather than
# obviously. Three pixels of width cannot carry a diagonal, so the naive
# glyphs for them differ by one row and render as one another -- the first
# draft of this table printed "unknown" as something closer to "UNKMOWM".
# They are distinguished by SHAPE instead: M has a solid top band, N a
# stepped diagonal, W converges to a single pixel at the bottom.
_FONT = {
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "110", "100", "110", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("101", "110", "111", "011", "101"),
    "O": ("010", "101", "101", "101", "010"),
    "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "111", "011"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "101", "111", "010"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
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
    "-": ("000", "000", "111", "000", "000"),
    "+": ("000", "010", "111", "010", "000"),
    ".": ("000", "000", "000", "000", "010"),
    ",": ("000", "000", "000", "010", "100"),
    "_": ("000", "000", "000", "000", "111"),
    "/": ("001", "001", "010", "100", "100"),
    ":": ("000", "010", "000", "010", "000"),
    "<": ("001", "010", "100", "010", "001"),
    ">": ("100", "010", "001", "010", "100"),
    "=": ("000", "111", "000", "111", "000"),
    "(": ("001", "010", "010", "010", "001"),
    ")": ("100", "010", "010", "010", "100"),
    "?": ("111", "001", "010", "000", "010"),
    " ": ("000", "000", "000", "000", "000"),
}
_GLYPH_ADVANCE = 4  # 3 columns plus one of spacing, before scaling


class ValueMappingError(SystemExit):
    """Every refusal in this file, so a caller cannot catch one class of
    value-mapping failure while letting another through (bin/convert_units.py
    uses the same device for the same reason)."""


# ---------------------------------------------------------------------------
# The alts seam
# ---------------------------------------------------------------------------
def ValueMapper_mapValue(value: str | None, rule: dict) -> int | None:
    """The seam at its Contract's literal signature:

        mapValue(str, Rule) -> concept_id?

    Returns the concept id this rule assigns to `value`, or None when the
    rule does not claim it. Kept even though the bulk rewrite happens inside
    duckdb, for the reason bin/convert_units.py keeps UnitConverter_convert:
    the seam is what an alternative implementation replaces (the card's
    Alternatives table names "keep the finest common grain" and "emit both
    raw and canonical columns"), and an interface that exists only as a SQL
    fragment is not one anything can be swapped in behind (Global Constraint
    7).

    A rule whose reviewer supplied no concept id maps to
    _NO_MATCHING_CONCEPT rather than to None: the value WAS mapped -- that is
    what distinguishes it from a value no group claims -- and 0 is OMOP's own
    way of saying the concept behind it was not resolved.
    """
    if value is None:
        return None
    if value not in rule["from"]["values"]:
        return None
    concept_id = rule["to"].get("concept_id")
    return _NO_MATCHING_CONCEPT if concept_id is None else int(concept_id)


def plan_value_maps(rules: list[dict], pack_variables: list[dict]) -> dict[str, dict]:
    """One entry per value_map rule: what it collapses, and onto which of
    §6.1's column_map rules its rows hang.

    Every refusal happens here, before a single row is rewritten -- the same
    reason bin/convert_units.py builds its plan as its own pass. A mapper
    that discovered an undeclared canonical value halfway through a rewrite
    would leave one table value-mapped and another not, and afterwards the
    two would be indistinguishable.

    Returns the plan, keyed by the value rule's own id.
    """
    variable_by_name = {variable["name"]: variable for variable in pack_variables}

    column_rule_by_key: dict[tuple[str, str, str], str] = {}
    for rule in rules:
        if rule.get("kind") == "column_map":
            key = (rule["from"]["cohort_id"], rule["from"]["dataset_id"], rule["from"]["column"])
            column_rule_by_key[key] = rule["rule_id"]

    plan: dict[str, dict] = {}
    claimed: dict[tuple[str, str, str], dict[str, str]] = {}

    for rule in rules:
        if rule.get("kind") != "value_map":
            # §6.1 applies column maps, §6.2 unit conversions, §7 derivations.
            # Each kind is applied by the stage that owns it, never
            # opportunistically here.
            continue

        variable_name = rule["to"]["variable"]
        variable = variable_by_name.get(variable_name)
        if variable is None:
            # bin/map_concepts.py raises on this first and would have stopped
            # the run before anything reached here; repeated rather than
            # assumed because this script is also reachable on its own, and a
            # plan built against a pack that does not declare the variable
            # would silently map nothing.
            raise ValueMappingError(
                f"rule {rule['rule_id']} value-maps '{variable_name}', which the concept pack does not "
                "declare. The ruleset was compiled against a different pack."
            )

        if variable.get("domain") == "derived":
            # The card's Trap, as a structural refusal rather than a name
            # check. See the module docstring.
            raise ValueMappingError(
                f"rule {rule['rule_id']} value-maps '{variable_name}', which the pack declares as a "
                f"DERIVED variable (derivation '{variable.get('derivation')}'). A derived variable is "
                "computed from a scan sequence, an assessment history or another domain under a recorded "
                "method version -- §7 derives it and records that version. Mapping a source's stated "
                "string onto it would import whichever method that cohort used, unrecorded, which is "
                "§6.3's own Trap and §7.3's whole reason for existing."
            )

        declared = variable.get("domain_values")
        canonical = rule["to"]["value"]
        if declared is not None and canonical not in [str(value) for value in declared]:
            raise ValueMappingError(
                f"rule {rule['rule_id']} collapses {rule['from']['values']} into '{canonical}', which is "
                f"not among '{variable_name}''s declared domain_values {[str(v) for v in declared]}. The "
                "pack is the target variable set and domain_values is its enumerated legal values; a "
                "canonical value invented at the gate is a target vocabulary nothing declared. Add it to "
                "the pack, or collapse into a value the pack already has."
            )

        key = (rule["from"]["cohort_id"], rule["from"]["dataset_id"], rule["from"]["column"])
        column_rule_id = column_rule_by_key.get(key)
        if column_rule_id is None:
            raise ValueMappingError(
                f"rule {rule['rule_id']} value-maps {key[0]}/{key[1]}/{key[2]}, which no column_map rule "
                "writes. §6.1 never mapped that column into a domain table, so there are no rows here "
                "whose values could be collapsed."
            )

        # Overlap ACROSS rules, not within one. §5.1 already refuses a value
        # claimed by two groups of one confirmed row; two rows naming the
        # same column would slip past that check, and a CASE expression
        # resolves an overlap by branch order -- silently, and differently
        # depending on which rule was written first.
        for value in rule["from"]["values"]:
            previous = claimed.setdefault(key, {}).get(value)
            if previous is not None:
                raise ValueMappingError(
                    f"{key[0]}/{key[1]}/{key[2]}: source value '{value}' is claimed by two value_map "
                    f"rules ('{previous}' and '{canonical}'). Which applies is ambiguous, and §6.3 "
                    "refuses an ambiguous collapse rather than letting rule order decide it."
                )
            claimed[key][value] = canonical

        plan[rule["rule_id"]] = {
            "rule_id": rule["rule_id"],
            "variable": variable_name,
            "cohort_id": key[0],
            "dataset_id": key[1],
            "column": key[2],
            "column_rule_id": column_rule_id,
            "from": list(rule["from"]["values"]),
            "to": canonical,
            # fan_in is len(from) and is derived HERE, never carried on the
            # rule. A stored fan_in is a second place the width of a collapse
            # is written down, and the nogo ("Do not collapse a value set
            # without writing its fan_in") is about the number being right,
            # not about it being stored twice.
            "fan_in": len(rule["from"]["values"]),
            "value_as_concept_id": ValueMapper_mapValue(rule["from"]["values"][0], rule),
        }

    return plan


# ---------------------------------------------------------------------------
# The rewrite
# ---------------------------------------------------------------------------
def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _source_value_column(columns: list[str], path: str) -> str | None:
    """The domain's own `<domain>_source_value` column, or None.

    Discovered from the schema rather than from a domain -> table map copied
    out of bin/map_concepts.py: the table name is that file's decision and a
    second copy of the mapping here is a second thing to keep in step. §6.2's
    unit_source_value shares the suffix and is excluded by name.
    """
    candidates = [
        name for name in columns if name.endswith(_SOURCE_VALUE_SUFFIX) and name != _UNIT_SOURCE_VALUE
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        raise ValueMappingError(
            f"{path} carries more than one source-value column ({candidates}). §6.1 writes exactly one "
            "per domain table, so this table did not come from the stage this one reads."
        )
    return candidates[0]


def _case_expression(branches: list[str], default_sql: str) -> str:
    """A CASE, or the bare default when nothing contributes a branch.

    duckdb rejects an empty CASE, and an empty branch list is the ordinary
    state of a table no value_map rule touches -- §6.1 emits every
    --cdm_domains table whether or not it has rows.
    """
    if not branches:
        return default_sql
    return "CASE " + " ".join(branches) + f" ELSE {default_sql} END"


def rewrite_table(
    con: duckdb.DuckDBPyConnection,
    source_path: str,
    out_path: str,
    applicable: list[dict],
    source_column: str,
) -> None:
    """Append value_as_concept_id and value_rule_id to one mapped table.

    `SELECT *, ...` rather than a REPLACE: both columns are NEW here, so
    there is nothing to replace, and appending keeps every column §6.1 and
    §6.2 wrote in its own position -- bin/artefact_digest.py hashes columns
    in declared order, so a positional shift would move every §10.1 digest
    for a reason that is not data.

    Row order is untouched. §6.1 wrote `ORDER BY person_id, rule_id` and owns
    that decision, exactly as §6.2 left it alone.
    """
    quoted = '"' + source_column.replace('"', '""') + '"'
    concept_branches, rule_branches = [], []
    for entry in applicable:
        values = ", ".join(_sql_literal(value) for value in entry["from"])
        predicate = f"WHEN rule_id = {_sql_literal(entry['column_rule_id'])} AND {quoted} IN ({values})"
        concept_branches.append(f"{predicate} THEN {int(entry['value_as_concept_id'])}")
        rule_branches.append(f"{predicate} THEN {_sql_literal(entry['rule_id'])}")

    concept_case = _case_expression(concept_branches, "NULL")
    rule_case = _case_expression(rule_branches, "NULL")
    con.execute(
        f"""
        COPY (
            SELECT *,
                   CAST({concept_case} AS BIGINT)  AS value_as_concept_id,
                   CAST({rule_case} AS VARCHAR)    AS value_rule_id
            FROM read_parquet({_sql_literal(source_path)})
        ) TO {_sql_literal(out_path)} (FORMAT PARQUET)
        """
    )


# ---------------------------------------------------------------------------
# The alluvial plot
# ---------------------------------------------------------------------------
def _draw_text(canvas: Canvas, x: int, y: int, message: str, rgb: tuple[int, int, int], scale: int = 2) -> int:
    """Render `message` at (x, y) and return the width it occupied.

    Canvas.text() exists in bin/link_resolve.py and is deliberately not used:
    it renders through that module's own _FONT, which covers digits and a
    decimal point because it labels two numeric thresholds. Every pixel this
    writes goes through Canvas.rect, so the two share the encoder and differ
    only in the glyph table.
    """
    cursor = x
    for char in message:
        glyph = _FONT.get(char.upper(), _FONT["?"])
        for row, bits in enumerate(glyph):
            for col, bit in enumerate(bits):
                if bit == "1":
                    canvas.rect(
                        cursor + col * scale,
                        y + row * scale,
                        cursor + col * scale + scale - 1,
                        y + row * scale + scale - 1,
                        rgb,
                    )
        cursor += _GLYPH_ADVANCE * scale
    return cursor - x


def _text_width(message: str, scale: int = 2) -> int:
    return len(message) * _GLYPH_ADVANCE * scale


def _truncate(message: str, budget_px: int, scale: int = 2) -> str:
    """Fit a label into the margin, ending in '...' when it does not.

    A label silently cut mid-word reads as a different value; one that ends
    in an ellipsis reads as a label that did not fit.
    """
    per_char = _GLYPH_ADVANCE * scale
    room = max(budget_px // per_char, 1)
    if len(message) <= room:
        return message
    return message[: max(room - 3, 1)] + "..."


def render_alluvial(
    variable: str,
    groups: list[dict],
    rows_by_source: dict[str, int],
    unmapped_rows: dict[str, int],
    out_path: str,
) -> dict[str, dict]:
    """Draw one variable's collapse and return each group's pixel geometry.

    The geometry is the point. It goes into qc/value_collapse.json so a test
    reads the ribbon back OFF the image -- §3.3's shape, and this card's nogo
    ("Never suppress the alluvial plot because the collapse looked obvious")
    is not enforceable by a check that only asks whether a file exists.

    Left: one band per SOURCE value, height proportional to its row count,
    including the values no group claims -- drawn in the unmapped colour with
    no ribbon, because a value that goes nowhere is exactly what the picture
    is for. Right: one band per canonical value. Ribbon width = n_rows, at
    one pixels-per-row scale shared by both columns, so the two sides are
    comparable and an unmapped remainder is visible as the gap it is.
    """
    canvas = Canvas(_WIDTH, _HEIGHT, _BG)
    plot_top, plot_bottom = _MARGIN_T, _HEIGHT - _MARGIN_B
    left_x = _MARGIN_L + _NODE_W
    right_x = _WIDTH - _MARGIN_R - _NODE_W

    group_of_value: dict[str, dict] = {}
    for index, group in enumerate(groups):
        group["rgb"] = _PALETTE[index % len(_PALETTE)]
        for value in group["from"]:
            group_of_value[value] = group

    # Sorted, so the drawing is a function of the data and never of dict
    # insertion order -- §4.3's discipline, applied to a picture.
    source_values = sorted(set(rows_by_source) | set(unmapped_rows))
    total_rows = sum(rows_by_source.get(value, 0) + unmapped_rows.get(value, 0) for value in source_values)
    mapped_rows = sum(group["n_rows"] for group in groups)

    available = (plot_bottom - plot_top) - _NODE_GAP * max(len(source_values) - 1, 0)
    px_per_row = (available / total_rows) if total_rows else 0.0

    left_bands: dict[str, tuple[int, int]] = {}
    cursor = float(plot_top)
    for value in source_values:
        n = rows_by_source.get(value, 0) + unmapped_rows.get(value, 0)
        height = max(int(round(n * px_per_row)), 1)
        top = int(round(cursor))
        left_bands[value] = (top, top + height)
        rgb = group_of_value[value]["rgb"] if value in group_of_value else _UNMAPPED_RGB
        canvas.rect(_MARGIN_L, top, left_x - 1, top + height, rgb)
        label = _truncate(str(value), _MARGIN_L - 8)
        _draw_text(canvas, _MARGIN_L - 6 - _text_width(label), top + max((height - 10) // 2, 0), label, _INK)
        cursor += height + _NODE_GAP

    right_span = mapped_rows * px_per_row + _NODE_GAP * max(len(groups) - 1, 0)
    cursor = plot_top + max(((plot_bottom - plot_top) - right_span) / 2, 0)
    right_bands: dict[str, tuple[int, int]] = {}
    for group in groups:
        height = max(int(round(group["n_rows"] * px_per_row)), 1)
        top = int(round(cursor))
        right_bands[group["to"]] = (top, top + height)
        canvas.rect(right_x + 1, top, right_x + _NODE_W, top + height, group["rgb"])
        label = _truncate(str(group["to"]), _MARGIN_R - 8)
        _draw_text(canvas, right_x + _NODE_W + 6, top + max((height - 10) // 2, 0), label, _INK)
        cursor += height + _NODE_GAP

    # The ribbons. Each source value keeps its own sub-slot inside the target
    # band, in the same sorted order the left column uses, so a reader can
    # follow one value across without the ribbons crossing arbitrarily.
    geometry: dict[str, dict] = {}
    for group in groups:
        target_top, target_bottom = right_bands[group["to"]]
        offset = float(target_top)
        source_bands = []
        for value in sorted(group["from"]):
            left_top, left_bottom = left_bands.get(value, (target_top, target_top + 1))
            n = rows_by_source.get(value, 0)
            slot = max(int(round(n * px_per_row)), 1)
            right_top, right_bottom = int(round(offset)), int(round(offset)) + slot
            for x in range(left_x, right_x + 1):
                t = (x - left_x) / max(right_x - left_x, 1)
                y0 = int(round(left_top + (right_top - left_top) * t))
                y1 = int(round(left_bottom + (right_bottom - left_bottom) * t))
                canvas.vline(x, y0, y1, group["rgb"])
            source_bands.append({"value": value, "top_px_y": left_top, "bottom_px_y": left_bottom})
            offset += slot
        geometry[group["rule_id"]] = {
            "emitted": True,
            "path": os.path.basename(out_path),
            "width": _WIDTH,
            "height": _HEIGHT,
            "left_x_px": left_x,
            "right_x_px": right_x,
            "rgb": list(group["rgb"]),
            "target_band": {"top_px_y": target_top, "bottom_px_y": target_bottom},
            "source_bands": source_bands,
        }

    _draw_text(canvas, _MARGIN_L, 8, _truncate(variable, _WIDTH - 2 * _MARGIN_L), _INK, scale=3)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as handle:
        handle.write(canvas.to_png())
    return geometry


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§6.3 map value vocabularies, and show every collapse")
    ap.add_argument("--mapped-glob", required=True, help="Glob over the staged mapped/*.parquet")
    ap.add_argument("--ruleset", required=True, help="rules/ruleset.json (§5.2's output)")
    ap.add_argument("--pack-variables", required=True, help="JSON: the pack's `variables` list")
    ap.add_argument(
        "--value-params",
        required=True,
        help='JSON: {"max_fan_in_warn": int, "emit_alluvial": bool, "unmapped_value_policy": str}',
    )
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--out-qc", required=True, help="qc/value_collapse.json")
    ap.add_argument("--out-unmapped", required=True, help="qc/value_unmapped.json")
    ap.add_argument("--out-plot-dir", required=True, help="Directory the alluvial PNGs are written to")
    args = ap.parse_args(argv)

    value_params = json.loads(args.value_params)
    max_fan_in_warn = int(value_params["max_fan_in_warn"])
    emit_alluvial = bool(value_params["emit_alluvial"])
    unmapped_value_policy = str(value_params["unmapped_value_policy"])

    if not 1 <= max_fan_in_warn <= 20:
        raise ValueMappingError(f"--max_fan_in_warn {max_fan_in_warn} is outside the card's domain (1..20).")
    if unmapped_value_policy not in {"manifest", "fail"}:
        raise ValueMappingError(
            f"--unmapped_value_policy '{unmapped_value_policy}' is outside the card's enum (manifest|fail)."
        )

    pack_variables = json.loads(args.pack_variables)
    with open(args.ruleset, encoding="utf-8") as fh:
        rules = json.load(fh)

    plan = plan_value_maps(rules, pack_variables)

    sources = sorted(globlib.glob(args.mapped_glob))
    if not sources:
        raise ValueMappingError(
            f"--mapped-glob '{args.mapped_glob}' matched no parquet file. §6.1 emits every --cdm_domains "
            "table and _unmapped.parquet unconditionally and §6.2 rewrites them, so an empty match means "
            "the mapped artefact did not arrive, not that it was empty."
        )

    out_dir = args.out_dir.rstrip("/")
    os.makedirs(out_dir, exist_ok=True)
    con = duckdb.connect()

    n_rows_by_rule: dict[str, int] = {}
    rows_by_rule_value: dict[str, dict[str, int]] = {}
    unmapped: list[dict] = []
    plan_by_column_rule: dict[str, list[dict]] = {}
    for entry in plan.values():
        plan_by_column_rule.setdefault(entry["column_rule_id"], []).append(entry)

    for source in sources:
        basename = os.path.basename(source)
        out_path = os.path.join(out_dir, basename)
        if basename in _PASSTHROUGH_BASENAMES:
            con.execute(
                f"COPY (SELECT * FROM read_parquet({_sql_literal(source)})) "
                f"TO {_sql_literal(out_path)} (FORMAT PARQUET)"
            )
            continue

        columns = [row[0] for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet({_sql_literal(source)})"
        ).fetchall()]
        source_column = _source_value_column(columns, source)
        if source_column is None:
            con.execute(
                f"COPY (SELECT * FROM read_parquet({_sql_literal(source)})) "
                f"TO {_sql_literal(out_path)} (FORMAT PARQUET)"
            )
            continue

        present = {
            row[0]
            for row in con.execute(
                f"SELECT DISTINCT rule_id FROM read_parquet({_sql_literal(source)}) WHERE rule_id IS NOT NULL"
            ).fetchall()
        }
        applicable = [
            entry
            for column_rule_id, entries in plan_by_column_rule.items()
            if column_rule_id in present
            for entry in entries
        ]
        rewrite_table(con, source, out_path, applicable, source_column)
        if not applicable:
            continue

        quoted = '"' + source_column.replace('"', '""') + '"'
        for value_rule_id, source_value, count in con.execute(
            f"SELECT value_rule_id, {quoted}, count(*) FROM read_parquet({_sql_literal(out_path)}) "
            "WHERE value_rule_id IS NOT NULL GROUP BY 1, 2"
        ).fetchall():
            n_rows_by_rule[value_rule_id] = n_rows_by_rule.get(value_rule_id, 0) + int(count)
            rows_by_rule_value.setdefault(value_rule_id, {})[str(source_value)] = int(count)

        # A value in a column the ledger DID value-map that no group claims.
        # Scoped to those columns by rule_id: a column with no value_map has
        # no value vocabulary to be outside of, and counting its values here
        # would make the policy fire on every continuous measurement in the
        # run.
        mapped_column_rules = ", ".join(
            _sql_literal(rule_id) for rule_id in sorted(plan_by_column_rule) if rule_id in present
        )
        for column_rule_id, source_value, count in con.execute(
            f"SELECT rule_id, {quoted}, count(*) FROM read_parquet({_sql_literal(out_path)}) "
            f"WHERE value_rule_id IS NULL AND {quoted} IS NOT NULL "
            f"AND rule_id IN ({mapped_column_rules}) GROUP BY 1, 2"
        ).fetchall():
            reference = plan_by_column_rule[column_rule_id][0]
            unmapped.append(
                {
                    "cohort_id": reference["cohort_id"],
                    "dataset_id": reference["dataset_id"],
                    "column": reference["column"],
                    "variable": reference["variable"],
                    "source_value": str(source_value),
                    "n_rows": int(count),
                }
            )

    unmapped.sort(key=lambda row: (row["cohort_id"], row["dataset_id"], row["column"], row["source_value"]))

    # Grouped by variable for the plot, and sorted by canonical value so the
    # ribbons are laid out identically on every run.
    by_variable: dict[str, list[dict]] = {}
    for rule_id in sorted(plan):
        entry = dict(plan[rule_id])
        entry["n_rows"] = n_rows_by_rule.get(rule_id, 0)
        by_variable.setdefault(entry["variable"], []).append(entry)
    for entries in by_variable.values():
        entries.sort(key=lambda entry: entry["to"])

    geometry: dict[str, dict] = {}
    if emit_alluvial:
        os.makedirs(args.out_plot_dir, exist_ok=True)
        for variable, entries in sorted(by_variable.items()):
            rows_by_source: dict[str, int] = {}
            for entry in entries:
                for value, count in rows_by_rule_value.get(entry["rule_id"], {}).items():
                    rows_by_source[value] = rows_by_source.get(value, 0) + count
            unmapped_rows = {
                row["source_value"]: row["n_rows"] for row in unmapped if row["variable"] == variable
            }
            geometry.update(
                render_alluvial(
                    variable,
                    entries,
                    rows_by_source,
                    unmapped_rows,
                    os.path.join(args.out_plot_dir, f"alluvial_{variable}.png"),
                )
            )
    else:
        # Recorded, not omitted. The nogo forbids the CODE suppressing the
        # plot because a collapse looked obvious; an operator who turns it off
        # gets that written onto every entry, so a missing PNG is never read
        # as a stage that failed to draw one.
        for rule_id in plan:
            geometry[rule_id] = {
                "emitted": False,
                "reason": "--emit_alluvial is false; the plot was suppressed by configuration, not by §6.3",
            }

    collapses = []
    for variable, entries in sorted(by_variable.items()):
        for entry in entries:
            collapses.append(
                {
                    "rule_id": entry["rule_id"],
                    "variable": variable,
                    "from": entry["from"],
                    "to": entry["to"],
                    "n_rows": entry["n_rows"],
                    # Never optional, on any entry, whatever its width -- the
                    # card's nogo. fan_in == 1 is a collapse that lost
                    # nothing, which is a fact about the collapse and not a
                    # reason to omit the field.
                    "fan_in": entry["fan_in"],
                    "value_as_concept_id": entry["value_as_concept_id"],
                    "flag": "wide_collapse" if entry["fan_in"] > max_fan_in_warn else None,
                    # The threshold the verdict was reached against travels
                    # with it, the way §6.2's range_check carries its own
                    # quantiles: an entry read on its own says what it was
                    # compared to.
                    "max_fan_in_warn": max_fan_in_warn,
                    "alluvial": geometry.get(entry["rule_id"]),
                }
            )

    for path, document in ((args.out_qc, collapses), (args.out_unmapped, unmapped)):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2)
            fh.write("\n")

    wide = [entry for entry in collapses if entry["flag"] == "wide_collapse"]
    for entry in wide:
        print(
            f"wide_collapse: '{entry['variable']}' collapses {entry['from']} into '{entry['to']}' -- a "
            f"fan-in of {entry['fan_in']}, wider than --max_fan_in_warn {max_fan_in_warn}, over "
            f"{entry['n_rows']} rows.",
            file=sys.stderr,
        )

    print(
        f"§6.3 values: {len(collapses)} collapse group(s) over {len(by_variable)} variable(s), "
        f"{sum(entry['fan_in'] > 1 for entry in collapses)} lossy, {len(wide)} flagged wide_collapse, "
        f"{len(unmapped)} unmappable value(s) under policy '{unmapped_value_policy}'",
        file=sys.stderr,
    )

    if unmapped and unmapped_value_policy == "fail":
        described = "; ".join(
            f"{row['cohort_id']}/{row['dataset_id']}/{row['column']} (variable '{row['variable']}') "
            f"value '{row['source_value']}' on {row['n_rows']} row(s)"
            for row in unmapped
        )
        raise ValueMappingError(
            f"--unmapped_value_policy is 'fail' and {len(unmapped)} source value(s) in a value-mapped "
            f"column are claimed by no collapse group: {described}. A column whose value vocabulary was "
            "reviewed but does not cover its own value set is a gap in that review, not a datum with "
            "nowhere to go -- add a group for it (§5.1), or set --unmapped_value_policy manifest to "
            "record it and proceed. qc/value_unmapped.json in this task's work directory carries the "
            "full list."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
