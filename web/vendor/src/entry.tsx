import * as React from "react"
import { createRoot, Root } from "react-dom/client"
import ContributionSkyline, { ContributionSkylineProps } from "./contribution-skyline"
const roots = new WeakMap<Element, Root>()
;(window as any).mountSkyline = (el: Element, props: ContributionSkylineProps) => {
  let r = roots.get(el); if (!r) { r = createRoot(el); roots.set(el, r) }
  r.render(<ContributionSkyline {...props} />)
}
