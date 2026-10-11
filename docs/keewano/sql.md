> For the complete documentation index, see [llms.txt](https://docs.keewano.com/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.keewano.com/sql-query-studio/guide.md).

# SQL Guide

Querying per-user event streams in PostgreSQL-compatible SQL.

## Overview

Keewano holds every event each of your users has ever fired, in order, and queries it whole — no cubes, no nightly rollups, no pre-aggregation, and no pipeline standing between the question and the data. You ask, and it reads the actual histories of millions of users to answer.

There is no schema to design and nothing to anticipate. Every event sits beside every other event in the same timeline, in order, so anything your product records can be measured against anything else it records, across all of your history, the moment someone thinks to ask.

You reach all of it in SQL. Keewano speaks the PostgreSQL wire protocol, so Tableau, Metabase, Looker, psql, and any JDBC or ODBC client connect straight to it and see a single `events` table. Nothing to install, nothing to export.

The syntax is Postgres. The semantics are the interesting part — that is what [The mental model](#the-mental-model) covers, and everything else in this reference follows from it.

#### Who this is for

* Analysts writing queries directly or through a BI tool (Tableau, Metabase, Looker).
* Developers embedding queries in dashboards and services.

#### Two ways to ask

A Keewano query is a walk along user timelines rather than a scan of tables, and there are two ways to write that walk.

**Lua** is the native interface. It hands you a user's whole timeline and you say what you want from it in the database's own terms — what happened, in what order, how long apart, what state they were in when it did. It reaches the full range of the engine, including things that have no name in SQL at all.

**SQL** is the familiar one, and it is what this document covers. Every BI tool already speaks it, so Tableau or Metabase points straight at your data with nothing in between. It covers the reporting core thoroughly — counts, sums, rates, revenue, retention, cohorts, breakdowns by any dimension, time series, distributions — and this dialect adds timeline functions that stretch it well past ordinary SQL.

{% hint style="info" %}
Use whichever suits the question, and pick up Lua early rather than late. A few hours with it open up ways of working with your data that SQL gives you no way to express.
{% endhint %}

***

## The mental model

This is the whole idea in one section. Read it once and the rest of the reference reads as consequences of it.

### One table, one row per event

There is one queryable relation, `events`, and it holds everything: every event from every user, already in order. That single table is the whole database.

```sql
SELECT count(*) AS n FROM events
```

Always write `FROM events`. Everything a project records lives in this one relation, already correlated by user and ordered in time. One row is one event occurrence in one user's timeline — a user who fired 400 events contributes 400 rows.

### Columns are step functions, not cells

Most of what you want to know about a user is not a property of any single event. Their country, their platform, the level they are on, the screen they have open — these become true at some moment and stay true until something changes them. What an event records is the moment of change.

So a column reads accordingly: it takes a value when its event occurs and holds that value forward until the next occurrence. A `country` event at position 0 gives every event after it a country, even though only one of them carries the payload.

![A column holds its value forward until the next event changes it](https://3698466984-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2FWQi6lbHOA8tyhBBRtvuQ%2Fuploads%2Fgit-blob-84052af3b104c116315878d53f330c5a5f30a025%2Fcolumn-step-function.png?alt=media)

{% hint style="success" %}
A predicate does not pick rows one at a time; it picks **stretches of the timeline**, and the aggregates then measure whatever falls inside them. Every behaviour in this dialect follows from that one rule.
{% endhint %}

Because a value holds until it changes, every event carries the full context of everything before it. You can ask for revenue during the stretch where the country was US, or the gold balance at the instant a level was cleared, and neither needed a country recorded on the purchase event nor a balance recorded on the level event. A warehouse cannot do that unless it saw the question coming; here every combination is already there, so a question nobody anticipated is just another query.

#### A user can land in more than one group

A value that holds forward can also change, and when it does the user has genuinely been in both states. Group by such a column and they are counted under each:

```sql
SELECT country, count(DISTINCT user_id) AS users
FROM events
GROUP BY country
```

Someone who played from the US and later from Canada appears under both, so the column of user counts sums to more than your total user count. That is the honest answer to "how many users did we serve in each country." It is not what you want if you are trying to split users into buckets.

To count each user once, pin the query to a single moment in their timeline. Their first value is occurrence of 1:

```sql
SELECT country, count(DISTINCT user_id) AS users
FROM events
WHERE occurrence(country) = 1
GROUP BY country
```

Now every user falls in exactly one group — the one they started in. There is no matching way to ask for someone's latest value in SQL, since occurrence counts forward and the last position differs per user; that question belongs in Lua, which sees the whole timeline at once.

#### NULL before the first occurrence

Before a column's first occurrence its value is unknown, and SQL `NULL` semantics apply: no comparison matches. If the first `country` event lands at position 12, then positions 0–11 match neither `country = 'US'` nor `country <> 'US'` — they do match `country IS NULL`.

This is the usual cause of a "missing" group: users who never emitted the dimension form a `NULL` group rather than disappearing. Add `AND <col> IS NOT NULL` if you want them excluded.

### Predicates compose as interval algebra

Boolean operators combine interval lists directly:

| SQL   | Interval operation                                    |
| ----- | ----------------------------------------------------- |
| `AND` | Intersection                                          |
| `OR`  | Union                                                 |
| `NOT` | Complement over the timeline, including the NULL head |

So `WHERE country = 'US' AND platform = 'ios'` means "the stretches of this user's timeline during which the last-known country was US and the last-known platform was iOS."

### What the aggregates measure

Aggregates measure whatever falls inside the stretches `WHERE` selected. `count(*)` is how many events that is; `count(x)` is how many of them were `x` events; and `sum(x)`, `min(x)`, `max(x)`, `avg(x)` work on those events' values. One asymmetry is worth knowing:

* `count(*)` counts events of **any kind** in the interval.
* `count(country)` counts `country` **events** — the number of times the value changed, not the number of events that "had" a country.

### Everything is already together

In a warehouse you join because facts and attributes were stored in separate tables and have to be stitched back together. Here they were never separated: they sit in the same stream, already correlated by user and ordered in time. The step-function rule is the join, applied for you.

So a question that would need a join elsewhere is answered by a `WHERE` clause here. "Revenue while the user was on the shop screen" is:

```sql
SELECT sum(purchase_price) AS s FROM events WHERE screen = 'shop'
```

To tie together two attributes of the same record — this purchase's product and this purchase's price — record families bind them within one record span instead of across last-known state. They are covered under [Your data](#your-data).

***

## Your data

Every project's columns are different, because a column is an event your product records. There is no fixed schema to learn: if your project emits an event called `Level Complete`, then `Level Complete` is a column, and nobody had to declare it. So the first thing to do is not to write a query — it is to look at what your project actually has.

### Connect

There are two ways to run SQL against your project.

#### In Keewano

The built-in **SQL Editor** needs no setup. Your project's full event list sits in the sidebar, several queries can be open at once as tabs, and any query can be named and saved to come back to.

Results appear as a table or as a chart — switch to the **Visualization** tab, pick a chart type, and set the axes, labels, and legend.

![The Keewano SQL Editor: saved queries and the event list on the left, the query above, results and visualization below](https://3698466984-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2FWQi6lbHOA8tyhBBRtvuQ%2Fuploads%2Fgit-blob-9b354a048499187d23cb74c6200bed114bada0ad%2Fsql-editor.png?alt=media)

The **Events** list on the left is the column list this guide keeps telling you to read — every event your project records, ready to drop into a query.

#### From your own client

Keewano presents itself as a PostgreSQL server, so there is nothing specific to install or configure. Point any Postgres-compatible client at the connection details below and it connects the way it would to any Postgres database.

Tableau, Metabase, Looker, psql, DBeaver, and any JDBC or ODBC driver all work. Once connected you will see a single `events` table carrying your project's columns, and autocomplete and schema browsing behave as usual.

**Connection**

* **Host:** `pgsql.keewano.com`, port `27543`
* **User:** the email address you were signed up to Keewano with (`you@company.com`)
* **Password:** a personal access token (`kwpat-…`), created under [**Settings → Access Token**](https://app.keewano.com/settings/personal/security) — the same token the [MCP server](/analytics/mcp-for-agents.md) uses
* **Database:** the project **UUID**, not the project name — under **Settings → Details → Project ID**
* **TLS is required:** `sslmode=require` or stronger

```bash
psql "host=pgsql.keewano.com port=27543 dbname=<project-uuid> user=<you@company.com> sslmode=require"
```

**Good to know**

* Data lives in the `events` table. It is an analytics engine speaking the Postgres protocol, not full Postgres, so some SQL behaves differently — see [Coming from PostgreSQL](#coming-from-postgresql).
* **Limits:** 16 KB per statement, 50 MB per result, roughly 2 minutes per query.
* You only see projects your user has access to.
* Repeated failed logins are throttled. If a client keeps retrying a wrong token, fix the configuration rather than retrying.

### See your columns

In psql:

```sql
\d events
```

In a BI tool, open the `events` table and read the field list. Either way you get every column your project has, with its type.

{% hint style="warning" %}
Read that list before going further. The examples in this document use names like `country`, `purchase_price`, and `level` because a document has to be concrete, but they come from an imaginary project. Yours will not match — a query naming a column you do not have fails with an unknown column. Find the equivalent in your own list and use that.
{% endhint %}

#### Quote names that are not plain lowercase

Your column names are your event names, written exactly as your product records them — usually with capitals and spaces: `Ad Revenue Placement`, `AB Test Assignment`, `Session Start`. SQL needs those in double quotes.

```sql
SELECT "Ad Revenue Placement" AS placement, count(*) AS n
FROM events
GROUP BY placement
ORDER BY n DESC
```

Without the quotes the parser reads `Ad` as the whole name and stops at the next word. Alias the column to something plain, as above, and the rest of the query stays readable. Generated columns are the exception: family fields like `ad_revenue_placement` and `purchase_price` are lowercase with underscores and need no quoting.

#### The four columns every project has

These exist in every project, spelled exactly like this:

| Column                    | Type        | Meaning                                                   |
| ------------------------- | ----------- | --------------------------------------------------------- |
| `user_id`                 | `text`      | The user's identifier. Constant within one user's stream. |
| `event_datetime`          | `timestamp` | When the current event happened.                          |
| `user_registration_time`  | `timestamp` | When the user first appeared. Constant per user.          |
| `user_last_activity_time` | `timestamp` | When the user was last seen. Constant per user.           |

A query built from only these four runs on any project, which is where [Asking questions](#asking-questions) begins. The registration and activity columns are also the cheapest of all to filter on: when a question concerns a cohort or a recent window, saying so on one of them narrows the query faster than any other predicate can.

### Value types

| Type        | Notes                                                                                                 |
| ----------- | ----------------------------------------------------------------------------------------------------- |
| `integer`   | Unsigned 32-bit payloads.                                                                             |
| `double`    | Money. Stored as cents or as 32-bit floats; rendered as a double.                                     |
| `text`      | See [Working with text](/sql-query-studio/reference.md#working-with-text) for which operations apply. |
| `bool`      | `TRUE` / `FALSE`.                                                                                     |
| `timestamp` | Epoch seconds, UTC.                                                                                   |

{% hint style="info" %}
All times are UTC. There is no session time zone. Timestamps are epoch seconds, and `date_trunc`, `extract`, and date literals all work in UTC. If a BI tool shows a day boundary shifted, the shift is being applied by the tool, not here.
{% endhint %}

### Record families

Some things are naturally records with several attributes: a purchase has a product, a price, and a time, emitted as consecutive events. Reading them as three independent step functions would let one purchase's product pair with another's price.

Record families fix this. Their columns are **record-scoped**: within one record's span all of that record's attributes hold simultaneously, so a conjunction binds inside a single record. The next anchor closes the previous span.

![Each record's fields hold together — the next anchor closes the span](https://3698466984-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2FWQi6lbHOA8tyhBBRtvuQ%2Fuploads%2Fgit-blob-e2bb8d8642fc3db4c2594df0f326f44ec319e3b8%2Frecord-families.png?alt=media)

So `purchase_product = 'starter_pack' AND purchase_price > 1000` matches nothing when those two values belong to different records — whereas plain last-known columns would have matched, pairing record 1's product with record 2's price. Bound correctly to one purchase:

```sql
-- revenue for one product, correctly bound to the same purchase
SELECT sum(purchase_price) AS s
FROM events
WHERE purchase_product = 'starter_pack'
```

Built-in families:

| Family         | Columns                                                                                                                        |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `purchase`     | `purchase_product`, `purchase_price`, `purchase_time`, `purchase_instance`                                                     |
| `ad_revenue`   | `ad_revenue_placement`, `ad_revenue_usd`, `ad_revenue_time`, `ad_revenue_instance`                                             |
| `subscription` | `subscription_package`, `subscription_usd`, `subscription_time`, `subscription_instance`                                       |
| `exchange`     | `exchange_location`, `exchange_from_item`, `exchange_from_count`, `exchange_to_item`, `exchange_to_count`, `exchange_instance` |

The naming is `<family>_<field>` with an underscore, never a dot, because a dotted name makes BI clients read the leading segment as a table qualifier.

#### The `_instance` field

`<family>_instance` is the 1-based ordinal of the record for that user — the Nth purchase, the Nth exchange. Select it alongside a record's other fields and it labels which record each projected row came from.

```sql
SELECT purchase_instance, purchase_product, purchase_price
FROM events
```

It is a projection column: it cannot be a `GROUP BY` key or a `WHERE` predicate. To work by position within a user's lifetime, use `occurrence()` instead.

#### Item lists

The `exchange` family also generates one column per item that gets exchanged — `exchange_from_<item>` and `exchange_to_<item>` — whose value is that item's count within the record.

```sql
SELECT sum(exchange_from_gold) AS gold_spent
FROM events
```

### Scopes

A scope is a named state that one event opens and any of several events close: a screen being open, a level in progress, a tutorial under way. While the scope is open its column carries the opening event's payload; outside it the column is `NULL`. The closing event still counts as inside.

Scopes are part of your project's configuration rather than something written in a query, so they arrive as ordinary columns already in your column list. Using one is like any other column:

```sql
SELECT screen, count(*) AS n FROM events WHERE screen IS NOT NULL GROUP BY screen
```

To add a scope or a custom record family, or to change which events open and close one, ask your Keewano contact. Both then appear as ordinary columns and behave like any other.

***

## Asking questions

These run on any project as written, because they use only the four columns everyone has. Run them first to see the shape of an answer, then swap in your own columns.

#### How much is there

```sql
SELECT count(DISTINCT user_id) AS users,
       count(*)                AS events
FROM events
```

#### Activity over time

```sql
SELECT date_trunc('day', event_datetime) AS day,
       count(DISTINCT user_id)       AS active_users
FROM events
WHERE event_datetime >= now() - INTERVAL '30 days'
GROUP BY day
ORDER BY day
```

Change `'day'` to `'week'` or `'month'` for a coarser series.

#### A cohort

`user_registration_time` is constant per user, so bounding it selects a group of users rather than a slice of their activity. This is also the cheapest way to narrow a query.

```sql
SELECT date_trunc('month', user_registration_time) AS joined,
       count(DISTINCT user_id)                     AS users
FROM events
GROUP BY joined
ORDER BY joined
```

#### Now with your own columns

Everything past this point needs a name from your own column list. Pick a dimension — an event whose payload describes the user rather than counts something — and group by it:

```sql
SELECT <your dimension>, count(DISTINCT user_id) AS users
FROM events
GROUP BY <your dimension>
ORDER BY users DESC
```

An event whose payload is a price can be summed. Both numbers below come out of a single pass, from two different events in the same stream, with nothing joined:

```sql
SELECT <your dimension>,
       count(DISTINCT user_id) AS users,
       sum(<your money event>) AS revenue
FROM events
GROUP BY <your dimension>
ORDER BY revenue DESC
```

#### Asking about part of a timeline

A `WHERE` on a dimension does not throw away users. It selects the stretches of each user's timeline where that dimension held, and everything else is measured inside those stretches. So this reads as "revenue earned while the user's country was known to be US":

```sql
SELECT sum(<your money event>) AS revenue
FROM events
WHERE <your dimension> = '<a value from that column>'
```

{% hint style="warning" %}
If that sentence is surprising, re-read [The mental model](#the-mental-model) before going further. Every other behaviour here follows from it.
{% endhint %}

#### Measuring one thing differently from the rest

`FILTER` narrows a single aggregate without touching the others, which is how a ratio and its denominator come out of one query:

```sql
SELECT count(DISTINCT user_id)                                       AS users,
       count(DISTINCT user_id) FILTER (WHERE <your money event> > 0) AS payers
FROM events
```

***

## Coming from PostgreSQL

The syntax is PostgreSQL's, so most of what you already write transfers unchanged. What changes is that the data arrives already correlated by the user and already in order — work a warehouse redoes on every query. Behavioural questions that need window functions, self-joins, or staged CTEs elsewhere are usually a single clause here.

#### Nothing has to be modelled in advance

A warehouse answers well whatever its schema anticipated; anything else means re-modelling, a backfill, and waiting. Here that decision never has to be made — every event a project records is automatically available as state alongside every other event, so a question nobody planned for is answerable on today's data and on all the history behind it, immediately.

| To ask for                                              | In a warehouse                                                 | Here                                   |
| ------------------------------------------------------- | -------------------------------------------------------------- | -------------------------------------- |
| An attribute nobody thought to attach to this event     | Re-model the table, backfill the history, wait                 | Name the column                        |
| A state that is only recorded when it changes           | A window carrying the last value forward, partitioned per user | Name the column                        |
| A metric's value at the instant something else happened | A lateral join or a correlated subquery                        | `FILTER (WHERE ...)`                   |
| Each user's first three purchases                       | `ROW_NUMBER() OVER (PARTITION BY user_id ...)`, then filter    | `WHERE occurrence(purchase_time) <= 3` |
| Only users with five or more sessions                   | Aggregate per user in a CTE, then join back to filter          | `WHERE count(session_start) >= 5`      |
| A running item balance                                  | A window sum over every grant and spend, per item, per user    | `total_balance('gold')`                |

#### Words that carry a little more meaning

Nothing here contradicts SQL; these words simply have more to describe when the data is a timeline.

| Word           | Here                                                                          |
| -------------- | ----------------------------------------------------------------------------- |
| A row          | One event occurrence in one user's timeline                                   |
| A column value | The last-known value, carried forward until the column changes again          |
| `WHERE`        | Selects the stretches of a timeline where the condition holds                 |
| `FILTER`       | Selects the instants an aggregate samples at                                  |
| `NULL`         | Absence, and also the stretch before a column has appeared for the first time |

#### Syntax you will not need here

Much of PostgreSQL's surface exists to reassemble data that was split across tables, or to write to it. Neither applies, so this dialect leaves it out and gives you the direct route instead.

| PostgreSQL syntax                            | Here                                                                                             |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `JOIN`                                       | Nothing to rejoin — everything is already together                                               |
| Window functions (`OVER`)                    | `occurrence()` and `<family>_instance` give per-user ordinals                                    |
| CTEs (`WITH`)                                | `FROM (SELECT ...) alias` subqueries                                                             |
| `INSERT` / `UPDATE` / `DELETE` / DDL         | The engine only reads; writing is the ingestion SDK's job                                        |
| `UNION`, `INTERSECT`, `EXCEPT`               | Run the branches as separate queries                                                             |
| Correlated subqueries                        | A subquery plus a `GROUP BY` usually says the same thing                                         |
| String functions, concatenation, `<` on text | See [Working with text](/sql-query-studio/reference.md#working-with-text) for what text supports |
| `round(x, n)` / `trunc(x, n)` with a scale   | The one-argument forms; scale arguments are rejected                                             |
| `generate_series`, set-returning functions   | Generate the series in the tool consuming the result                                             |
| Arithmetic on a column inside `WHERE`        | Compare the column directly, or push it into a subquery                                          |

***

## Where to go next

This guide covers the model and the day-to-day reporting core. When you need the exact surface — every aggregate and its behaviour, the predicate shapes `WHERE` accepts, time handling, the timeline functions with no PostgreSQL equivalent, and the per-query limits — reach for the companion document.

[**SQL Query Studio — Reference**](/sql-query-studio/reference.md) is the lookup companion to this guide: the full SQL surface, organised for jumping straight to what you need. For questions this SQL cannot phrase — anything about position, order, or accumulated state across a whole timeline — Lua is the native interface, and a few hours with it are well spent.


---

# Agent Instructions
This documentation is published with GitBook. GitBook is the documentation platform designed so that both humans and AI agents can read, navigate, and reason over technical content effectively. Learn more at gitbook.com.

## Querying This Documentation
If you need additional information that is not directly available in this page, you can query the documentation by asking a question.

Perform an HTTP GET request on the following URL with the `ask` and `goal` query parameters:

```
GET https://docs.keewano.com/sql-query-studio/guide.md?ask=<question>&goal=<user_goal>
```

`ask` is the immediate question: it should be specific, self-contained, and written in natural language.
`goal` is what the user is ultimately trying to achieve, the reason they need the answer. Sharing it helps GitBook give you a better, more relevant answer. A goal is most helpful when it describes the outcome the user wants rather than restating the question. For example, with `ask=how do I create an API token`, a goal like `automate deployments from our CI pipeline` lets GitBook tailor the answer to that use case.

The response will contain a direct answer to the question and relevant excerpts and sources from the documentation.

Use this mechanism when the answer is not explicitly present in the current page, you need clarification or additional context, or you want to retrieve related documentation sections.
