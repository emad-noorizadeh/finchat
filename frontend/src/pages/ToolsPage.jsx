import { useEffect, useState, Fragment } from 'react'
import client from '../api/client'

const LOAD_BADGE = {
  always: 'bg-green-100 text-green-800',
  deferred: 'bg-amber-100 text-amber-800',
}

const SCOPE_BADGE = {
  planner: 'bg-blue-100 text-blue-800',
  presenter: 'bg-indigo-100 text-indigo-800',
  sub_agent: 'bg-purple-100 text-purple-700',
}

const SCOPE_LABEL = {
  planner: 'Planner',
  presenter: 'Presenter',
  sub_agent: 'Sub-agent',
}

export default function ToolsPage() {
  const [tools, setTools] = useState([])
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [loadingDetail, setLoadingDetail] = useState(false)

  useEffect(() => {
    client.get('/tools').then((res) => {
      setTools(res.data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  const handleExpand = async (toolName) => {
    if (expandedId === toolName) { setExpandedId(null); setDetail(null); return }
    setExpandedId(toolName)
    setLoadingDetail(true)
    try {
      const res = await client.get(`/tools/${toolName}`)
      setDetail(res.data)
    } catch { setDetail(null) }
    setLoadingDetail(false)
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-800">Tools</h1>
        <p className="text-gray-500 text-sm mt-1">
          {tools.length} registered tools — the agent discovers deferred tools on demand via tool_search
        </p>
      </div>

      {loading ? (
        <p className="text-gray-400">Loading tools...</p>
      ) : tools.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg mb-2">No tools registered</p>
          <p className="text-sm">Tools are loaded when the server starts</p>
        </div>
      ) : (
        <div className="border border-gray-200 rounded-xl overflow-visible">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-left text-xs uppercase tracking-wider">
              <tr>
                <th className="px-5 py-3 font-medium">Name</th>
                <th className="px-5 py-3 font-medium w-28">Scope</th>
                <th className="px-5 py-3 font-medium w-28">Loading</th>
                <th className="px-5 py-3 font-medium w-28">Widget</th>
                <th className="px-5 py-3 font-medium w-20">Access</th>
                <th className="px-5 py-3 font-medium">Description</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {tools.map((tool) => (
                <Fragment key={tool.name}>
                  <tr className="hover:bg-gray-50 cursor-pointer" onClick={() => handleExpand(tool.name)}>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <svg
                          className={`w-4 h-4 text-gray-400 transition-transform ${expandedId === tool.name ? 'rotate-90' : ''}`}
                          fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor"
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                        </svg>
                        <span className="font-mono font-semibold text-gray-800 text-sm">{tool.name}</span>
                      </div>
                    </td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${SCOPE_BADGE[tool.scope] || 'bg-gray-100 text-gray-700'}`}>
                        {SCOPE_LABEL[tool.scope] || tool.scope || '—'}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      {tool.agent_scoped ? (
                        <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
                          agent: {tool.agent}
                        </span>
                      ) : (
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${tool.always_load ? LOAD_BADGE.always : LOAD_BADGE.deferred}`}>
                          {tool.always_load ? 'always' : 'deferred'}
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      {tool.widget ? (
                        <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">
                          {tool.widget}
                        </span>
                      ) : (
                        <span className="text-gray-400 text-xs">none</span>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      {tool.is_read_only ? (
                        <span className="text-gray-500 text-xs">read</span>
                      ) : (
                        <span className="text-orange-600 text-xs font-medium">write</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-gray-500 truncate max-w-md">{tool.description}</td>
                  </tr>

                  {expandedId === tool.name && (
                    <tr>
                      <td colSpan={6} className="bg-gray-50 px-5 py-4">
                        {loadingDetail ? (
                          <p className="text-gray-400 text-sm">Loading...</p>
                        ) : detail ? (
                          <div className="space-y-4">
                            {/* Top metadata row */}
                            <div className="grid grid-cols-4 gap-4 text-sm">
                              <div>
                                <span className="text-gray-500 text-xs">Concurrency</span>
                                <p className="text-gray-700 text-xs mt-0.5">{detail.is_concurrency_safe ? 'Safe (parallel)' : 'Sequential only'}</p>
                              </div>
                              <div>
                                <span className="text-gray-500 text-xs">Search Hints</span>
                                <p className="text-gray-700 text-xs mt-0.5 font-mono">{detail.search_hint || '—'}</p>
                              </div>
                              <div>
                                <span className="text-gray-500 text-xs">Widget Output</span>
                                <p className="text-gray-700 text-xs mt-0.5">{detail.widget || 'Text only'}</p>
                              </div>
                              <div>
                                <span className="text-gray-500 text-xs">Workflow</span>
                                <p className="text-gray-700 text-xs mt-0.5">{detail.has_workflow_instructions ? 'Has instructions' : 'None'}</p>
                              </div>
                            </div>

                            {/* Flow */}
                            {detail.flow?.length > 0 && (
                              <div>
                                <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">Flow</h4>
                                <div className="flex flex-wrap gap-2">
                                  {detail.flow.map((step, i) => (
                                    <div key={i} className="flex items-center gap-1.5">
                                      {i > 0 && (
                                        <svg className="w-3 h-3 text-gray-300 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                          <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
                                        </svg>
                                      )}
                                      <span className="px-2.5 py-1 bg-white border border-gray-200 rounded-lg text-xs text-gray-700">
                                        {step}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Validation & Errors side by side */}
                            {(detail.validations?.length > 0 || detail.errors?.length > 0) && (
                              <div className="grid grid-cols-2 gap-4">
                                {detail.validations?.length > 0 && (
                                  <div>
                                    <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">Validation</h4>
                                    <ul className="space-y-1">
                                      {detail.validations.map((v, i) => (
                                        <li key={i} className="flex items-start gap-1.5 text-xs text-gray-600">
                                          <svg className="w-3.5 h-3.5 text-green-500 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                                          </svg>
                                          {v}
                                        </li>
                                      ))}
                                    </ul>
                                  </div>
                                )}
                                {detail.errors?.length > 0 && (
                                  <div>
                                    <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">Error Handling</h4>
                                    <ul className="space-y-1">
                                      {detail.errors.map((e, i) => (
                                        <li key={i} className="flex items-start gap-1.5 text-xs text-gray-600">
                                          <svg className="w-3.5 h-3.5 text-red-400 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
                                          </svg>
                                          {e}
                                        </li>
                                      ))}
                                    </ul>
                                  </div>
                                )}
                              </div>
                            )}

                            {/* Input Schema */}
                            {detail.input_schema?.properties && Object.keys(detail.input_schema.properties).length > 0 && (
                              <div>
                                <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">Input Parameters</h4>
                                <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
                                  {Object.entries(detail.input_schema.properties).map(([key, prop]) => {
                                    const isRequired = detail.input_schema.required?.includes(key)
                                    return (
                                      <div key={key} className="px-4 py-2.5 flex items-start gap-4">
                                        <div className="flex items-center gap-2 min-w-[140px]">
                                          <span className="font-mono text-xs font-semibold text-gray-800">{key}</span>
                                          {isRequired && (
                                            <span className="text-red-400 text-xs">*</span>
                                          )}
                                        </div>
                                        <div className="flex-1">
                                          <div className="flex items-center gap-2">
                                            <span className="text-xs text-gray-400">{prop.type}{prop.enum ? ` (${prop.enum.join(' | ')})` : ''}</span>
                                            {prop.default !== undefined && (
                                              <span className="text-xs text-gray-400">default: {JSON.stringify(prop.default)}</span>
                                            )}
                                          </div>
                                          {prop.description && (
                                            <p className="text-xs text-gray-600 mt-0.5">{prop.description}</p>
                                          )}
                                        </div>
                                      </div>
                                    )
                                  })}
                                </div>
                              </div>
                            )}

                            {/* Workflow Instructions */}
                            {detail.workflow_instructions && (
                              <div>
                                <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wider mb-2">Workflow Instructions</h4>
                                <div className="bg-white rounded-lg p-3 border border-gray-200">
                                  <pre className="text-xs text-gray-700 whitespace-pre-wrap">{detail.workflow_instructions}</pre>
                                </div>
                              </div>
                            )}
                          </div>
                        ) : (
                          <p className="text-gray-400 text-sm">No details available</p>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
