import { useMemo, useState } from 'react'
import WidgetActions from './WidgetActions'

const CATEGORY_COLORS = {
  PURCHASE_GAS: 'bg-amber-50 text-amber-700',
  PURCHASE_GROCERY: 'bg-green-50 text-green-700',
  PURCHASE_FOOD: 'bg-orange-50 text-orange-700',
  PURCHASE_RETAIL: 'bg-pink-50 text-pink-700',
  PURCHASE_ONLINE: 'bg-purple-50 text-purple-700',
  PURCHASE_ELECTRONICS: 'bg-indigo-50 text-indigo-700',
  PURCHASE_TRAVEL: 'bg-teal-50 text-teal-700',
  PURCHASE_TRANSPORT: 'bg-cyan-50 text-cyan-700',
  PURCHASE_SUBSCRIPTION: 'bg-violet-50 text-violet-700',
  PAYROLL_DIRECT: 'bg-emerald-50 text-emerald-700',
  DEPOSIT_CHECK: 'bg-emerald-50 text-emerald-700',
  INTEREST_CREDIT: 'bg-emerald-50 text-emerald-700',
  PAYMENT_BILL: 'bg-slate-100 text-slate-700',
  PAYMENT_RENT: 'bg-slate-100 text-slate-700',
  PAYMENT_CREDIT_CARD: 'bg-slate-100 text-slate-700',
  TRANSFER_INTERNAL: 'bg-blue-50 text-blue-700',
  TRANSFER_WIRE: 'bg-blue-50 text-blue-700',
  TRANSFER_ZELLE: 'bg-blue-50 text-blue-700',
  ATM_WITHDRAWAL: 'bg-gray-100 text-gray-700',
  CHECK_PAID: 'bg-gray-100 text-gray-700',
}

