import fs from 'node:fs'
import path from 'node:path'

const [root, endpoint] = process.argv.slice(2)
if (!root || !endpoint) throw new Error('uso: patch-endpoint.mjs <root> <endpoint>')

let replacements = 0
function visit(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules' && entry.name !== '.next') visit(file)
      continue
    }
    if (!/\.(ts|tsx)$/.test(entry.name)) continue
    const source = fs.readFileSync(file, 'utf8')
    const matches = source.match(/http:\/\/localhost:7777\/?/g)
    if (!matches) continue
    replacements += matches.length
    const updated = source.replace(/http:\/\/localhost:7777\/?/g, endpoint)
    if (updated !== source) fs.writeFileSync(file, updated)
  }
}

visit(root)
if (!replacements) throw new Error('endpoint padrão localhost:7777 não encontrado no Agent UI')
console.log(`endpoint Agent UI atualizado em ${replacements} arquivo(s)`)
