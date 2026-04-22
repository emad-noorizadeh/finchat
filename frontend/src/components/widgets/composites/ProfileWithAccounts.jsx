/**
 * Designed Tier-1 composite: profile header + account list in one styled card.
 *
 * Picked by the Presenter when both profile_data and accounts_data slots are
 * populated. The component is a hand-designed layout — not a runtime composer.
 */
export default function ProfileWithAccounts({ widget }) {
  const profile = widget?.data?.profile || {}
  const accounts = widget?.data?.accounts || []

  const name = profile.name || 'User'
  const location = [profile.city, profile.state].filter(Boolean).join(', ')
  const tier = profile.rewards_tier || ''
  const topScore = profile.credit_scores?.[0]?.score
  const scoreLabel = profile.credit_scores?.[0]?.assessmentCat

  const totalBalance = accounts.reduce((sum, a) => sum + (Number(a.balance) || 0), 0)

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      {/* Hero header */}
      <div className="px-6 py-5 bg-gradient-to-br from-blue-50 to-indigo-50 border-b border-gray-100">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap">
              <h3 className="text-xl font-semibold text-gray-900 truncate">{name}</h3>
              {location && <span className="text-sm text-gray-500">· {location}</span>}
            </div>
            {tier && (
              <p className="mt-1 text-sm text-blue-700 font-medium">{tier}</p>
            )}
          </div>
          {topScore !== undefined && (
            <div className="text-right flex-shrink-0">
              <p className="text-xs text-gray-500 uppercase tracking-wide">Credit Score</p>
              <p className="text-2xl font-bold text-gray-900">{topScore}</p>
              {scoreLabel && <p className="text-xs text-gray-500">{scoreLabel}</p>}
            </div>
          )}
        </div>
      </div>

      {/* Account summary row */}
      <div className="px-6 py-3 border-b border-gray-100 bg-gray-50 flex items-center justify-between">
        <p className="text-xs uppercase tracking-wide text-gray-500 font-semibold">
          Accounts ({accounts.length})
        </p>
        <p className="text-sm font-semibold text-gray-900">
          Total: ${totalBalance.toLocaleString('en-US', { minimumFractionDigits: 2 })}
        </p>
      </div>

      {/* Accounts list */}
      {accounts.length === 0 ? (
        <p className="px-6 py-6 text-sm text-gray-400 text-center">No accounts found</p>
      ) : (
        <div className="divide-y divide-gray-100">
          {accounts.map((acct, i) => (
            <div key={acct.account_ref || i} className="px-6 py-3 flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-800">{acct.display_name}</p>
                <p className="text-xs text-gray-400 mt-0.5">{acct.type}</p>
              </div>
              <div className="text-right">
                <p className="text-base font-semibold text-gray-900">
                  ${Number(acct.balance || 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                </p>
                {acct.available !== undefined && acct.available !== acct.balance && (
                  <p className="text-xs text-gray-400">
                    Avail ${Number(acct.available).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
