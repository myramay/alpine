# Skyline bundle source

The Contribution Skyline component (kedhareswer, 21st.dev, MIT) with one change: continuous shading by value instead of four levels.
Rebuild: `npm i react@18.3.1 react-dom@18.3.1 esbuild tailwindcss@3` then
`npx esbuild entry.tsx --bundle --minify --format=iife --target=es2020 --jsx=automatic --define:process.env.NODE_ENV=\"production\" --outfile=../skyline-bundle.js`
and `npx tailwindcss -c tailwind.config.js -i in.css -o ../skyline.css --minify` with `in.css` = `@tailwind components; @tailwind utilities;`.

# Pipeline bundle source

`components/ui/build-pipeline.tsx` is the BuildPipeline component (shadcn style, inline CSS, no Tailwind) as given. `pipeline-map.ts` maps `/runs`, `/report` and `/decisions` to its runs (pure, testable in node); `pipeline-entry.tsx` exposes `window.mountAlpinePipeline(el, { apiBase })`.
Rebuild (deps are in `package.json`; `node_modules/` and `package-lock.json` are gitignored): `npm i` then
`npx esbuild pipeline-entry.tsx --bundle --minify --format=iife --target=es2020 --jsx=automatic --define:process.env.NODE_ENV=\"production\" --outfile=../pipeline-bundle.js` (same as `npm run build:pipeline`).
