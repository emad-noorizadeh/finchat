import WidgetActions from './WidgetActions'

export default function TransferConfirmation({ widget, onAction }) {
  const { data, title, actions } = widget
  const isCompleted = data?.status === 'COMPLETED'

  const fields = [
    { label: 'From', value: data?.from },
    { label: 'To', value: data?.to },
    { label: 'Amount', value: data?.amount ? `$${Number(data.amount).toLocaleString('en-US', { minimumFractionDigits: 2 })}` : '' },
    { label: 'Date', value: data?.date },
    { label: 'Confirmation ID', value: data?.confirmation_id },
  ].filter(f => f.value)

  return (
    <div className={`rounded-xl border p-4 ${isCompleted ? 'bg-green-50 border-green-200' : 'bg-white border-gray-200'}`}>
      <div className="flex items-center gap-2 mb-3">
        {isCompleted && <span className="text-green-600 text-lg">✓</span>}
        <h3 className="text-sm font-semibold text-gray-800">{title || 'Transfer'}</h3>
      </div>
      <div className="space-y-1.5">
        {fields.map((f) => (
          <div key={f.label} className="flex justify-between text-sm">
            <span className="text-gray-500">{f.label}</span>
            <span className="font-medium text-gray-800">{f.value}</span>
          </div>
        ))}
      </div>
      {actions?.length > 0 && (
        <WidgetActions actions={actions} widget={widget} onAction={onAction} />
      )}
    </div>
  )
}
