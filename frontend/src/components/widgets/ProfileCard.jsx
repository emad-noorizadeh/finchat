import WidgetActions from './WidgetActions'

export default function ProfileCard({ widget, onAction, mode = 'standalone' }) {
  const { data, title, actions } = widget

  const fields = [
    data?.name && { label: 'Name', value: data.name },
    (data?.city || data?.state) && { label: 'Location', value: [data.city, data.state].filter(Boolean).join(', ') },
    data?.rewards_tier && { label: 'Rewards Tier', value: data.rewards_tier },
    data?.segment && { label: 'Segment', value: data.segment },
    data?.credit_scores?.length > 0 && {
      label: 'Credit Score',
      value: `${data.credit_scores[0].score} (${data.credit_scores[0].assessmentCat || ''})`
    },
    data?.qualifying_balance && { label: 'Qualifying Balance', value: `$${Number(data.qualifying_balance).toLocaleString('en-US', { minimumFractionDigits: 2 })}` },
  ].filter(Boolean)

  if (mode === 'composite') {
    // No outer card; compact key/value rows with a small section heading.
    return (
      <div className="text-sm">
        {title && <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">{title}</h4>}
        <div className="divide-y divide-gray-100">
          {fields.map((f) => (
            <div key={f.label} className="flex justify-between py-1.5">
              <span className="text-gray-500">{f.label}</span>
              <span className="font-medium text-gray-800 text-right">{f.value}</span>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-5 py-3 border-b border-gray-100 bg-gray-50">
        <h3 className="text-sm font-semibold text-gray-800">{title || 'Profile'}</h3>
      </div>
      <div className="px-5 py-3 divide-y divide-gray-50">
        {fields.map((f) => (
          <div key={f.label} className="flex justify-between py-2 text-sm">
            <span className="text-gray-500 min-w-[140px]">{f.label}</span>
            <span className="font-medium text-gray-800 text-right">{f.value}</span>
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
