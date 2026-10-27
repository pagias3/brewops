# Plan: dashboard date-range filter (ticket 005)

Goal (from `tickets/005-date-filter.md`): let people pick a date range and have the
dashboard numbers and charts follow it. Default (no range) must behave exactly as today.

Project facts you need:
- Python + FastAPI + SQLite. Code is in `src/brewops/`. Frontend is vanilla JS, no build step,
  no external resources (a test enforces this).
- Run tests: `uv run pytest`. Run app: `uv run seed` then `uv run start`, open http://localhost:8123.
- Timestamps are stored as naive local-time text `YYYY-MM-DD HH:MM:SS` (no timezone handling anywhere).

## Decisions (already made, do not revisit)

1. Range is **inclusive on both ends, by calendar day**: `from=2026-06-01&to=2026-06-07` includes
   everything from `2026-06-01 00:00:00` through `2026-06-07 23:59:59`.
2. Both params are optional. Only `from` = "from that day onward". Only `to` = "up to and including
   that day". Neither = all time (today's behaviour).
3. The filter applies to **brew counts** everywhere: `/api/stats` and the brew-derived fields of
   `/api/machines/{id}` (`brew_count`, `last_brew`, `specialty`). It does **not** apply to
   `last_maintenance` or `recent_errors`; those are current-state facts and stay unfiltered.
4. Future dates in the filter are allowed (they just produce empty results).
5. Days with no brews are **not** zero-filled by the API; `per_day` keeps listing only days that have brews.
   The chart is allowed to compress gaps. (Out of scope: zero-filling, presets, URL persistence.)

## 1. `src/brewops/db/queries.py`

### 1a. Add a small helper (top of file, after imports)

```python
def _range_bounds(date_from: str | None, date_to: str | None) -> tuple[str | None, str | None]:
    """Turn inclusive 'YYYY-MM-DD' dates into a half-open [start, end) timestamp range.
    Either side may be None (unbounded)."""
```

- `start` = `f"{date_from} 00:00:00"` if `date_from` else `None`.
- `end` = (`date_to` + 1 day) formatted `"YYYY-MM-DD 00:00:00"` if `date_to` else `None`.
  Use `datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)`. Add `from datetime import datetime, timedelta`.
- Always compare with `timestamp >= start` and `timestamp < end` (half-open). Never use `BETWEEN` with bare dates
  (it would drop the whole last day) and never wrap `timestamp` in `DATE()` in a WHERE (defeats the index
  `idx_brew_events_timestamp`).

### 1b. Change `get_stats(conn)` -> `get_stats(conn, date_from=None, date_to=None)`

Keep the return shape identical: `{"total_brews", "per_drink", "per_day"}`.

Build the condition once, e.g. a list of SQL fragments and a params list:

```python
start, end = _range_bounds(date_from, date_to)
conds, params = [], []
if start: conds.append("timestamp >= ?"); params.append(start)
if end:   conds.append("timestamp < ?");  params.append(end)
```

- **total**: `SELECT COUNT(*) ... FROM brew_events` + `WHERE <conds>` when present.
- **per_day**: same `WHERE` before `GROUP BY DATE(timestamp)`.
- **per_drink** (the tricky one): it is `drink_types dt LEFT JOIN brew_events be ON be.drink_type = dt.name`.
  Put the range conditions in the **`ON` clause** (prefix columns with `be.`:
  `AND be.timestamp >= ? AND be.timestamp < ?`), **not** in `WHERE`. If you put them in `WHERE`, the
  LEFT JOIN becomes an inner join and drinks with zero brews disappear from the bars. They must stay
  listed with `count: 0`.
- Use `?` placeholders only; never f-string user values into SQL (the fragments you build contain only
  fixed text, the values go in `params`).

### 1c. Change `get_machine_health(conn, machine_id)` -> `get_machine_health(conn, machine_id, date_from=None, date_to=None)`

- Apply the range to the **brews** query (`COUNT(*)`, `MAX(timestamp)` -> `brew_count`, `last_brew`) and to the
  **specialty** query (add range conditions to its `WHERE be.machine_id = ?`, using `be.timestamp`).
- Leave the `last_maintenance` and `recent_errors` queries untouched.
- Empty range result: `brew_count == 0`, `last_brew is None`, `specialty is None` (already how the code behaves
  with no rows).
- Keep the specialty tie-break (`COUNT(*) DESC, MAX(be.timestamp) DESC, dt.id ASC`) unchanged.

Share the condition-building between the three functions via one helper rather than copy-pasting
(e.g. `_range_conditions(column, date_from, date_to) -> tuple[list[str], list[str]]`).

