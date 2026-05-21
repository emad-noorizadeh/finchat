import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import useAgentStore from '../store/agentStore'
import AgentTable from '../components/agents/AgentTable'
import client from '../api/client'

export default function AgentsPage() {
  const { agents, loading, fetchAgents, deployAgent, disableAgent, deleteAgent } = useAgentStore()
  const [search, setSearch] = useState('')
  const [importing, setImporting] = useState(false)
  const importInputRef = useRef(null)
  const navigate = useNavigate()

  useEffect(() => {
    fetchAgents()
  }, [fetchAgents])

  useEffect(() => {
    const timeout = setTimeout(() => fetchAgents(search), 300)
    return () => clearTimeout(timeout)
  }, [search, fetchAgents])

  const handleImport = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setImporting(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const res = await client.post('/agents/import', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      const { name, channel } = res.data || {}
      alert(`Imported ${name} (${channel}).`)
      fetchAgents(search)
    } catch (err) {
      alert('Import failed: ' + (err?.response?.data?.detail || err.message))
    } finally {
      setImporting(false)
      if (importInputRef.current) importInputRef.current.value = ''
    }
  }

  const handleExport = async (templateName) => {
    try {
      const res = await client.get(`/agents/export/${templateName}`, { responseType: 'blob' })
      const dispo = res.headers['content-disposition'] || ''
      const match = /filename="?([^";]+)"?/.exec(dispo)
      const filename = match ? match[1] : `${templateName}.json`
      const url = window.URL.createObjectURL(res.data)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.URL.revokeObjectURL(url)
    } catch (err) {
      alert('Export failed: ' + (err?.response?.data?.detail || err.message))
    }
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Agents</h1>
          <p className="text-gray-500 text-sm mt-1">
            Sub-agents handle complex multi-step workflows
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={importInputRef}
            type="file"
            accept=".json,application/json"
            onChange={handleImport}
            className="hidden"
          />
          <button
            onClick={() => importInputRef.current?.click()}
            disabled={importing}
            title="Upload a sub-agent JSON template (validated server-side)"
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            {importing ? 'Importing…' : 'Import JSON'}
          </button>
          <button
            onClick={() => navigate('/agents/builder')}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 cursor-pointer"
          >
            + Create Agent
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="mb-4">
        <div className="relative">
          <svg
            className="absolute left-3 top-2.5 w-4 h-4 text-gray-400"
            fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
          </svg>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search agents..."
            className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <p className="text-gray-400 py-8 text-center">Loading agents...</p>
      ) : agents.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg mb-2">No agents found</p>
          <p className="text-sm">Create your first agent to get started</p>
        </div>
      ) : (
        <AgentTable
          agents={agents}
          onEdit={(name, channel) => navigate(`/agents/builder/${name}/${channel}`)}
          onDeploy={deployAgent}
          onDisable={disableAgent}
          onDelete={deleteAgent}
          onExport={handleExport}
        />
      )}
    </div>
  )
}
