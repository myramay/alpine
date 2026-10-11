# Skyline bundle source

The Contribution Skyline component (kedhareswer, 21st.dev, MIT) with one change: continuous shading by value instead of four levels.
Rebuild: `npm i react@18.3.1 react-dom@18.3.1 esbuild tailwindcss@3` then
`npx esbuild entry.tsx --bundle --minify --format=iife --target=es2020 --jsx=automatic --define:process.env.NODE_ENV=\"production\" --outfile=../skyline-bundle.js`
and `npx tailwindcss -c tailwind.config.js -i in.css -o ../skyline.css --minify` with `in.css` = `@tailwind components; @tailwind utilities;`.
