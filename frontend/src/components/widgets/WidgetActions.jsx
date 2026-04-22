import { useState } from 'react'

const STYLE_MAP = {
  primary: 'bg-blue-600 text-white hover:bg-blue-700',
  secondary: 'bg-white text-gray-700 border border-gray-300 hover:bg-gray-50',
  danger: 'bg-white text-red-600 border border-red-200 hover:bg-red-50',
  success: 'bg-green-600 text-white hover:bg-green-700',
}

export default function WidgetActions({ actions, widget, onAction }) {
  const [loadingId, setLoadingId] = useState(null)
  const [error, setError] = useState(null)

  if (!actions || actions.length === 0) return null

  const handleClick = async (action) => {
    if (action.confirm_message && !confirm(action.confirm_message)) return
    setLoadingId(action.id)
    setError(null)
    try {
      await onAction(action, widget)
    } catch (e) {
      setError(e.message || 'Action failed')
    }
    setLoadingId(null)
  }

  return (
    <div>
      <div className="flex gap-2 mt-3">
        {actions.map((action) => (
          <button
            key={action.id}
            onClick={() => handleClick(action)}
            disabled={loadingId !== null}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium cursor-pointer transition-colors disabled:opacity-50 ${STYLE_MAP[action.style] || STYLE_MAP.secondary}`}
          >
            {loadingId === action.id ? (
              <span className="flex items-center gap-1.5">
                <span className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
                {action.label}
              </span>
            ) : action.label}
          </button>
        ))}
      </div>
      {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
    </div>
  )
}