## 2. `src/brewops/api/main.py`

### 2a. Date-range dependency/helper

Add a function `parse_date_range(date_from, date_to) -> tuple[str | None, str | None]` that:
- Parses each non-empty value with `datetime.strptime(value, "%Y-%m-%d")`; invalid -> `HTTPException(400, f"invalid date {value!r}, expected YYYY-MM-DD")`.
  (Do NOT reuse `parse_timestamp`: it needs a time part and rejects future dates.)
- If both present and `from > to` -> `HTTPException(400, "'from' must not be after 'to'")`.
  Equal dates are valid (a single day).
- Treat empty strings (`?from=`) as "not provided" (`None`).
- Returns normalized `YYYY-MM-DD` strings or `None`.

### 2b. New query parameters

`from` is a Python keyword, so use FastAPI `Query` with an alias:

```python
from fastapi import Query

@app.get("/api/stats")
def stats(
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    conn: sqlite3.Connection = Depends(get_db),
):
    start, end = parse_date_range(date_from, date_to)
    return queries.get_stats(conn, start, end)
```

Do the same for `GET /api/machines/{machine_id}` (`machine_health`), passing the range to
`queries.get_machine_health`. Keep the existing 404 for an unknown machine. Validate the dates **before**
touching the DB so a bad date is always a 400.

Public API after the change:

| Endpoint | New optional query params | Notes |
|---|---|---|
| `GET /api/stats` | `from=YYYY-MM-DD`, `to=YYYY-MM-DD` | response shape unchanged |
| `GET /api/machines/{id}` | `from=YYYY-MM-DD`, `to=YYYY-MM-DD` | only `brew_count`, `last_brew`, `specialty` are filtered |

`GET /api/machines` (list) is unchanged.

## 3. Frontend

### 3a. `src/brewops/frontend/index.html`

Inside `<section id="dashboard">`, **before** `<div class="stat-tiles">`, add a filter bar:

```html
<div class="filter-bar" id="date-filter">
  <label for="filter-from">From</label>
  <input type="date" id="filter-from">
  <label for="filter-to">To</label>
  <input type="date" id="filter-to">
  <button type="button" id="filter-clear">All time</button>
  <p id="filter-message" class="message" role="status"></p>
</div>
```

Also update the tile label `brews total` (`<span class="tile-label">` next to `id="total-brews"`) so it
doesn't lie when filtered: give the span an id, `id="total-brews-label"`, and set its text from JS
("brews total" when unfiltered, "brews in range" when filtered).

### 3b. `src/brewops/frontend/style.css`

- Add `.filter-bar` (flex row, wrap, gap ~0.75rem, align center) using existing CSS variables
  (`--panel`, `--line`, `--radius`, `--ink-soft`, `--brass-deep`); do not hard-code new colours.
- The `#dashboard` grid places items by explicit `grid-row` (stat tiles row 1, timeline/drink bars row 2,
  machines row 3). Put `.filter-bar` in `grid-column: 1 / -1; grid-row: 1;` and shift the existing rows by one:
  `.stat-tiles` -> `grid-row: 2`, timeline and drink-bars panels -> `grid-row: 3`, machines panel -> `grid-row: 4`.
  (Otherwise the bar overlaps the tiles.)
- Style `button#filter-clear` like the existing form buttons if there is a rule for them; otherwise a simple
  bordered button is fine.

### 3c. `src/brewops/frontend/app.js`

Function-by-function. Functions not listed here (`fetchJSON`, `renderDrinkBars`, `localNow`, `fillSelect`,
`setupForms`, `submitForm`) are **unchanged**.

| Function | Status | Exact change |
|---|---|---|
| `rangeQuery()` | **new** | Reads `#filter-from` and `#filter-to`. Builds a `URLSearchParams` with `from` and/or `to` only for non-empty values. Returns `""` if none, else `"?" + params.toString()`. |
| `hasRange()` | **new** | Returns `true` if either input has a value. Used for the tile label. |
| `showFilterMessage(text)` | **new** | Sets `#filter-message` text; resets its class to `"message"` and adds `"error"` if `text` is non-empty. `showFilterMessage("")` clears it. |
| `setupFilter()` | **new** | Called once at the bottom of the file, next to `setupForms().catch(...)` (same `.catch(console.error)` style). Adds a `change` listener to `#filter-from` and `#filter-to` and a `click` listener to `#filter-clear` (see below). |
| `loadDashboard()` | **changed** | See below. |
| `renderTimeline(perDay)` | **changed** | Empty-state handling, see below. |
| `renderMachineCards(healths)` | verify only | No code change expected; just confirm the empty-range card renders (see below). |

