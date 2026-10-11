// Pure mapping from Alpine API payloads (/runs, /report, /decisions) to BuildPipeline runs.
// No React, no fetch: unit-testable with node.
import type { PipelineLog, PipelineRun, PipelineStage } from "./components/ui/build-pipeline"

export interface KeewanoRun { run_id: string; scenario: string; sim?: string; created: string; totals?: Record<string, number>; policy?: Record<string, unknown> }
export interface Metric { name: string; value: number; unit?: string; query?: string }
export interface Report { source?: string; cached?: boolean; computed_at?: string; run_id?: string; metrics?: Metric[]; error?: string }
export interface DecisionDay { day: number; date?: string; questions?: { question: string; query?: string; answer: unknown }[]; reason?: string; changed?: boolean }
export interface Decisions { detail?: { days?: DecisionDay[] } | null }
/** Per-run lazy state: undefined = not requested yet. */
export interface RunDetail { loading?: boolean; report?: Report | null; reportError?: string; decisions?: Decisions | null }

/** Alpine keewano runs, newest first; all keewano runs if none are tagged sim=alpine. */
export function pickRuns(runsPayload: { keewano_runs?: KeewanoRun[] } | null | undefined): KeewanoRun[] {
  const kw = runsPayload?.keewano_runs ?? []
  const alpine = kw.filter(r => r.sim === "alpine")
  const list = alpine.length ? alpine : kw
  return [...list].sort((a, b) => String(b.created).localeCompare(String(a.created)))
}

export const isLive = (r?: Report | null) => !!r && r.source === "keewano"

const fmt = (v: number | undefined, d: number) => v == null || !Number.isFinite(v) ? "n/a" : v.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })

/** HH:MM:SS (UTC) of `created` plus `offset` seconds; plain sequential clock if created is unparseable. */
export function clock(created: string, offset: number): string {
  const t = new Date(created).getTime()
  const d = new Date((Number.isNaN(t) ? 0 : t) + offset * 1000)
  return d.toISOString().slice(11, 19)
}

function totalsLogs(t: Record<string, number>, created: string): PipelineLog[] {
  const water = t.water_mgal ?? (t.water_gal != null ? t.water_gal / 1e6 : undefined)
  const heat = t.heat_exported_gwh ?? (t.heat_sold_mwh != null ? t.heat_sold_mwh / 1000 : undefined)
  const cost = t.cost_musd ?? (t.cost_usd != null ? t.cost_usd / 1e6 : undefined)
  const lines = [
    "8,760 hours simulated",
    `PUE ${fmt(t.pue, 3)}`,
    `WUE ${fmt(t.wue_l_per_kwh, 3)} L/kWh`,
    `ERF ${fmt(t.erf, 3)}`,
    `water ${fmt(water, 2)} Mgal`,
    `heat sold ${fmt(heat, 1)} GWh`,
  ]
  if (cost != null) lines.push(`cost $${fmt(cost, 1)}M`)
  return lines.map((message, i) => ({ time: clock(created, i), message }))
}

const ans = (a: unknown) => typeof a === "number" ? a.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(a)

export function mapRun(r: KeewanoRun, detail?: RunDetail): PipelineRun {
  const created = r.created
  const loaded = !!detail && detail.report !== undefined // keeps old data visible during a re-query
  const report = detail?.report
  const live = isLive(report)
  let s = 10 // synthetic seconds after `created` for later stages
  const at = () => clock(created, s++)

  const simulate: PipelineStage = { id: "simulate", name: "Simulate year", kind: "build", status: "passed", logs: totalsLogs(r.totals ?? {}, created) }

  const stream: PipelineStage = { id: "stream", name: "Stream to Keewano", kind: "checkout", status: "queued", logs: [] }
  if (loaded) {
    if (live) {
      stream.status = "passed"
      stream.logs = [{ time: at(), message: `events for ${r.run_id} queryable in Keewano` }]
    } else {
      stream.status = "failed"
      stream.logs = [{ time: at(), level: "error", message: `${r.run_id} isn't queryable in Keewano yet${detail?.reportError ? ` (${detail.reportError})` : ""}` }]
    }
  }

  const agent: PipelineStage = { id: "agent", name: "Agent decides", kind: "test", status: r.scenario === "agent" ? "queued" : "skipped", logs: [] }
  if (r.scenario === "agent" && loaded) {
    const days = detail?.decisions?.detail?.days ?? []
    const changed = days.filter(d => d.changed)
    agent.status = detail?.decisions ? "passed" : "failed"
    if (!detail?.decisions) agent.logs.push({ time: at(), level: "error", message: "no decision log for this run" })
    for (const d of changed) agent.logs.push({ time: at(), message: `${d.date ?? `day ${d.day}`} · ${d.reason ?? ""}` })
    for (const q of (changed[0]?.questions ?? []).slice(0, 3)) agent.logs.push({ time: at(), message: `${q.question} → ${ans(q.answer)}` })
    if (detail?.decisions && !changed.length) agent.logs.push({ time: at(), message: `${days.length} days checked, policy never changed` })
  }

  const deploy: PipelineStage = { id: "report", name: "Live report", kind: "deploy", status: "queued", logs: [] }
  if (loaded && live) {
    deploy.status = "passed"
    if (report?.cached) deploy.logs.push({ time: at(), level: "warning", message: `cached from ${report.computed_at ?? "an earlier query"}` })
    for (const m of report?.metrics ?? []) deploy.logs.push({ time: at(), message: `${m.name} ${fmt(m.value, 3)} · ${m.query ?? ""}` })
  }

  const commit = r.run_id.split("-").pop() ?? r.run_id
  return { id: r.run_id, branch: r.scenario, commit, startedAt: created, stages: [simulate, stream, agent, deploy] }
}

export const mapRuns = (runs: KeewanoRun[], details: Record<string, RunDetail>) => runs.map(r => mapRun(r, details[r.run_id]))
