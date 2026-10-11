> For the complete documentation index, see [llms.txt](https://docs.keewano.com/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.keewano.com/sql-query-studio/reference.md).

# SQL Reference

The full SQL surface: aggregates, predicates, time, timeline functions, and limits.

This is the lookup companion to the [SQL Query Studio Guide](/sql-query-studio/guide.md). It assumes the model the guide sets out — one `events` table, one row per event, and columns that hold their last value forward so a `WHERE` clause selects stretches of a user's timeline rather than single rows. If a query result ever surprises you, that rule is almost always why; the guide explains it in full.

***

## SELECT

### Projection

Selecting bare columns with no aggregate and no `GROUP BY` emits one row per occurrence of the driving columns:

```sql
SELECT button_click AS b, event_datetime AS t FROM events ORDER BY t
```

### DISTINCT

```sql
SELECT DISTINCT country FROM events
SELECT DISTINCT platform, country FROM events
```

`SELECT DISTINCT` deduplicates output rows after `HAVING` and before `ORDER BY`. It is equivalent to grouping by every selected column.

### Aggregate functions

| Function                                         | Notes                                                 |
| ------------------------------------------------ | ----------------------------------------------------- |
| `count(*)`                                       | Events inside the qualifying intervals.               |
| `count(col)`                                     | Occurrences of `col`. Never `NULL`; empty is 0.       |
| `count(DISTINCT col)`                            | Exact at dimension cardinality; see below.            |
| `count(DISTINCT user_id)`                        | Exact distinct users, at any scale.                   |
| `sum(col)`, `avg(col)`                           | `NULL` when no occurrence qualified.                  |
| `min(col)`, `max(col)`                           | `NULL` when no occurrence qualified.                  |
| `percentile_cont(f) WITHIN GROUP (ORDER BY col)` | `f` in 0..1. Estimated; see below.                    |
| `percentile_disc(f) WITHIN GROUP (ORDER BY col)` | As above.                                             |
| `variance(col)`, `var_samp`, `var_pop`           | Streaming Welford; `NULL` below the sample threshold. |
| `stddev(col)`, `stddev_samp`, `stddev_pop`       | As above.                                             |

`count(DISTINCT user_id)` is exact, always, at any scale — it is the number most reporting rests on, so it is counted directly rather than summarised. `count(DISTINCT col)` on any other column is exact at the cardinalities real dimensions reach (countries, platforms, placements, product ids, levels). Past a few hundred distinct values a column switches to a summary that stays within about 1.6% however large it grows. Percentiles land within about a percentage point of the true value, at any volume.

#### `count(*)` versus `count(col)`

```sql
-- 'all_events' counts every event in the interval;
-- 'clicks' counts only button_click occurrences.
SELECT count(*) AS all_events, count(button_click) AS clicks
FROM events
```

### FILTER

`FILTER (WHERE ...)` restricts one aggregate without restricting the rest of the query.

```sql
SELECT country,
       count(DISTINCT user_id)                                   AS users,
       count(DISTINCT user_id) FILTER (WHERE purchase_price > 0) AS payers
FROM events
GROUP BY country
```

#### Which clause restricts what

![Three clauses, three jobs: only WHERE changes what is scanned; FILTER and HAVING change what is reported](https://3698466984-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2FWQi6lbHOA8tyhBBRtvuQ%2Fuploads%2Fgit-blob-cbb92c6ea63002f8b93bd63dc13a18f4f454b471%2Fwhere-filter-having.png?alt=media)

{% hint style="success" %}
**`FILTER` has occurrence semantics, not interval semantics.**

A `WHERE` clause selects intervals. A `FILTER` predicate instead names **instants**: the occurrences whose payload matches. The aggregate then samples its argument's last-known value at exactly those instants.
{% endhint %}

```sql
-- how much gold players were holding at the moment they reached level 10
SELECT avg(total_balance('gold')) FILTER (WHERE level_up = 10) AS gold_at_level_10
FROM events
```

#### FILTER restrictions

* Supported aggregates: `count`, `sum`, `avg`, `min`, `max`, `variance`, `stddev`. Not `percentile`.
* A time window inside `FILTER` is rejected; put it in `WHERE`.
* At most 4 composite (`AND`/`OR`/`NOT`) `FILTER` conditions per query.
* Some columns cannot appear inside a composite `FILTER`; the error message names the alternative.

### Expressions over aggregates

Aggregates can be combined with arithmetic, `CASE`, and `COALESCE`.

```sql
SELECT sum(purchase_price) / count(DISTINCT user_id) AS arpu,
       100.0 * count(DISTINCT user_id) FILTER (WHERE purchase_price > 0)
             / count(DISTINCT user_id)                AS payer_pct
FROM events
```

#### CASE

```sql
SELECT CASE WHEN count(button_click) > 100 THEN 2
            WHEN count(button_click) > 10  THEN 1
            ELSE 0
       END AS engagement_tier,
       count(DISTINCT user_id) AS users
FROM events
```

The arms carry numbers, not text: no query here can build a string that was never recorded. Emit a numeric tier and let whatever renders the result put a name on it. A `NULL` condition is not true, so it takes the `ELSE` branch.

#### COALESCE

`COALESCE(a, b)` and `IFNULL(a, b)` return `b` when `a` is `NULL`. Up to 8 arguments.

```sql
SELECT COALESCE(sum(purchase_price), 0) AS revenue
FROM events
```

### Scalar functions

| Function                        | Notes                                       |
| ------------------------------- | ------------------------------------------- |
| `abs(x)`                        |                                             |
| `floor(x)`, `ceil(x)`           | `ceiling` is accepted too.                  |
| `trunc(x)`                      | Toward zero. No scale argument.             |
| `round(x)`                      | Nearest, halves to even. No scale argument. |
| `mod(a, b)`                     |                                             |
| `greatest(a, b)`, `least(a, b)` | Two arguments.                              |
| `nullif(a, b)`                  | `NULL` when equal.                          |
| `cast(x AS <type>)`             | See below.                                  |

Types accepted by `CAST`: `int`/`integer`/`int4`/`int8`/`bigint`/`smallint`, `double`/`float`/`float8`/`real`/`numeric`/`decimal`, `text`/`varchar`/`char`/`bpchar`, `bool`/`boolean`, `timestamp`/`timestamptz`/`date`/`time`. Casting an arithmetic expression is only allowed to a numeric type; `CAST(x AS INTEGER)` rounds to nearest with halves to even.

***

## WHERE

### Supported predicate shapes

A `WHERE` leaf compares one column against constants:

| Shape                     | Example                              |
| ------------------------- | ------------------------------------ |
| Comparison                | `country = 'US'`, `level >= 5`       |
| `IN` / `NOT IN`           | `country IN ('US', 'CA')`            |
| `BETWEEN` / `NOT BETWEEN` | `event_datetime BETWEEN 100 AND 200` |
| `LIKE` / `NOT LIKE`       | `country LIKE 'U%'`                  |
| `IS NULL` / `IS NOT NULL` | `exchange_from_item IS NULL`         |
| Occurrence ordinal        | `occurrence(purchase_time) <= 3`     |
| Per-user event count      | `count(button_click) > 10`           |

Leaves combine with `AND`, `OR`, and `NOT` to arbitrary depth (subject to [Capacities](#capacities)).

### Two predicates that are not about intervals

Two of the shapes above address the timeline rather than a column's value, and both are described under [Keewano-specific functions](#keewano-specific-functions):

* `occurrence(E) <cmp> N` picks events by their position in the user's lifetime.
* `count(E) <cmp> N` gates on the user's lifetime count and keeps or drops that user whole. It combines with `AND` only.

### LIKE

Wildcards are `%` for any run of characters and `_` for exactly one. A pattern may not match more than 64 distinct values in the column, and the query is refused if it does. The same ceiling applies to `IN`, and a query with several such conditions shares it between them — so the fix is a narrower pattern rather than a longer list.

***

## Working with text

Everything you would reach for on a dimension works: `=`, `<>`, `IN`, `NOT IN`, `LIKE`, `NOT LIKE`, `IS NULL`, `IS NOT NULL`, `GROUP BY`, and `count(DISTINCT ...)`.

What you cannot do is build new text: no concatenation, no substring, no upper/lower, and no ordering comparison between two labels. Those belong in whatever renders the result, where they are free. Comparisons are case-insensitive on the label, and a literal that matches no label returns nothing rather than raising an error:

```sql
SELECT count(*) AS n FROM events WHERE country = 'nonexistent'   -- returns 0, not an error
```

***

## Time

### Literals and intervals

```sql
WHERE event_datetime >= '2026-01-01'
WHERE event_datetime >= '2026-01-01 12:30:00'
WHERE event_datetime >= now() - INTERVAL '7 days'
WHERE event_datetime BETWEEN '2026-01-01' AND '2026-01-01' + INTERVAL '1 month'
```

`now()`, `current_timestamp`, and `current_date` are available. Interval units: seconds, minutes, hours, days, weeks, months, years.

### date\_trunc

`date_trunc(unit, <time column>)` rounds down to a boundary. Units: `hour`, `day`, `week`, `month`, `quarter`, `year`.

```sql
SELECT date_trunc('week', event_datetime) AS week, count(*) AS n FROM events
GROUP BY week ORDER BY week
```

Only one `date_trunc` time bucket may appear in a `GROUP BY`.

### extract

`EXTRACT(part FROM <time column>)` yields an integer calendar field. Parts: `year`, `quarter`, `month`, `day`, `hour`, `minute`, `second`, `dow` (0 = Sunday), `doy` (1-based), `epoch`.

```sql
SELECT EXTRACT(DOW FROM event_datetime) AS weekday, count(*) AS n
FROM events
GROUP BY weekday ORDER BY weekday
```

***

## GROUP BY, HAVING, ORDER BY, LIMIT

### GROUP BY

Group by output column name, by alias, or by 1-based position.

```sql
SELECT country AS c, count(*) AS n FROM events GROUP BY c
SELECT country,      count(*) AS n FROM events GROUP BY 1
```

Every selected non-aggregate column must appear in the `GROUP BY`. Maximum 8 grouping columns. Grouping by a constant is allowed and collapses to a single group.

### HAVING

`HAVING` tests the finished aggregates, and may reference output aliases.

```sql
SELECT country, count(button_click) AS n FROM events
GROUP BY country
HAVING n >= 2
```

Expressions are allowed: `HAVING (count(*) + 1) > 2`.

### ORDER BY and LIMIT

Order by output name, alias, position, or an aggregate expression. `ASC` / `DESC`, `LIMIT`, and `OFFSET` all behave as in PostgreSQL.

```sql
SELECT country, count(*) AS n FROM events
GROUP BY country ORDER BY count(*) DESC LIMIT 10 OFFSET 20
```

{% hint style="info" %}
**Order ties deliberately.** Rows that compare equal under `ORDER BY` can come back in any order, here and in PostgreSQL alike, since neither promises one. Add a tiebreaker column when a stable sequence matters.
{% endhint %}

***

## Subqueries

`FROM (SELECT ...) alias` is supported. The outer query runs over the inner result table, which makes two-stage aggregation possible: per-user first, then across users.

```sql
-- average events per user
SELECT avg(n) AS avg_events_per_user
FROM (SELECT user_id, count(*) AS n FROM events GROUP BY user_id) u
```

```sql
-- the busiest country's event count
SELECT max(t.n) AS m
FROM (SELECT country AS c, count(*) AS n FROM events GROUP BY c) t
```

Row-level expressions over the inner result are evaluated per output row:

```sql
SELECT CAST(TRUNC(EXTRACT(YEAR FROM q.d)) AS INTEGER) AS y, SUM(q.n) AS n
FROM (SELECT date_trunc('day', event_datetime) AS d, count(*) AS n FROM events GROUP BY d) q
GROUP BY y
```

Subquery nesting depth is bounded; the parser reports when it is exceeded.

***

## Keewano-specific functions

These have no PostgreSQL equivalent. They exist because the underlying data is a timeline, and each answers a question about position or accumulated state that a row-oriented dialect has no way to phrase.

### total\_balance('\<item>')

A virtual column carrying the user's running balance of one item at each point in their timeline. It is not stored anywhere — the engine reconstructs it by replaying every event pair in the stream that moves that item. Six pair streams feed it, and the item is looked up by label in each:

| Source               | Effect on the balance |
| -------------------- | --------------------- |
| Items exchanged away | subtract              |
| Items exchanged for  | add                   |
| Purchase grants      | add                   |
| Ad-reward grants     | add                   |
| Subscription grants  | add                   |
| Items reset          | set to this value     |

Because it is an ordinary column once derived, it composes with everything else:

```sql
-- the highest gold balance each user reached
SELECT max(total_balance('gold')) AS peak_gold
FROM events
```

```sql
-- how much gold players were holding when they hit the shop
SELECT avg(total_balance('gold')) FILTER (WHERE screen = 'shop') AS gold_at_shop
FROM events
```

The item name is matched case-insensitively; naming an item that is never moved is an unknown column rather than an empty result. The balance behaves like every other column — `count(total_balance('gold'))` is the number of times the balance changed, and `min`/`max` are the low and high water marks.

### occurrence(\<event>)

The 1-based lifetime ordinal of an event for that user: 1 at its first occurrence, 2 at the second, and so on. This is how you address "the first N" of something without a window function.

```sql
-- value of each user's first three purchases
SELECT sum(purchase_price) AS first_3_purchases_value
FROM events
WHERE occurrence(purchase_time) <= 3
```

```sql
-- everything after the tenth session
SELECT count(*) AS n
FROM events
WHERE occurrence(session_start) > 10
```

The ordinal counts over the user's whole lifetime, not within the query's time window, so it stays stable when you change the reporting period.

### count(\<event>) inside WHERE

In a `SELECT` list `count(E)` aggregates as usual. In a `WHERE` clause it means something different and useful: it tests the user's lifetime count of `E` and keeps or drops that user entirely. It is a cohort gate.

```sql
-- measure only users who came back at least five times
SELECT count(DISTINCT user_id) AS engaged, sum(purchase_price) AS revenue
FROM events
WHERE count(session_start) >= 5
```

Because it decides about a whole user rather than a stretch of timeline, it combines with `AND` only, not inside `OR` or `NOT`.

***

## Capacities

A single query can be this wide. Reaching one of these is unusual, and generally means two questions have been folded into one.

| Per query                                  | Up to |
| ------------------------------------------ | ----- |
| Selected columns                           | 16    |
| Aggregates per query                       | 16    |
| Distinct event columns referenced          | 16    |
| `GROUP BY` columns                         | 8     |
| `COALESCE` arguments                       | 8     |
| Composite `FILTER` conditions              | 4     |
| Scope closing events per scope             | 8     |
| Chain members / boundaries per family      | 8     |
| Values matched by `LIKE` and `IN` together | 64    |

Exceeding a limit is always an explicit error, never a silent truncation.

***

## Troubleshooting

Results that look wrong without saying so, and the errors whose obvious fix is the wrong one. Anything not listed here says what to do in the message itself.

<details>

<summary>A group you expected is missing, or an unexpected empty group appears</summary>

Users who never emitted the grouping column form a `NULL` group rather than vanishing. Add `AND <col> IS NOT NULL` to exclude them, and remember that `<col> <> 'x'` does **not** include them, because `NULL` fails every comparison.

</details>

<details>

<summary>Counts are higher than expected after adding a dimension</summary>

`count(*)` counts all events in the qualifying intervals, not just events of the dimension. If you want occurrences of a specific event, use `count(<that event>)`.

</details>

<details>

<summary>"Arithmetic in a condition is not supported"</summary>

Comparisons in `WHERE` must be column-against-constant. Rewrite the arithmetic onto the constant side, or push the expression into a subquery and filter the outer query.

```sql
-- rejected
SELECT count(*) AS n FROM events WHERE level + 1 > 6
```

```sql
-- accepted
SELECT count(*) AS n FROM events WHERE level > 5
```

</details>

<details>

<summary>count() conditions apply to the whole user</summary>

The message says it cannot sit inside `OR` or `NOT`. The way around: run each branch as its own query, or move the condition to `HAVING` over a subquery grouped by user.

</details>

<details>

<summary>LIKE matches too many values</summary>

The pattern matched more than 64 distinct values in that column. `IN` shares the same budget, so a longer list will not help — narrow the pattern instead, or split the work across two queries.

</details>

<details>

<summary>Results differ between identical runs</summary>

Check whether the difference is only the order of rows that tie under `ORDER BY`. Neither Keewano nor PostgreSQL promises an order for rows that compare equal; add a tiebreaker column and the sequence becomes stable.

</details>


---

# Agent Instructions
This documentation is published with GitBook. GitBook is the documentation platform designed so that both humans and AI agents can read, navigate, and reason over technical content effectively. Learn more at gitbook.com.

## Querying This Documentation
If you need additional information that is not directly available in this page, you can query the documentation by asking a question.

Perform an HTTP GET request on the following URL with the `ask` and `goal` query parameters:

```
GET https://docs.keewano.com/sql-query-studio/reference.md?ask=<question>&goal=<user_goal>
```

`ask` is the immediate question: it should be specific, self-contained, and written in natural language.
`goal` is what the user is ultimately trying to achieve, the reason they need the answer. Sharing it helps GitBook give you a better, more relevant answer. A goal is most helpful when it describes the outcome the user wants rather than restating the question. For example, with `ask=how do I create an API token`, a goal like `automate deployments from our CI pipeline` lets GitBook tailor the answer to that use case.

The response will contain a direct answer to the question and relevant excerpts and sources from the documentation.

Use this mechanism when the answer is not explicitly present in the current page, you need clarification or additional context, or you want to retrieve related documentation sections.
