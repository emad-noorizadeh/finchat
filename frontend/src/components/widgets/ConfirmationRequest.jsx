import WidgetActions from './WidgetActions'

export default function ConfirmationRequest({ widget, onAction }) {
  const { data, title, actions } = widget
  const fields = data?.fields || []

  return (
    <div className="bg-amber-50 rounded-xl border border-amber-200 p-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-amber-600 text-lg">⚠</span>
        <h3 className="text-sm font-semibold text-gray-800">{title || 'Confirmation Required'}</h3>
      </div>
      {data?.details && (
        <p className="text-sm text-gray-600 mb-3">{data.details}</p>
      )}
      {fields.length > 0 && (
        <div className="space-y-1.5 mb-3">
          {fields.map((f, i) => (
            <div key={i} className="flex justify-between text-sm">
              <span className="text-gray-500">{f.label}</span>
              <span className="font-medium text-gray-800">{f.value}</span>
            </div>
          ))}
        </div>
      )}
      {actions?.length > 0 && (
        <WidgetActions actions={actions} widget={widget} onAction={onAction} />
      )}
    </div>
  )
}
