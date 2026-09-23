import fs from 'node:fs'
import path from 'node:path'

const root = process.argv[2]
if (!root) throw new Error('uso: customize-model-catalog.mjs <root>')

const sidebar = path.join(root, 'src/components/chat/Sidebar/Sidebar.tsx')
const component = path.join(root, 'src/components/chat/Sidebar/ModelCatalogSelector.tsx')
let source = fs.readFileSync(sidebar, 'utf8')
if (!source.includes("./ModelCatalogSelector")) {
  source = source.replace(
    "import { Skeleton } from '@/components/ui/skeleton'",
    "import { Skeleton } from '@/components/ui/skeleton'\nimport ModelCatalogSelector from './ModelCatalogSelector'"
  )
  source = source.replace(
    '                      <EntitySelector />',
    '                      <EntitySelector />\n                      <ModelCatalogSelector />'
  )
  fs.writeFileSync(sidebar, source)
}

fs.writeFileSync(component, `'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { useStore } from '@/store'

type CatalogModel = { id: string; provider: string; is_free?: boolean }

export default function ModelCatalogSelector() {
  const endpoint = useStore((state) => state.selectedEndpoint)
  const authToken = useStore((state) => state.authToken)
  const selectedModel = useStore((state) => state.selectedModel)
  const setSelectedModel = useStore((state) => state.setSelectedModel)
  const [models, setModels] = useState<CatalogModel[]>([])
  const [value, setValue] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let active = true
    fetch(\
      \`\${endpoint.replace(/\\/$/, '')}/api/models/catalog?limit=10000\`,
      { headers: authToken ? { Authorization: \`Bearer \${authToken}\` } : undefined }
    )
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((data) => { if (active) setModels(data.models || []) })
      .catch(() => { if (active) setModels([]) })
    return () => { active = false }
  }, [endpoint, authToken])

  useEffect(() => {
    if (selectedModel && models.some((item) => item.id === selectedModel || \`\${item.provider}/\${item.id}\` === selectedModel)) {
      setValue(selectedModel)
    }
  }, [models, selectedModel])

  const selectModel = async (next: string) => {
    setValue(next)
    const item = models.find((candidate) => candidate.id === next || \`\${candidate.provider}/\${candidate.id}\` === next)
    if (!item) return
    setLoading(true)
    try {
      const response = await fetch(\`\${endpoint.replace(/\\/$/, '')}/api/models/select\`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(authToken ? { Authorization: \`Bearer \${authToken}\` } : {}) },
        body: JSON.stringify({ provider: item.provider, model: item.id })
      })
      if (!response.ok) throw new Error(await response.text())
      setSelectedModel(item.id)
      toast.success(\`Modelo Agno: \${item.id}\`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Não foi possível selecionar o modelo')
    } finally { setLoading(false) }
  }

  return <div className="flex w-full flex-col items-start gap-2">
    <div className="text-xs font-medium uppercase text-primary">Catálogo Bauer</div>
    <select className="h-9 w-full rounded-xl border border-primary/15 bg-accent px-3 text-xs text-muted" value={value} disabled={loading || models.length === 0} onChange={(event) => selectModel(event.target.value)}>
      <option value="">{models.length ? 'Selecionar modelo' : 'Catálogo indisponível'}</option>
      {models.map((item) => <option key={\`\${item.provider}/\${item.id}\`} value={item.id}>{item.provider} · {item.id}{item.is_free ? ' · grátis' : ''}</option>)}
    </select>
  </div>
}
`)
console.log('seletor do catálogo Bauer aplicado no Agent UI')
