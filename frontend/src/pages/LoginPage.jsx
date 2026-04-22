import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import useAuthStore from '../store/authStore'
import ProfileCard from '../components/profiles/ProfileCard'

export default function LoginPage() {
  const { profile, profiles, loading, fetchProfiles, login } = useAuthStore()
  const [selected, setSelected] = useState(null)
  const [loggingIn, setLoggingIn] = useState(false)
  const navigate = useNavigate()

  // Honour the persisted profile — if the user is already logged in (from a
  // previous session rehydrated via zustand/persist), send them straight to
  // chat. They can still reach this page by clicking "Switch profile" which
  // calls logout() first.
  useEffect(() => {
    if (profile) navigate('/chat', { replace: true })
  }, [profile, navigate])

  useEffect(() => {
    fetchProfiles()
  }, [fetchProfiles])

  const handleLogin = async () => {
    if (!selected) return
    setLoggingIn(true)
    const success = await login(selected.login_id)
    if (success) {
      navigate('/chat')
    }
    setLoggingIn(false)
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
      <div className="w-full max-w-lg">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-900">Erica Agent Platform</h1>
          <p className="text-gray-500 mt-2">Select a profile to get started</p>
        </div>

        {loading ? (
          <p className="text-center text-gray-400">Loading profiles...</p>
        ) : (
          <div className="space-y-3">
            {profiles.map((profile) => (
              <ProfileCard
                key={profile.login_id}
                profile={profile}
                selected={selected?.login_id === profile.login_id}
                onSelect={setSelected}
              />
            ))}
          </div>
        )}

        {selected && (
          <div className="mt-6 p-4 bg-white rounded-xl border border-gray-200">
            <div className="mb-4">
              <h2 className="text-xl font-semibold text-gray-900">{selected.name}</h2>
              <p className="text-sm text-gray-500 mt-1">{selected.city}, {selected.state} &middot; {selected.tier}</p>
            </div>
            <button
              onClick={handleLogin}
              disabled={loggingIn}
              className="w-full py-2.5 bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors cursor-pointer"
            >
              {loggingIn ? 'Logging in...' : `Continue as ${selected.name}`}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
