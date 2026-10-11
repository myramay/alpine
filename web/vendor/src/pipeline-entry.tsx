import * as React from "react"
import { createRoot, Root } from "react-dom/client"
import BuildPipeline, { PipelineRun } from "./components/ui/build-pipeline"
import { KeewanoRun, RunDetail, mapRuns, pickRuns } from "./pipeline-map"

interface Opts { apiBase: string }

async function getJSON(url: string) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 160)}`)
  return r.json()
}

function AlpinePipeline({ apiBase }: Opts) {
  const base = apiBase.replace(/\/$/, "")
  const [runs, setRuns] = React.useState<KeewanoRun[]>([])
  const [details, setDetails] = React.useState<Record<string, RunDetail>>({})
  const [selected, setSelected] = React.useState<string | undefined>()
  const [busy, setBusy] = React.useState<string | null>(null)
  const inflight = React.useRef(new Set<string>())

  const load = React.useCallback(async (run: KeewanoRun) => {
    const id = run.run_id
    if (inflight.current.has(id)) return
    inflight.current.add(id)
    setBusy(id)
    setDetails(d => ({ ...d, [id]: { ...d[id], loading: true } }))
    const [rep, dec] = await Promise.allSettled([
      getJSON(`${base}/report?run_id=${encodeURIComponent(id)}`),
      run.scenario === "agent" ? getJSON(`${base}/decisions?run_id=${encodeURIComponent(id)}`) : Promise.resolve(null),
    ])
    setDetails(d => ({ ...d, [id]: {
      loading: false,
      report: rep.status === "fulfilled" ? rep.value : null,
      reportError: rep.status === "rejected" ? String(rep.reason?.message ?? rep.reason) : undefined,
      decisions: dec.status === "fulfilled" ? dec.value : null,
    } }))
    inflight.current.delete(id)
    setBusy(b => (b === id ? null : b))
  }, [base])

  // Run list on mount, refreshed every minute (new runs show up; loaded details are kept).
  React.useEffect(() => {
    let alive = true
    const fetchRuns = () => getJSON(`${base}/runs`).then(p => { if (alive) setRuns(pickRuns(p)) }).catch(() => {})
    fetchRuns()
    const t = setInterval(fetchRuns, 60_000)
    return () => { alive = false; clearInterval(t) }
  }, [base])

  // Lazy: only the selected run (first one by default) is queried.
  const current = runs.find(r => r.run_id === selected) ?? runs[0]
  React.useEffect(() => { if (current && !details[current.run_id]) load(current) }, [current?.run_id])

  const pipelineRuns = React.useMemo(() => mapRuns(runs, details), [runs, details])
  const byId = (p: PipelineRun) => runs.find(r => r.run_id === p.id)

  return <BuildPipeline
    theme="light"
    title="Alpine pipeline"
    runs={pipelineRuns}
    selectedRunId={current?.run_id}
    onRunChange={p => setSelected(p.id)}
    onRerun={p => { const r = byId(p); if (r) load(r) }}
    rerunLabel="Re-query Keewano"
    busyRunId={busy}
  />
}

const roots = new WeakMap<Element, Root>()
;(window as any).mountAlpinePipeline = (el: Element, opts: Opts) => {
  let r = roots.get(el); if (!r) { r = createRoot(el); roots.set(el, r) }
  r.render(<AlpinePipeline apiBase={opts?.apiBase ?? ""} />)
}
