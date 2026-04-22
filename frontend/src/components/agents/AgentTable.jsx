import { Fragment, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import AgentMenu from './AgentMenu'

const STATUS_STYLE = {
  deployed: 'bg-green-100 text-green-800',
  draft: 'bg-gray-100 text-gray-600',
  disabled: 'bg-red-100 text-red-700',
}

const CHANNEL_ICON = { chat: '💬', voice: '🎙️', api: '🔌' }

function formatDate(dateStr) {
  if (!dateStr) return '—'
  try {
    const d = new Date(dateStr)
    if (isNaN(d.getTime())) return '—'
    const now = new Date()
    const diff = now - d
    if (diff < 0) return d.toLocaleDateString()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    const days = Math.floor(hrs / 24)
    if (days < 7) return `${days}d ago`
    return d.toLocaleDateString()
  } catch {
    return '—'
  }
}

export default function AgentTable({ agents, onEdit, onDeploy, onDisable, onDelete }) {
  const [expanded, setExpanded] = useState(null)
  const navigate = useNavigate()

  return (
    <div className="border border-gray-200 rounded-xl overflow-visible">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-500 text-left text-xs uppercase tracking-wider">
          <tr>
            <th className="px-4 py-3 font-medium">Name</th>
            <th className="px-4 py-3 font-medium">Channels</th>
            <th className="px-4 py-3 font-medium">Status</th>
            <th className="px-4 py-3 font-medium">Creator</th>
            <th className="px-4 py-3 font-medium">Updated</th>
            <th className="px-4 py-3 font-medium w-10"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {agents.map((group) => {
            const isExpanded = expanded === group.name
            const latestUpdate = group.variants
              .map((v) => v.updated_at)
              .filter(Boolean)
              .sort()
              .reverse()[0]
            const creator = group.variants.find((v) => v.created_by && v.created_by !== '-')?.created_by || '-'
            const firstVariant = group.variants[0]

            return (
              <Fragment key={group.name}>
                {/* Group row */}
                <tr
                  className="hover:bg-gray-50 cursor-pointer"
                  onClick={() => setExpanded(isExpanded ? null : group.name)}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <svg
                        className={`w-4 h-4 text-gray-400 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                        fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                      </svg>
                      <span className="font-medium text-gray-900">{group.display_name}</span>
                      <span className="text-xs text-gray-400 font-mono">{group.name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1.5">
                      {group.variants.map((v) => (
                        <span key={v.channel} className="px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-600">
                          {CHANNEL_ICON[v.channel]} {v.channel}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-1.5">
                      {group.variants.map((v) => (
                        <span key={v.channel} className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLE[v.status]}`}>
                          {v.status}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-500">{creator}</td>
                  <td className="px-4 py-3 text-gray-500">{formatDate(latestUpdate)}</td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <AgentMenu actions={[
                      { label: 'Edit', onClick: () => navigate(`/agents/builder/${group.name}/${firstVariant.channel}`) },
                      ...(group.variants.some(v => v.id && v.status === 'draft') ? [
                        { label: 'Deploy All', onClick: () => group.variants.filter(v => v.id && v.status === 'draft').forEach(v => onDeploy(v.id)) },
                      ] : []),
                      ...(group.variants.some(v => v.id && v.status === 'deployed') ? [
                        { label: 'Disable All', onClick: () => group.variants.filter(v => v.id && v.status === 'deployed').forEach(v => onDisable(v.id)) },
                      ] : []),
                      { label: 'Delete All', danger: true, onClick: () => {
                        if (confirm('Delete all variants of this agent?')) {
                          group.variants.filter(v => v.id).forEach(v => onDelete(v.id))
                        }
                      }},
                    ]} />
                  </td>
                </tr>

                {/* Expanded variant rows */}
                {isExpanded && group.variants.map((v) => (
                  <tr key={`${group.name}-${v.channel}`} className="bg-gray-50/50">
                    <td className="px-4 py-2.5 pl-12">
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          navigate(`/agents/builder/${group.name}/${v.channel}`)
                        }}
                        className="flex items-center gap-2 text-blue-600 hover:text-blue-800 hover:underline cursor-pointer"
                      >
                        <span>{CHANNEL_ICON[v.channel]}</span>
                        <span className="capitalize text-sm font-medium">{v.channel}</span>
                      </button>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-xs text-gray-400">
                        {v.tool_count} tools · max {v.max_iterations} iter
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLE[v.status]}`}>
                        {v.status}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-400 text-xs">
                      {v.created_by || '—'}
                    </td>
                    <td className="px-4 py-2.5 text-gray-400 text-xs">
                      {formatDate(v.updated_at)}
                    </td>
                    <td className="px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
                      <AgentMenu actions={[
                        { label: 'Edit', onClick: () => navigate(`/agents/builder/${group.name}/${v.channel}`) },
                        v.status === 'draft'
                          ? { label: 'Deploy', onClick: () => onDeploy(v.id) }
                          : v.status === 'deployed'
                          ? { label: 'Disable', onClick: () => onDisable(v.id) }
                          : { label: 'Deploy', onClick: () => onDeploy(v.id) },
                        { label: 'Delete', danger: true, onClick: () => {
                          if (confirm('Delete this agent variant?')) onDelete(v.id)
                        }},
                      ]} />
                    </td>
                  </tr>
                ))}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
