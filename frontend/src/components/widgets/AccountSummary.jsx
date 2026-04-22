import WidgetActions from './WidgetActions'

export default function AccountSummary({ widget, onAction, mode = 'standalone' }) {
  const { data, title, actions } = widget
  const accounts = data?.accounts || []

  if (mode === 'composite') {
    return (
      <div className="text-sm">
        {title && <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">{title}</h4>}
        <div className="divide-y divide-gray-100">
          {accounts.map((acct, i) => (
            <div key={acct.account_ref || i} className="flex items-center justify-between py-1.5">
              <div>
                <p className="font-medium text-gray-800">{acct.display_name}</p>
                <p className="text-xs text-gray-400">{acct.type}</p>
              </div>
              <p className="font-semibold text-gray-900">
                ${acct.balance?.toLocaleString('en-US', { minimumFractionDigits: 2 })}
              </p>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-5 py-3 border-b border-gray-100 bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-800">{title || 'Your Accounts'}</h3>
      </div>
      <div className="divide-y divide-gray-100">
        {accounts.map((acct, i) => (
          <div key={acct.account_ref || i} className="px-5 py-4 flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-gray-800">{acct.display_name}</p>
              <p className="text-xs text-gray-400 mt-0.5">{acct.type}</p>
            </div>
            <div className="text-right">
              <p className="text-lg font-semibold text-gray-900">
                ${acct.balance?.toLocaleString('en-US', { minimumFractionDigits: 2 })}
              </p>
              {acct.available !== undefined && acct.available !== acct.balance && (
                <p className="text-xs text-gray-400">
                  Available: ${acct.available?.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
      {actions?.length > 0 && (
        <div className="px-5 py-3 border-t border-gray-100">
          <WidgetActions actions={actions} widget={widget} onAction={onAction} />
        </div>
      )}
    </div>
  )
}