**`loadDashboard()`**
- Compute `const query = rangeQuery();` once at the top.
- `fetchJSON("/api/stats")` -> `fetchJSON("/api/stats" + query)`.
- `fetchJSON(`/api/machines/${m.id}`)` inside the `Promise.all` -> `fetchJSON(`/api/machines/${m.id}${query}`)`.
  Leave `fetchJSON("/api/machines")` (the list) without a query.
- After setting `#total-brews`, set `document.getElementById("total-brews-label").textContent` to
  `hasRange() ? "brews in range" : "brews total"`.
- `brews-today` logic (`stats.per_day[stats.per_day.length - 1]`, falling back to `0`) stays as is. With a range it
  means "last active day inside the range"; the tile label ("brews on last active day") is still accurate.
- Call `showFilterMessage("")` after the stats request succeeds.
- Stale-response guard (a fast user can change dates twice quickly): keep a module-level `let loadSeq = 0;`,
  do `const seq = ++loadSeq;` at the top, and after **each** `await` do `if (seq !== loadSeq) return;` so an older
  response can never overwrite a newer one.
- Error handling: the file's bottom-level `loadDashboard().catch(...)` sets `#total-brews` to `"!"`. For filter-triggered
  loads, use a separate wrapper `reloadFromFilter()` = `loadDashboard().catch((error) => showFilterMessage(error.message))`,
  and call that from `setupFilter()` listeners. Do not change the initial `loadDashboard().catch(...)` call.

**`setupFilter()` listeners**
- `change` on either date input: if both have a value and `from > to` (plain string comparison is correct for
  `YYYY-MM-DD`), call `showFilterMessage("'From' must not be after 'To'")` and **return without a request**
  (leave the previous numbers on screen). Otherwise call `reloadFromFilter()`.
- `click` on `#filter-clear`: set both input values to `""`, `showFilterMessage("")`, `reloadFromFilter()`.

**`renderTimeline(perDay)` — what happens with zero brews**
- Today: the function does `svg.innerHTML = ""` then `if (perDay.length === 0) return;`, so the chart area is just
  **blank**, with no bars and no explanation. (That early return is also what prevents `Math.max(...[])` = `-Infinity`
  from producing NaN bar heights; keep it.)
- New behaviour: still clear the svg and return early, but also show an explanation. Do **not** draw SVG `<text>`:
  the svg uses `preserveAspectRatio="none"`, which would stretch/squash the text. Instead:
  - In `index.html`, right after the `<svg id="timeline" ...></svg>`, add
    `<p id="timeline-empty" class="hint" hidden>No brews in this range</p>`.
  - At the top of `renderTimeline`, after clearing the svg, do
    `document.getElementById("timeline-empty").hidden = perDay.length > 0;`
    (so it is also hidden again when data comes back), then keep `if (perDay.length === 0) return;`.
- Other timeline notes: a 1-element `perDay` draws one full-width bar (fine). Days with no brews inside the range are
  simply absent from `per_day`, so bars are evenly spaced by *active* day, not by calendar day (known limitation,
  out of scope). The `<title>` tooltip text (`${day.day}: ${day.count} brews`) is unchanged.
- No CSS needed: the existing `.hint` rule in `style.css` sets no `display`, so the `hidden` attribute works.
  Optionally centre it (`text-align: center`) via `#timeline-empty`.

**`renderMachineCards(healths)` with an empty range** (no code change expected, but confirm in the browser)
- `m.last_brew` is `null` -> already renders "last never".
- `m.specialty` is `null` -> already renders "no specialty yet"; the percentage `count / m.brew_count` is inside the
  truthy branch, so there is no division by zero.
- `m.brew_count` shows `0 brews`.
- `last_maintenance` and `recent_errors` still show because the API doesn't filter them.

**Other rules**
- `submitForm` already ends with `await loadDashboard()`, so a newly logged brew refreshes the numbers using the
  current range. A brew logged outside the range legitimately doesn't change them.
- Don't add any external script/CSS (`test_frontend_has_no_external_resources` enforces this).

## 4. Edge cases checklist