function categoryLabel(cat) {
  if (!cat) return ''
  return cat.replace(/^PURCHASE_|^PAYMENT_|^TRANSFER_|^FEE_|^DEPOSIT_|^REFUND_|^REWARDS_/, '')
    .toLowerCase().replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function CategoryPill({ cat, className = '' }) {
  if (!cat) return null
  const style = CATEGORY_COLORS[cat] || 'bg-gray-100 text-gray-600'
  return (
    <span className={`inline-block text-[10px] font-medium px-1.5 py-0.5 rounded ${style} ${className}`}>
      {categoryLabel(cat)}
    </span>
  )
}

function Amount({ txn }) {
  const isCredit = txn.direction === 'credit'
  return (
    <span className={`text-sm font-medium ml-3 tabular-nums ${
      isCredit ? 'text-green-600' : 'text-gray-800'
    }`}>
      {isCredit ? '+' : ''}{txn.amount}
    </span>
  )
}

function TxnRow({ txn, onClick }) {
  const isPending = txn.status === 'pending'
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full px-4 py-2.5 flex items-center justify-between text-left hover:bg-gray-50 ${
        isPending ? 'bg-amber-50/40' : ''
      }`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className={`text-sm truncate ${isPending ? 'text-gray-700 italic' : 'text-gray-800'}`}>
            {txn.description}
          </p>
          {isPending && (
            <span className="inline-block text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 flex-shrink-0">
              Pending
            </span>
          )}
          <CategoryPill cat={txn.category} />
        </div>
        <p className="text-xs text-gray-400 mt-0.5">{txn.date} · {txn.account}</p>
      </div>
      <Amount txn={txn} />
    </button>
  )
}

function TxnDetail({ txn, onClose }) {
  if (!txn) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-2xl max-w-md w-full mx-4 overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-gray-100 flex items-start justify-between">
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-gray-900 truncate">{txn.description}</h3>
            <p className="text-xs text-gray-500 mt-0.5">{txn.date}</p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="Close"
          >×</button>
        </div>
        <div className="px-5 py-4 space-y-3">
          <div className="flex items-baseline justify-between">
            <span className="text-xs uppercase tracking-wide text-gray-500">Amount</span>
            <span className={`text-lg font-semibold tabular-nums ${
              txn.direction === 'credit' ? 'text-green-600' : 'text-gray-900'
            }`}>
              {txn.direction === 'credit' ? '+' : ''}{txn.amount}
            </span>
          </div>
          {txn.category && (
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase tracking-wide text-gray-500">Category</span>
              <CategoryPill cat={txn.category} />
            </div>
          )}
          <div className="flex items-center justify-between">
            <span className="text-xs uppercase tracking-wide text-gray-500">Account</span>
            <span className="text-sm text-gray-800">{txn.account}</span>
          </div>
          {txn.status && (
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase tracking-wide text-gray-500">Status</span>
              <span className="text-sm text-gray-800 capitalize">{txn.status}</span>
            </div>
          )}
          {txn.direction && (
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase tracking-wide text-gray-500">Direction</span>
              <span className="text-sm text-gray-800 capitalize">{txn.direction}</span>
            </div>
          )}
          {txn.merchant && txn.merchant !== txn.description && (
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase tracking-wide text-gray-500">Merchant</span>
              <span className="text-sm text-gray-800">{txn.merchant}</span>
            </div>
          )}
          {txn.reference && (
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase tracking-wide text-gray-500">Reference</span>
              <span className="text-xs font-mono text-gray-600">{txn.reference}</span>
            </div>
          )}
          {txn.status_description && (
            <div className="pt-2 border-t border-gray-100">
              <p className="text-xs text-gray-500">{txn.status_description}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function FlatList({ transactions, onRowClick }) {
  if (!transactions.length) {
    return <p className="px-4 py-6 text-sm text-gray-400 text-center">No transactions match</p>
  }
  return (
    <div className="divide-y divide-gray-50">
      {transactions.map((txn, i) => (
        <TxnRow key={txn.reference || i} txn={txn} onClick={() => onRowClick(txn)} />
      ))}
    </div>
  )
}

function GroupedList({ groups, onRowClick, expandedGroups, onToggleGroup, groupBy }) {
  if (!groups.length) {
    return <p className="px-4 py-6 text-sm text-gray-400 text-center">No groups</p>
  }
  const labelFor = (g) => {
    if (groupBy === 'category') return <CategoryPill cat={g.group} />
    return <span className="text-sm text-gray-800 truncate">{g.group || '—'}</span>
  }
  return (
    <div className="divide-y divide-gray-100">
      {groups.map((g, i) => {
        const expanded = expandedGroups.has(g.group)
        return (
          <div key={g.group || i}>
            <button
              type="button"
              onClick={() => onToggleGroup(g.group)}
              className="w-full px-4 py-2.5 flex items-center justify-between hover:bg-gray-50 text-left"
            >
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <span className="text-xs text-gray-400 w-3">{expanded ? '▾' : '▸'}</span>
                <span className="flex-shrink-0">{labelFor(g)}</span>
                <span className="text-xs text-gray-500 flex-shrink-0">{g.count} txn{g.count === 1 ? '' : 's'}</span>
              </div>
              <span className="text-sm font-semibold tabular-nums text-gray-800 flex-shrink-0 ml-3">
                {g.total_amount_display || `$${(g.total_amount || 0).toFixed(2)}`}
              </span>
            </button>
            {expanded && (
              <div className="bg-gray-50/50 divide-y divide-gray-100">
                {(g.transactions || []).map((txn, j) => (
                  <TxnRow key={txn.reference || j} txn={txn} onClick={() => onRowClick(txn)} />
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// Human-readable chips describing the server-side filters the backend
// applied before the payload reached us. Each chip communicates scope:
// the widget doesn't hold the full transaction set, only what matches.
function appliedFilterChips(applied) {
  if (!applied || typeof applied !== 'object') return []
  const chips = []
  if (applied.category) chips.push(categoryLabel(applied.category) || applied.category)
  if (applied.query) chips.push(`matching "${applied.query}"`)
  if (applied.direction) chips.push(applied.direction === 'credit' ? 'credits only' : 'debits only')
  if (applied.account) chips.push(`on ${applied.account}`)
  if (applied.date_from && applied.date_to) chips.push(`${applied.date_from} – ${applied.date_to}`)
  else if (applied.date_from) chips.push(`from ${applied.date_from}`)
  else if (applied.date_to) chips.push(`through ${applied.date_to}`)
  const money = (n) => `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  if (applied.min_amount != null && applied.max_amount != null) chips.push(`${money(applied.min_amount)} – ${money(applied.max_amount)}`)
  else if (applied.min_amount != null) chips.push(`≥ ${money(applied.min_amount)}`)
  else if (applied.max_amount != null) chips.push(`≤ ${money(applied.max_amount)}`)
  return chips
}

export default function TransactionList({ widget, onAction, mode = 'standalone' }) {
  const { data, title, actions, metadata } = widget
  const shape = data?.shape || (Array.isArray(data?.transactions) ? 'flat' : 'flat')
  const appliedChips = useMemo(() => appliedFilterChips(data?.applied_filters), [data?.applied_filters])

  const allTransactions = useMemo(
    () => (Array.isArray(data?.transactions) ? data.transactions : []),
    [data?.transactions]
  )
  const groups = useMemo(
    () => (Array.isArray(data?.groups) ? data.groups : []),
    [data?.groups]
  )

  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [merchantFilter, setMerchantFilter] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [expandedGroups, setExpandedGroups] = useState(() => new Set())
  const [visibleCount, setVisibleCount] = useState(mode === 'composite' ? 5 : 10)
  const [selected, setSelected] = useState(null)
  // Client-side grouping selector. Seed from the LLM's choice (data.group_by)
  // if the payload arrived pre-grouped, else "none".
  const [groupBy, setGroupBy] = useState(() => data?.group_by || 'none')

  const allFlatTransactions = useMemo(() => {
    if (shape === 'groups') {
      return groups.flatMap(g => g.transactions || [])
    }
    return allTransactions
  }, [shape, allTransactions, groups])

  const availableCategories = useMemo(() => {
    const cats = new Set()
    allFlatTransactions.forEach(t => t.category && cats.add(t.category))
    return Array.from(cats).sort()
  }, [allFlatTransactions])

  const availableMerchants = useMemo(() => {
    const ms = new Set()
    allFlatTransactions.forEach(t => t.merchant && ms.add(t.merchant))
    return Array.from(ms).sort()
  }, [allFlatTransactions])

  const parseISO = (s) => {
    if (!s) return null
    const d = new Date(s)
    return isNaN(d.getTime()) ? null : d
  }
  const parseRowDate = (row) => {
    const s = row?.date
    if (!s) return null
    // Rows use MM/DD/YYYY
    const parts = s.split('/')
    if (parts.length === 3) {
      const [m, d, y] = parts.map(Number)
      return new Date(y, m - 1, d)
    }
    const fallback = new Date(s)
    return isNaN(fallback.getTime()) ? null : fallback
  }

  const matchesFilters = (t) => {
    if (categoryFilter && t.category !== categoryFilter) return false
    if (merchantFilter && t.merchant !== merchantFilter) return false
    if (dateFrom || dateTo) {
      const d = parseRowDate(t)
      if (!d) return false
      const from = parseISO(dateFrom)
      const to = parseISO(dateTo)
      if (from && d < from) return false
      if (to) {
        const endOfDay = new Date(to); endOfDay.setHours(23, 59, 59, 999)
        if (d > endOfDay) return false
      }
    }
    const needle = search.trim().toLowerCase()
    if (needle) {
      const hay = `${t.description || ''} ${t.category || ''} ${t.account || ''} ${t.merchant || ''}`.toLowerCase()
      if (!hay.includes(needle)) return false
    }
    return true
  }

  // Single source of truth — start from the flattened list (regardless of
  // whether the payload arrived flat or pre-grouped), apply all filters, THEN
  // decide at render time whether to present flat or to regroup client-side.
  const filteredTransactions = useMemo(
    () => allFlatTransactions.filter(matchesFilters),
    [allFlatTransactions, search, categoryFilter, merchantFilter, dateFrom, dateTo]
  )

  // Derive the date range covered by the currently-filtered rows. Shown in the
  // header so the summary totals have scope. Absent rows with parseable dates
  // → omit. Same year → compact format ("Mar 8 – Apr 16"); else full year.
  const dateRange = useMemo(() => {
    const parsed = filteredTransactions
      .map(parseRowDate)
      .filter(d => d instanceof Date && !isNaN(d.getTime()))
    if (!parsed.length) return null
    const min = new Date(Math.min(...parsed.map(d => d.getTime())))
    const max = new Date(Math.max(...parsed.map(d => d.getTime())))
    const sameDay = min.toDateString() === max.toDateString()
    const sameYear = min.getFullYear() === max.getFullYear()
    const short = (d) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    const withYear = (d) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    if (sameDay) return short(min)
    if (sameYear) return `${short(min)} – ${short(max)}, ${max.getFullYear()}`
    return `${withYear(min)} – ${withYear(max)}`
  }, [filteredTransactions])

  const GROUP_KEY = {
    category: 'category',
    merchant: 'merchant',
    date: 'date',
    account: 'account',
  }

  const derivedGroups = useMemo(() => {
    const key = GROUP_KEY[groupBy]
    if (!key) return []
    const buckets = new Map()
    filteredTransactions.forEach(t => {
      const bucket = t[key] || 'Uncategorized'
      if (!buckets.has(bucket)) buckets.set(bucket, [])
      buckets.get(bucket).push(t)
    })
    const result = []
    for (const [group, txns] of buckets) {
      const total = txns.reduce((s, t) => s + (t.amount_value || 0), 0)
      result.push({
        group,
        count: txns.length,
        total_amount: total,
        total_amount_display: `$${total.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
        transactions: txns,
      })
    }
    result.sort((a, b) => b.total_amount - a.total_amount)
    return result
  }, [filteredTransactions, groupBy])

  const activeFilterCount = [categoryFilter, merchantFilter, dateFrom, dateTo].filter(Boolean).length
  const hasAnyFilter = !!(search || activeFilterCount)

  const clearAllFilters = () => {
    setSearch(''); setCategoryFilter(''); setMerchantFilter('')
    setDateFrom(''); setDateTo(''); setVisibleCount(10)
  }

  const toggleGroup = (name) => {
    setExpandedGroups(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const groupByLabel = groupBy !== 'none' ? ` by ${groupBy}` : ''

  // ----- COMPOSITE MODE: compact, top 5, "View all" link -----
  if (mode === 'composite') {
    if (groupBy !== 'none') {
      const top = derivedGroups.slice(0, 3)
      return (
        <div className="text-sm">
          {title && <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">{title}</h4>}
          <div className="divide-y divide-gray-100">
            {top.map((g, i) => (
              <div key={g.group || i} className="flex items-center justify-between py-1.5">
                <div className="flex items-center gap-2">
                  {groupBy === 'category' ? <CategoryPill cat={g.group} /> : <span className="text-xs text-gray-700">{g.group}</span>}
                  <span className="text-xs text-gray-400">{g.count}</span>
                </div>
                <span className="font-medium tabular-nums">{g.total_amount_display}</span>
              </div>
            ))}
          </div>
          <TxnDetail txn={selected} onClose={() => setSelected(null)} />
        </div>
      )
    }
    const top = filteredTransactions.slice(0, 5)
    return (
      <div className="text-sm">
        {title && <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500 mb-2">{title}</h4>}
        <FlatList transactions={top} onRowClick={setSelected} />
        <TxnDetail txn={selected} onClose={() => setSelected(null)} />
      </div>
    )
  }

  // ----- STANDALONE MODE: full widget with search/filter/pagination -----
  const hasControls = allFlatTransactions.length > 5 || availableCategories.length > 1 || availableMerchants.length > 1

  let bodyContent
  // Empty-state when filters/search produce zero matches: make it obvious
  // the filter is the reason, not "no data". Includes a one-click Clear all.
  if (filteredTransactions.length === 0 && hasAnyFilter) {
    bodyContent = (
      <div className="px-4 py-10 text-center">
        <p className="text-sm text-gray-700 font-medium">No transactions match your filters</p>
        <p className="text-xs text-gray-500 mt-1">
          {search && <>Search: <span className="font-mono">"{search}"</span></>}
          {search && activeFilterCount > 0 && <span> · </span>}
          {activeFilterCount > 0 && <>{activeFilterCount} filter{activeFilterCount === 1 ? '' : 's'} active</>}
        </p>
        <button
          type="button"
          onClick={clearAllFilters}
          className="mt-3 text-xs text-blue-600 hover:text-blue-700 font-medium"
        >
          Clear all filters
        </button>
      </div>
    )
  } else if (groupBy !== 'none') {
    bodyContent = (
      <GroupedList
        groups={derivedGroups}
        onRowClick={setSelected}
        expandedGroups={expandedGroups}
        onToggleGroup={toggleGroup}
        groupBy={groupBy}
      />
    )
  } else {
    const visible = filteredTransactions.slice(0, visibleCount)
    const remaining = filteredTransactions.length - visible.length
    bodyContent = (
      <>
        <FlatList transactions={visible} onRowClick={setSelected} />
        {remaining > 0 && (
          <div className="px-4 py-2 border-t border-gray-100 text-center">
            <button
              type="button"
              onClick={() => setVisibleCount(c => c + 10)}
              className="text-xs text-blue-600 hover:text-blue-700 font-medium"
            >
              Show {Math.min(10, remaining)} more ({remaining} remaining)
            </button>
          </div>
        )}
      </>
    )
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-gray-800">
            {title || 'Transactions'}{groupByLabel}
          </h3>
          {appliedChips.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-1">
              <span className="text-[10px] uppercase tracking-wide text-gray-400">Scope</span>
              {appliedChips.map((c, i) => (
                <span
                  key={i}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 font-medium"
                  title="The assistant pre-filtered this view. Widget search applies within this subset."
                >
                  {c}
                </span>
              ))}
            </div>
          )}
          {(() => {
            // Recompute summary from the CURRENT filtered set so the totals and
            // date range stay consistent with what the user is looking at.
            const count = filteredTransactions.length
            const inflow = filteredTransactions.reduce(
              (s, t) => t.direction === 'credit' ? s + (t.amount_value || 0) : s, 0
            )
            const outflow = filteredTransactions.reduce(
              (s, t) => t.direction === 'debit' ? s + (t.amount_value || 0) : s, 0
            )
            const money = (n) => `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
            return (
              <p className="text-[11px] text-gray-500 mt-0.5 truncate">
                {count} txn{count === 1 ? '' : 's'}
                {dateRange && <span className="ml-2">· {dateRange}</span>}
                {inflow > 0 && <span className="text-green-600 ml-2">↑ {money(inflow)}</span>}
                {outflow > 0 && <span className="text-gray-600 ml-2">↓ {money(outflow)}</span>}
              </p>
            )
          })()}
        </div>
        {metadata?.total > 0 && filteredTransactions.length !== metadata.total && (
          <span className="text-xs text-gray-400 flex-shrink-0 ml-3">
            {filteredTransactions.length} / {metadata.total}
          </span>
        )}
      </div>

      {hasControls && (
        <div className="border-b border-gray-100 bg-gray-50/50">
          <div className="px-4 py-2 flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search description, merchant, account…"
              className="flex-1 min-w-[160px] text-xs px-2 py-1 border border-gray-200 rounded focus:outline-none focus:ring-2 focus:ring-blue-200 bg-white"
            />
            <div className="flex items-center gap-1 text-xs text-gray-600">
              <span className="text-gray-500">View by</span>
              <select
                value={groupBy}
                onChange={e => setGroupBy(e.target.value)}
                className="text-xs px-1.5 py-1 border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
              >
                <option value="none">List</option>
                <option value="category">Category</option>
                <option value="merchant">Merchant</option>
                <option value="date">Date</option>
                <option value="account">Account</option>
              </select>
            </div>
            <button
              type="button"
              onClick={() => setFiltersOpen(v => !v)}
              className={`text-xs px-2 py-1 rounded border transition-colors ${
                filtersOpen || activeFilterCount
                  ? 'bg-blue-50 border-blue-200 text-blue-700'
                  : 'bg-white border-gray-200 text-gray-600 hover:border-gray-300'
              }`}
            >
              Filters{activeFilterCount ? ` · ${activeFilterCount}` : ''} {filtersOpen ? '▴' : '▾'}
            </button>
            {hasAnyFilter && (
              <button
                type="button"
                onClick={clearAllFilters}
                className="text-xs text-gray-500 hover:text-gray-700"
              >
                Clear
              </button>
            )}
          </div>

          {filtersOpen && (
            <div className="px-4 pb-3 grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-2">
              {availableCategories.length > 1 && (
                <label className="flex items-center gap-2 text-xs text-gray-600">
                  <span className="w-16 text-gray-500">Category</span>
                  <select
                    value={categoryFilter}
                    onChange={e => setCategoryFilter(e.target.value)}
                    className="flex-1 px-2 py-1 border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
                  >
                    <option value="">All</option>
                    {availableCategories.map(c => (
                      <option key={c} value={c}>{categoryLabel(c)}</option>
                    ))}
                  </select>
                </label>
              )}

              {availableMerchants.length > 1 && (
                <label className="flex items-center gap-2 text-xs text-gray-600">
                  <span className="w-16 text-gray-500">Merchant</span>
                  <select
                    value={merchantFilter}
                    onChange={e => setMerchantFilter(e.target.value)}
                    className="flex-1 px-2 py-1 border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
                  >
                    <option value="">All</option>
                    {availableMerchants.map(m => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                </label>
              )}

              <label className="flex items-center gap-2 text-xs text-gray-600">
                <span className="w-16 text-gray-500">From</span>
                <input
                  type="date"
                  value={dateFrom}
                  onChange={e => setDateFrom(e.target.value)}
                  className="flex-1 px-2 py-1 border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
                />
              </label>

              <label className="flex items-center gap-2 text-xs text-gray-600">
                <span className="w-16 text-gray-500">To</span>
                <input
                  type="date"
                  value={dateTo}
                  onChange={e => setDateTo(e.target.value)}
                  className="flex-1 px-2 py-1 border border-gray-200 rounded bg-white focus:outline-none focus:ring-2 focus:ring-blue-200"
                />
              </label>
            </div>
          )}
        </div>
      )}

      {bodyContent}

      {actions?.length > 0 && (
        <div className="px-4 py-2 border-t border-gray-100">
          <WidgetActions actions={actions} widget={widget} onAction={onAction} />
        </div>
      )}

      <TxnDetail txn={selected} onClose={() => setSelected(null)} />
    </div>
  )
}
