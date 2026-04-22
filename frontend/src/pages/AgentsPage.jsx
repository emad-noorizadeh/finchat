import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import useAgentStore from '../store/agentStore'
import AgentTable from '../components/agents/AgentTable'

export default function AgentsPage() {
  const { agents, loading, fetchAgents, deployAgent, disableAgent, deleteAgent } = useAgentStore()
  const [search, setSearch] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    fetchAgents()
  }, [fetchAgents])

  useEffect(() => {
    const timeout = setTimeout(() => fetchAgents(search), 300)
    return () => clearTimeout(timeout)
  }, [search, fetchAgents])

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Agents</h1>
          <p className="text-gray-500 text-sm mt-1">
            Sub-agents handle complex multi-step workflows
          </p>
        </div>
        <button
          onClick={() => navigate('/agents/builder')}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 cursor-pointer"
        >
          + Create Agent
        </button>
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
        />
      )}
    </div>
  )
}