| Case | Expected behaviour |
|---|---|
| No params | Identical to today's output (existing tests must still pass unchanged). |
| Only `from` / only `to` | Open-ended range on the other side. |
| `from == to` | Single day, whole day included (00:00:00 to 23:59:59). |
| Brew at `YYYY-MM-DD 23:59:59` on the `to` day | Included. Brew at 00:00:00 the next day: excluded. |
| Brew at `YYYY-MM-DD 00:00:00` on the `from` day | Included. Previous day 23:59:59: excluded. |
| Empty range (no brews) | `/api/stats`: `total_brews: 0`, `per_day: []`, `per_drink` still lists **all** drink types with `count: 0`. Machine: `brew_count: 0`, `last_brew: null`, `specialty: null`; maintenance fields still populated. UI: tile shows 0, the timeline svg is empty and the `#timeline-empty` paragraph "No brews in this range" is visible, bars all zero, cards show "never"/"no specialty yet". No JS errors. |
| `from` after `to` | API: HTTP 400 with detail `'from' must not be after 'to'`. UI: inline message, no request sent. |
| Malformed date (`2026-13-01`, `abc`, `2026-6-1x`) | API: HTTP 400 `invalid date ...`. |
| Empty param (`?from=`) | Treated as not provided. |
| Days with no brews inside the range | Not returned in `per_day` (not zero-filled); chart simply has fewer bars. Do not crash on a 1-element `per_day`. |
| Future dates | Allowed, empty result. |
| Unknown machine id with a valid range | Still 404. Bad dates on any machine id: 400. |
| SQL injection via `from`/`to` | Impossible if dates are validated by `strptime` and passed as `?` params. |

## 5. Tests to add

`tests/test_db.py` (fixture `conn` already exists; use `insert_brew(conn, machine_id, drink, "YYYY-MM-DD HH:MM:SS", dur, temp, "csv")` then `conn.commit()`):
- `test_stats_date_range_inclusive_bounds`: brews at `2026-06-01 00:00:00`, `2026-06-02 23:59:59`, `2026-05-31 23:59:59`,
  `2026-06-03 00:00:00`; range `2026-06-01`..`2026-06-02` counts exactly the middle two.
- `test_stats_from_only` and `test_stats_to_only`.
- `test_stats_single_day`.
- `test_stats_empty_range_keeps_all_drinks`: range with no brews -> `total_brews == 0`, `per_day == []`,
  `len(per_drink) == 6`, all counts 0.
- `test_stats_unfiltered_unchanged`: `get_stats(conn)` equals `get_stats(conn, None, None)`.
- `test_machine_health_range`: brew_count / last_brew / specialty honour the range; `last_maintenance` and
  `recent_errors` ignore it.
- `test_machine_health_empty_range`: count 0, last_brew None, specialty None.

`tests/test_api.py` (fixture `db` has brews on 2026-06-01 x2 and 2026-06-02 x1; use `request(app, "GET", url)` and `r.status`, `r.json()`):
- `GET /api/stats?from=2026-06-02&to=2026-06-02` -> total 1, per_day `{"2026-06-02": 1}`.
- `GET /api/stats?from=2026-06-03` -> total 0, per_day `[]`, per_drink length 6.
- `GET /api/stats?from=2026-06-05&to=2026-06-01` -> 400.
- `GET /api/stats?from=nope` -> 400.
- `GET /api/stats?from=` -> 200, same as no params.
- `GET /api/machines/1?from=2026-06-02` -> `brew_count == 0`; `GET /api/machines/1` -> `brew_count == 2`.
- `GET /api/machines/999?from=2026-06-01` -> 404.

## 6. Verification

1. `uv run pytest` — everything green, including all pre-existing tests and `test_frontend.py`.
2. Manual (needed: the frontend has no automated tests):
   - `uv run seed`, `uv run start`, open http://localhost:8123.
   - Pick a From and To that cover part of the data: tiles, drink bars, timeline and machine cards all change
     and agree with each other (sum of drink bars == total tile == sum of per-day counts).
   - Pick a range with no data (e.g. year 2030): no console errors, the timeline area shows the "No brews in this range" message instead of bars,
     drink bars all 0, machine cards show "never"/"no specialty yet", maintenance info still shown.
   - Set From later than To: inline message, no request in the network tab.
   - Click "All time": numbers return to the original totals.
   - Log a brew inside the range: numbers update and the range is preserved.
3. API spot checks with curl:
   - `curl "http://localhost:8123/api/stats?from=2026-06-01&to=2026-06-07"`
   - `curl -i "http://localhost:8123/api/stats?from=2026-06-07&to=2026-06-01"` -> `400`.
4. Optionally mention the new query params in `README.md` if it documents the API (it currently only lists commands,
   so likely no change).

## Files touched (summary)

- `src/brewops/db/queries.py` — `get_stats`, `get_machine_health`, new helpers.
- `src/brewops/api/main.py` — `stats`, `machine_health`, new `parse_date_range`.
- `src/brewops/frontend/index.html`, `style.css`, `app.js`.
- `tests/test_db.py`, `tests/test_api.py`.
