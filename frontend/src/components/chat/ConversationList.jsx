function groupByDate(sessions) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today - 86400000)
  const thisWeek = new Date(today - 7 * 86400000)

  const groups = { Today: [], Yesterday: [], 'This Week': [], Older: [] }

  for (const s of sessions) {
    const d = new Date(s.updated_at || s.created_at)
    if (d >= today) groups.Today.push(s)
    else if (d >= yesterday) groups.Yesterday.push(s)
    else if (d >= thisWeek) groups['This Week'].push(s)
    else groups.Older.push(s)
  }

  return Object.entries(groups).filter(([, items]) => items.length > 0)
}

export default function ConversationList({ sessions, activeSessionId, onSelect, onNew, onDelete }) {
  const grouped = groupByDate(sessions)

  return (
    <div className="w-64 bg-gray-50 border-r border-gray-200 flex flex-col h-full">
      <div className="p-3 border-b border-gray-200">
        <button
          onClick={onNew}
          className="w-full px-3 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 cursor-pointer"
        >
          + New Chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {grouped.length === 0 ? (
          <p className="text-center text-gray-400 text-sm py-8">No conversations yet</p>
        ) : (
          grouped.map(([label, items]) => (
            <div key={label}>
              <div className="px-3 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider">
                {label}
              </div>
              {items.map((s) => (
                <div
                  key={s.id}
                  onClick={() => onSelect(s.id)}
                  className={`group px-3 py-2 mx-1 rounded-lg cursor-pointer flex items-center justify-between ${
                    activeSessionId === s.id
                      ? 'bg-blue-100 text-blue-800'
                      : 'hover:bg-gray-100 text-gray-700'
                  }`}
                >
                  <span className="text-sm truncate flex-1">{s.title || 'New Chat'}</span>
                  <button
                    onClick={(e) => { e.stopPropagation(); onDelete(s.id) }}
                    className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 text-xs cursor-pointer ml-2"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
