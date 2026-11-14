import { useEffect, useState } from 'react'

// 小工具：调用后端，统一处理错误
async function api(path, options = {}) {
  const res = await fetch(path, options)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
  return data
}

function Meta({ meta }) {
  if (!meta) return null
  return (
    <p className="meta">
      {meta.provider} · {meta.model} · {meta.cached ? 'cached' : `${meta.latency_ms} ms`}
      {meta.attempts > 1 && ` · ${meta.attempts} attempts`}
    </p>
  )
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [file, setFile] = useState(null)
  const [mode, setMode] = useState('chat')
  const [output, setOutput] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/api/health').then(setHealth).catch((e) => setError(`Backend not reachable: ${e.message}`))
  }, [])

  async function run() {
    setBusy(true)
    setError('')
    setOutput(null)
    try {
      if (mode === 'vision') {
        if (!file) throw new Error('Choose an image first')
        const form = new FormData()
        form.append('prompt', prompt || 'Describe this image.')
        form.append('file', file)
        const r = await api('/api/vision', { method: 'POST', body: form })
        setOutput({ text: r.text, meta: r })
      } else {
        const path = mode === 'breakdown' ? '/api/breakdown' : '/api/chat'
        const r = await api(path, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt }),
        })
        setOutput(mode === 'breakdown' ? { json: r.result, meta: r.meta } : { text: r.text, meta: r })
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main>
      <header>
        <h1>MEC kit</h1>
        {health && (
          <span className="status">
            {health.providers.map((p) => p.name).join(' → ') || 'no providers configured'}
          </span>
        )}
      </header>

      <div className="tabs">
        {['chat', 'vision', 'breakdown'].map((m) => (
          <button key={m} className={m === mode ? 'active' : ''} onClick={() => setMode(m)}>
            {m}
          </button>
        ))}
      </div>

      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        placeholder={mode === 'vision' ? 'What should the model look for?' : 'Type a prompt'}
        rows={5}
      />
      {mode === 'vision' && <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files[0])} />}
      <button className="run" onClick={run} disabled={busy}>
        {busy ? 'Running…' : 'Run'}
      </button>

      {error && <p className="error">{error}</p>}
      {output && (
        <section className="output">
          {output.text && <pre>{output.text}</pre>}
          {output.json && <pre>{JSON.stringify(output.json, null, 2)}</pre>}
          <Meta meta={output.meta} />
        </section>
      )}
    </main>
  )
}
