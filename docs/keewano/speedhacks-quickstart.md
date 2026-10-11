> For the complete documentation index, see [llms.txt](https://docs.keewano.com/llms.txt). Markdown versions of documentation pages are available by appending `.md` to page URLs; this page is available as [Markdown](https://docs.keewano.com/getting-started/speedhacks-quickstart.md).

# SpeedHacks Quickstart

Everything a SpeedHacks team needs to build on Keewano: what qualifies, setup in five minutes, sending events, and asking questions.

## What is Keewano

Keewano is an analytics database built for AI agents. You send small, typed events about anything (a user, a customer, an agent run, a device) with no schema or data modelling up front. Your agent then asks questions over those events in plain English through MCP, and gets answers in seconds, including about events you started sending minutes ago.

## Best Use of Keewano

A qualifying project ingests meaningful event data into Keewano and builds an agent or application that uses that data to reason, answer questions, give valuable insight, or power a meaningful part of the experience. Keewano should be an integral part of how the project works, not simply connected to the stack.

A quick test: if you can take Keewano out and the project still works the same, it doesn’t qualify.

**What the judge looks for:**

* **Keewano is load-bearing.** Events go in during the hackathon, and the answers change what the product says or does.
* **It works live.** The demo runs at least one live Keewano query.
* **A real bottleneck removed.** Bonus if you measure the before and after in Keewano itself.
* **It handles the unexpected.** The judge may ask one question you didn’t prepare, and your agent answers it from Keewano.

**What scores low:**

* Keewano used only to store data nobody queries.
* A dashboard as the end product.
* An agent whose decisions would be the same without the query.

## What you can build

The idea is yours, and there is no fixed list. Most strong projects built on Keewano follow one loop: something happens, an event goes to Keewano, an agent or app asks a question, and the answer changes what happens next. The three patterns below are common starting points, not a limit: combine them or go your own way.

| Pattern                           | What it does                                                                                                               | A question it asks Keewano                                                              | Fits for example                                                         |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **Ask-anything agent**            | Your product or agent sends events; an agent answers any question about them, writes reports, spots anomalies              | “Which users dropped off after the new signup step, and what did they do right before?” | Product analytics, ops monitoring, support insights, agent observability |
| **App that decides on live data** | Before acting, the app asks about this entity’s history or what is happening right now, and adapts                         | “Was this customer’s last order late, and have they ordered since?”                     | Personalization, offers, routing, ETAs, fraud flags, triage              |
| **Self-improving loop**           | An agent records its own actions and their outcomes, asks what worked, and changes its behaviour; then measures the change | “Which step of the flow causes most failures, and did yesterday’s fix reduce them?”     | Coding agents, voice or chat agents, onboarding flows, A/B tests         |

The self-improving loop is the easiest to fill with real data: point it at your own coding agent or your own app, and you generate events all day just by building.

## Get set up (about 5 minutes)

1. Go to <https://www.keewano.com/> and click the **Sign Up Free** button in the top-right corner, and create a project.
2. Open the chat in the bottom-left corner of the [Keewano platform](https://app.keewano.com/) (above the **Settings** button) and send `#SpeedHacks`. We enable full event access for your account: no limits, MCP access and team seats.

   ![The chat button in the bottom-left corner of the Keewano platform, above the Settings button](https://3698466984-files.gitbook.io/~/files/v0/b/gitbook-x-prod.appspot.com/o/spaces%2FWQi6lbHOA8tyhBBRtvuQ%2Fuploads%2Fgit-blob-fada03b8deb578df370829e9d930631d967d7cdc%2Fkeewano-help-chat-button.png?alt=media)
3. Create a key for sending events: **Settings → Project → API Keys**.
4. Create your personal Access Token for MCP: **Settings → Personal → Security**.
5. Connect your agent over MCP. In Claude Code:

   ```bash
   claude mcp add --scope user --transport http keewano \
     https://api.keewano.com/mcp --header "k-token: $KEEWANO_PAT"
   ```

   Any other MCP client: Streamable HTTP, URL `https://api.keewano.com/mcp`, header `k-token: <your Access Token>`.
6. Send events with an SDK ([Web](/sdks/web.md) · [Node](/sdks/nodejs.md) · [Python](/sdks/python.md) · [iOS](/sdks/ios.md) · [Android](/sdks/android.md) · [React Native](/sdks/react-native.md) · [Expo](/sdks/expo.md) · [Unity](/sdks/unity.md)) or the REST API, which takes events at `POST https://api.keewano.com/event/ingress/v1/json/in`. See [Sending events](#sending-events) below.
7. Give new events about 5 minutes to become queryable, then ask your first question, in your agent or in [Ask Keewano](/analytics/keewano-ask.md) in the web app: “How many users and events does my project have, and which event types does it record?”

## Sending events

An event is a name plus one typed value, attached to an entity. Keewano keeps each entity’s events in order, so every question can look at what came before.

**The entity is whoever the story is about.** It can be a person, a customer, an agent session, a call or a device. Its id must be a UUID (any version); over REST, send it as `installId`. Random UUIDs (v4) are fine. If you already have a string id like `machine-7`, derive the UUID from it with UUID v5, which always turns the same string into the same UUID: in Python, `uuid.uuid5(uuid.NAMESPACE_URL, "machine-7")`.

**Each event reads as Name(value).** The name says what happened; the value adds one detail. Every event type has one fixed value type:

| Value type       | Use it when the detail is…                   | Example event                   | Reads as                             |
| ---------------- | -------------------------------------------- | ------------------------------- | ------------------------------------ |
| `none`           | nothing; the event itself is the fact        | `SignedUp`                      | A user signed up                     |
| `string`         | a category or label                          | `PlanSelected("pro")`           | They picked the Pro plan             |
| `uint`           | a whole number: a count, duration or score   | `WaitSeconds(42)`               | They waited 42 seconds               |
| `bool`           | yes or no                                    | `PaymentSucceeded(false)`       | Their payment failed                 |
| `price_usd_cent` | money, in US cents                           | `OrderPlaced(1999)`             | They ordered for $19.99              |
| `timestamp`      | another moment in time, in Unix seconds      | `DeliveryScheduled(1791219600)` | Delivery is set for Oct 5, 17:00 UTC |
| x/y pair         | a position on a screen or map (0–65535 each) | `TappedAt(120, 48)`             | They tapped at x=120, y=48           |

**Need more than one detail?** Send the context first, as its own event: `PaymentMethod("card")`, then `OrderPlaced(1999)`. Keewano stores each entity’s events in order, and every event carries the latest value of every event before it. So a question about orders can also ask which payment method was in effect, with no join.

**Custom events are registered once** before you send them, each with an id (2000 and up), a name and a value type. The list is append-only: a registered event’s type can’t change, so add a new event with a new id instead.

**Events, not logs.** If you’d grep it, it’s a log; if you’d count it, it’s an event. Turn text into a category before sending, and keep raw text elsewhere under an id. String values are capped at 256 characters.

| Send this (event)                                 | Not this (log)                  |
| ------------------------------------------------- | ------------------------------- |
| `ToolFailed("Bash")` + `ErrorClass("type_error")` | The stack trace                 |
| `TicketOpened("late_delivery")`                   | The customer’s email            |
| `IntentDetected("reschedule")`                    | The call transcript             |
| `VibrationLevel(42)` every 10 seconds             | 60 raw sensor readings a second |

**No data yet? Generate history.** The REST API accepts a timestamp on every event, so a small script can backfill weeks of realistic history in minutes, then keep sending live events during your demo. Send each entity’s events in time order. The SDKs always stamp the current time, so backfill through REST:

```python
import json, time, uuid, zlib
from urllib.request import Request, urlopen

API = "https://api.keewano.com/event/ingress/v1/json"
API_KEY = "<your API key>"

def post(path, body):
    req = Request(f"{API}/{path}", data=json.dumps(body).encode(),
                  headers={"k-token": API_KEY, "Content-Type": "application/json"})
    with urlopen(req) as resp:  # raises HTTPError on a 4xx/5xx
        return json.load(resp)

# Your custom events. Append new ones; never change an existing one's type.
CUSTOM_EVENTS = [
    {"eventTypeId": 2000, "name": "PaymentMethod", "dataType": "string"},
    {"eventTypeId": 2001, "name": "OrderPlaced", "dataType": "price_usd_cent"},
]
# The hash names this exact list, so it changes whenever the list does
EVENTS_HASH = zlib.crc32(json.dumps(CUSTOM_EVENTS, sort_keys=True).encode()) or 1

# 1. Register the list (safe to repeat: an already registered list is a no-op)
print(post("custom", {"customEventsHash": EVENTS_HASH, "eventTypes": CUSTOM_EVENTS}))

# 2. Send one customer's events from a week ago, in time order
customer = str(uuid.uuid5(uuid.NAMESPACE_URL, "customer-42"))
t = int(time.time()) - 7 * 86400
print(post("in", {
    "dataSessionId": str(uuid.uuid4()),
    "batch": 1,
    "customEventsHash": EVENTS_HASH,
    "installId": customer,
    "events": [
        {"eventTypeId": 2000, "dataType": "string", "timestamp": t, "data": "card"},
        {"eventTypeId": 2001, "dataType": "price_usd_cent",
         "timestamp": t + 5, "data": 1999},
    ],
}))
```

Then hand it to your coding agent: “Rewrite this for my idea: a few hundred entities, two weeks of history, then keep sending live events.”

**Where events can come from:**

* Your own app, through an SDK (for example, the Web SDK tracks button clicks, page views and errors automatically).
* Your coding agent, through hooks (for example Claude Code’s `PreToolUse`, `PostToolUse`, `Stop`).
* Webhooks from tools you already use (voice agents, payments, Git hosting, support desks).
* A simulator you write, through REST.

## Asking questions

Let your agent ask in plain English over MCP; it picks the right tool and writes the query. Under the hood it has SQL queries, Lua script queries and a few other tools.

If you write queries yourself, the SQL looks like PostgreSQL but isn’t: it’s a read-only subset over a single events table, where every event type is its own column. See the [SQL guide](/sql-query-studio/guide.md) and [Lua guide](/lua-scripting/guide.md). The short version:

* **No JOINs needed:** every event already carries its entity’s context, so a JOIN elsewhere is a WHERE here.
* Use **subqueries** instead of CTEs (`WITH`).
* Use **`occurrence()`** instead of window functions.
* For logic that walks one entity’s events in order, use a **Lua script query**.

**Starter questions that work on any project:**

* “Walk me through entity X’s last three sessions.”
* “Of entities that hit event A, how many reached event B within a day, compared with those that didn’t?”
* “What happened in the 5 events before each failure?”
* “Since I started sending event C an hour ago, how does it relate to event D?”

## Before you demo

* [ ] Events reached Keewano during SpeedHacks (live, backfilled history, or both).
* [ ] Events are small and typed: no logs, transcripts or raw text.
* [ ] At least one live Keewano query runs during the demo.
* [ ] The answer changes what your agent or app does.
* [ ] Your Keewano project name is on your submission.

## Docs and help

* **SDK guides:** [Web](/sdks/web.md) · [Node](/sdks/nodejs.md) · [Python](/sdks/python.md) · [iOS](/sdks/ios.md) · [Android](/sdks/android.md) · [React Native](/sdks/react-native.md) · [Expo](/sdks/expo.md) · [Unity](/sdks/unity.md)
* **REST API reference:** [Swagger](https://api.keewano.com/event/ingress/v1/json/swagger)
* **Querying:** [MCP for agents](/analytics/mcp-for-agents.md) · [Ask Keewano](/analytics/keewano-ask.md) · [SQL reference](/sql-query-studio/reference.md) · [Lua reference](/lua-scripting/reference.md)
* **Onsite all day:** Vitaly Bukhovsky (Co-Founder & CPO, Keewano prize judge) and Karen Teboulle (VP Growth).
* **Online:** the chat in the bottom-left corner of the [Keewano platform](https://app.keewano.com/) (above the **Settings** button).


---

# Agent Instructions
This documentation is published with GitBook. GitBook is the documentation platform designed so that both humans and AI agents can read, navigate, and reason over technical content effectively. Learn more at gitbook.com.

## Querying This Documentation
If you need additional information that is not directly available in this page, you can query the documentation by asking a question.

Perform an HTTP GET request on the following URL with the `ask` and `goal` query parameters:

```
GET https://docs.keewano.com/getting-started/speedhacks-quickstart.md?ask=<question>&goal=<user_goal>
```

`ask` is the immediate question: it should be specific, self-contained, and written in natural language.
`goal` is what the user is ultimately trying to achieve, the reason they need the answer. Sharing it helps GitBook give you a better, more relevant answer. A goal is most helpful when it describes the outcome the user wants rather than restating the question. For example, with `ask=how do I create an API token`, a goal like `automate deployments from our CI pipeline` lets GitBook tailor the answer to that use case.

The response will contain a direct answer to the question and relevant excerpts and sources from the documentation.

Use this mechanism when the answer is not explicitly present in the current page, you need clarification or additional context, or you want to retrieve related documentation sections.
