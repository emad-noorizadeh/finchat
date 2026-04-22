import { NavLink } from 'react-router-dom'
import useAuthStore from '../../store/authStore'

const navItems = [
  { to: '/chat', label: 'Chat', icon: '💬' },
  { to: '/tools', label: 'Tools', icon: '🔧' },
  { to: '/knowledge', label: 'Knowledge', icon: '📚' },
  { to: '/widgets', label: 'Widgets', icon: '📊' },
  { to: '/agents', label: 'Agents', icon: '⚡' },
]

function initials(name) {
  if (!name) return '?'
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join('')
}

export default function Sidebar() {
  const profile = useAuthStore((s) => s.profile)
  const logout = useAuthStore((s) => s.logout)
  const name = profile?.name || profile?.login_id || ''
  const subtitle = profile
    ? [profile.city, profile.state].filter(Boolean).join(', ') || profile.tier || ''
    : ''

  return (
    <aside className="w-60 h-screen bg-gray-900 text-gray-300 flex flex-col">
      <div className="p-4 border-b border-gray-700">
        <h1 className="text-lg font-semibold text-white leading-tight">Erica Agent Platform</h1>
      </div>
      <nav className="flex-1 p-2 space-y-1">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                isActive
                  ? 'bg-gray-700 text-white'
                  : 'hover:bg-gray-800 hover:text-white'
              }`
            }
          >
            <span>{item.icon}</span>
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="p-3 border-t border-gray-700 space-y-2">
        {profile ? (
          <div className="flex items-center gap-2.5 px-2 py-1.5">
            <div className="h-8 w-8 rounded-full bg-blue-500/20 text-blue-300 flex items-center justify-center text-xs font-semibold border border-blue-400/30">
              {initials(name)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm text-white font-medium truncate">{name}</div>
              {subtitle && (
                <div className="text-[11px] text-gray-400 truncate">{subtitle}</div>
              )}
            </div>
          </div>
        ) : (
          <div className="px-2 py-1.5 text-xs text-gray-500 italic">No profile selected</div>
        )}
        <NavLink
          to="/"
          onClick={() => logout()}
          className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm hover:bg-gray-800 hover:text-white transition-colors"
        >
          <span>👤</span>
          <span>Switch profile</span>
        </NavLink>
      </div>
    </aside>
  )
}
