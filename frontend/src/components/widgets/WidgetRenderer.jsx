import TransactionList from './TransactionList'
import AccountSummary from './AccountSummary'
import TransferConfirmation from './TransferConfirmation'
import TransferForm from './TransferForm'
import RefundForm from './RefundForm'
import ProfileCard from './ProfileCard'
import ConfirmationRequest from './ConfirmationRequest'
import TextCard from './TextCard'
import GenericComposite from './GenericComposite'
import ProfileWithAccounts from './composites/ProfileWithAccounts'

const WIDGET_MAP = {
  transaction_list: TransactionList,
  account_summary: AccountSummary,
  transfer_confirmation: TransferConfirmation,
  transfer_form: TransferForm,
  refund_form: RefundForm,
  profile_card: ProfileCard,
  confirmation_request: ConfirmationRequest,
  text_card: TextCard,
  generic_composite: GenericComposite,
  profile_with_accounts: ProfileWithAccounts,
}

const STATUS_STYLES = {
  pending: '',
  completed: 'ring-2 ring-green-200 bg-green-50/30',
  dismissed: 'opacity-50',
  failed: 'ring-2 ring-red-200 bg-red-50/30',
  expired: 'opacity-40 grayscale',
}

export default function WidgetRenderer({ widget, onAction, mode = 'standalone' }) {
  if (!widget || !widget.widget) {
    return <pre className="text-xs text-gray-400">{JSON.stringify(widget, null, 2)}</pre>
  }

  const Component = WIDGET_MAP[widget.widget]
  if (!Component) {
    return (
      <div className="p-3 bg-gray-50 rounded-lg border border-gray-200">
        <p className="text-xs text-gray-500 mb-1">Unknown widget: {widget.widget}</p>
        <pre className="text-xs text-gray-600 whitespace-pre-wrap">{JSON.stringify(widget.data, null, 2)}</pre>
      </div>
    )
  }

  // In composite mode, skip the outer status frame — sections live inside a
  // GenericComposite frame instead.
  if (mode === 'composite') {
    return <Component widget={widget} onAction={onAction} mode="composite" />
  }

  const status = widget.status || 'pending'
  const statusStyle = STATUS_STYLES[status] || ''

  return (
    <div className={`rounded-xl ${statusStyle}`}>
      {status === 'completed' && !['transfer_confirmation', 'refund_form'].includes(widget.widget) && (
        <div className="flex items-center gap-1.5 px-4 pt-2 text-xs text-green-600">
          <span>✓</span> Completed
        </div>
      )}
      {status === 'failed' && (
        <div className="flex items-center gap-1.5 px-4 pt-2 text-xs text-red-600">
          <span>!</span> Failed — try again
        </div>
      )}
      <Component
        widget={widget}
        onAction={onAction}
        instanceId={widget.instance_id}
        status={status}
        mode="standalone"
      />
    </div>
  )
}
