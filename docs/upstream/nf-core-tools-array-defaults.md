# Upstream issue draft — nf-core/tools: array and object params are unsupported end-to-end

**Status:** drafted 2026-08-17, not yet filed.
**Target:** <https://github.com/nf-core/tools/issues/new?template=bug_report.yml>
**Blocks:** `.github/workflows/linting.yml` on this pipeline (8 affected params).

Everything below the line is the issue body, ready to paste. Keep this file in
sync if the draft is edited before filing, and record the issue number here once
it exists.

Prior art checked (none is a duplicate):

| issue | state | relation |
|---|---|---|
| [#2896](https://github.com/nf-core/tools/issues/2896) | open | same function, different symptom — unhandled `TypeError` from `int(None)`. Not array-related. |
| [#2387](https://github.com/nf-core/tools/issues/2387) | closed | added the `if param["default"] is None` guard to this same function |
| [#1100](https://github.com/nf-core/tools/issues/1100) | closed | added the empty-string guard to this same function |
| [#1554](https://github.com/nf-core/tools/issues/1554) | open | "Support nested parameters (objects)" — adjacent, about schema *structure*, not default coercion |

#2387 and #1100 matter: this function has already been patched twice for exactly
this class of bug, one scalar type at a time. That is the argument for fixing the
shape rather than adding a third special case.

---

### Description of the bug

`nf-core pipelines lint` cannot lint any pipeline whose schema declares a
parameter of `type: array` or `type: object` with a `default`. There are two
independent failures, and the second is only reachable once the first is fixed.

**1. `PipelineSchema.sanitise_param_default` stringifies array and object defaults.**

[`nf_core/pipelines/schema.py:194`](https://github.com/nf-core/tools/blob/master/nf_core/pipelines/schema.py#L194)
handles `boolean`, `integer` and `number` explicitly and then falls through to an
unconditional `str()`:

```python
        if param["default"] is None:
            return param

        # Strings
        param["default"] = str(param["default"])
        return param
```

For `type: array` that turns `["a", "b"]` into the Python `repr` string
`"['a', 'b']"` — which is not even valid JSON, because `repr` uses single quotes.
`validate_default_params()` then rejects the value it was just handed:

```
CRITICAL Critical error: [✗] Pipeline schema does not follow nf-core specs:
          Default parameters are invalid: '[1, 30.4375, 365.25, 0.001, 1000]' is
         not of type 'array'
INFO     Stopping tests...
```

This is a `CRITICAL` that aborts the whole lint run, so no other lint result is
produced at all.

**2. `PipelineSchema.build_schema_param` assumes any non-scalar config value is a string.**

[`nf_core/pipelines/schema.py:923`](https://github.com/nf-core/tools/blob/master/nf_core/pipelines/schema.py#L923)
has the same shape — a ladder of `None` / `bool` / `int` / `float`, then a string
fallthrough:

```python
        # TODO: remove string branch once old text-format config caches are no longer supported
        p_val = p_val.strip("\"'")
```

A `list` (or `dict`) coming from the config cache reaches `.strip()` and raises:

```
  File ".../nf_core/pipelines/schema.py", line 937, in build_schema_param
    p_val = p_val.strip("\"'")
AttributeError: 'list' object has no attribute 'strip'
```

reached via `schema_params` → `add_schema_found_configs` → `build_schema_param`.
This is an unhandled traceback, not a lint failure.

Both sites are the same underlying shape: a whitelist of scalar types with a
string catch-all, written before the config cache carried structured values, and
never extended when `array` / `object` became expressible in a pipeline schema.

### Minimal reproducer

No pipeline needed. `nextflow_schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "mre pipeline parameters",
  "type": "object",
  "$defs": {
    "opts": {
      "title": "Options",
      "type": "object",
      "properties": {
        "an_array":  { "type": "array",  "default": ["a", "b"] },
        "an_object": { "type": "object", "default": {"k": 1} },
        "a_string":  { "type": "string", "default": "hello" },
        "an_int":    { "type": "integer","default": 7 }
      }
    }
  },
  "allOf": [{ "$ref": "#/$defs/opts" }]
}
```

```python
from nf_core.pipelines.schema import PipelineSchema
s = PipelineSchema(); s.schema_filename = "nextflow_schema.json"; s.load_schema()
for k, v in s.schema["$defs"]["opts"]["properties"].items():
    print(k, v["type"], "->", repr(s.sanitise_param_default(dict(v))["default"]))
s.get_schema_defaults(); s.validate_default_params()
```

Observed on nf-core 4.1.0:

```
an_array  array   -> "['a', 'b']"     <-- str, was a list
an_object object  -> "{'k': 1}"       <-- str, was a dict
a_string  string  -> 'hello'
an_int    integer -> 7
AssertionError: Default parameters are invalid: "{'k': 1}" is not of type 'object'
```

### Expected behaviour

An `array` or `object` default is already the correct type as parsed from JSON.
It should be left alone, and lint should proceed.

### Suggested fix

The narrow change is to stop the catch-all from claiming types it was never
meant to handle — make the last branch say what its own comment already says:

```python
-        # Strings
-        param["default"] = str(param["default"])
+        # Strings. Anything else -- array, object -- is already the type the
+        # schema declares, and str() would corrupt it into an invalid default.
+        if param["type"] == "string":
+            param["default"] = str(param["default"])
         return param
```

and give `build_schema_param` the two branches it is missing, before the string
fallthrough:

```python
         if isinstance(p_val, float):
             return {"type": "number", "default": p_val}
+        if isinstance(p_val, list):
+            return {"type": "array", "default": p_val}
+        if isinstance(p_val, dict):
+            return {"type": "object", "default": p_val}
```

I verified both patches locally against nf-core 4.1.0:

* the MRE above passes;
* a real pipeline with 8 affected params (7 `array` — one of them the template's
  own `schema_ignore_params: []` — and 1 `object`) lints to completion instead of
  aborting: 25 ordinary, unrelated lint findings and no crash;
* every existing scalar coercion is unchanged:
  `boolean "true"→True`, `integer "7"→7`, `number "1.5"→1.5`,
  `string 123→"123"`, `string "  "→""`;
* `{"type": "integer", "default": None}` still raises the same `TypeError` as
  before, i.e. this does not accidentally paper over #2896.

Happy to open a PR with both changes and a test if that is useful.

### System information

* nf-core/tools 4.1.0 (latest release at time of writing)
* Both functions present unchanged on `master` and `dev` as of 2026-08-17
  (checked: neither has a `list`/`dict` branch)
* Python 3.10.13 (also reproduces under the 3.14 that the nf-core linting
  workflow pins)
* `repository_type: pipeline`, `is_nfcore: false`
