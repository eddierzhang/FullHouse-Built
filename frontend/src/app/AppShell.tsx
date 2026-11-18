import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  type AgentAction,
  type AgentDefinition,
  type AgentQuestion,
  type InventoryItem,
  type Restaurant,
  type Runtime,
  type User,
  approveAction,
  canEditRestaurant,
  fetchChangeVersion,
  fetchFlags,
  fetchHealth,
  fetchRuntime,
  listActions,
  listAgentDefinitions,
  listInventoryItems,
  listQuestions,
  rejectAction,
  triggerRun,
} from '../api/client'
import { ActionDrawer } from '../components/ActionDrawer'
import { CommandBar, type CommandPage } from '../components/CommandBar'
import { TOUR_EVENT, Tour, tourSeen } from '../components/Tour'
import { VoiceButton } from '../components/VoiceButton'
import { FeedbackButton } from '../components/FeedbackButton'
import { Icon, type IconName } from '../components/Icon'
import { NewTaskModal } from '../components/NewTaskModal'
import { NoticesBell } from '../components/NoticesBell'
import { fetchNotices, type Notices } from '../api/notifications'
import { AskField } from '../components/prices'
import { Toast, type ToastState } from '../components/Toast'
import { relativeTime } from '../lib/format'
import { announceDataChanged } from '../lib/useLoad'
import { PRICE_ROUTES, type Route, readHashParam, useHashRoute } from '../lib/useHashRoute'
import { AgentsPage } from '../pages/AgentsPage'
import { CalendarPage } from '../pages/CalendarPage'
import { InvoiceHubPage } from '../pages/InvoiceHubPage'
import { ConnectPage } from '../pages/ConnectPage'
import { PrepSheetPage, PrepSheetsPage } from '../pages/PrepSheetPage'
import { PurchaseOrderPage } from '../pages/PurchaseOrderPage'
import { RecipePage } from '../pages/RecipePage'
import { RecipesPage } from '../pages/RecipesPage'
import { CustomersPage } from '../pages/CustomersPage'
import { DigestPage } from '../pages/DigestPage'
import { LaborPage } from '../ml/LaborPage'
import { MenuCostsPage } from '../ml/MenuCostsPage'
import { OrderingPage } from '../ml/OrderingPage'
import { ChannelsPage } from '../ml/ChannelsPage'
import { UsagePage } from '../ml/UsagePage'
import { DraftPage } from '../pages/DraftPage'
import { ItemPage } from '../pages/ItemPage'
import { HelpPage } from '../pages/HelpPage'
import { AutopilotPage } from '../pages/AutopilotPage'
import { CountPage } from '../pages/CountPage'
import { InsightsPage } from '../pages/InsightsPage'
import { VariancePage } from '../pages/VariancePage'
import { ToolsPage } from '../pages/ToolsPage'
import { HistoryPage } from '../pages/HistoryPage'
import { ApprovalsPage } from '../pages/ApprovalsPage'
import { InventoryPage } from '../pages/InventoryPage'
import { MenuPage } from '../pages/MenuPage'
import { OverviewPage } from '../pages/OverviewPage'
import { ProfitPage } from '../pages/ProfitPage'
import { ReceivePage } from '../pages/ReceivePage'
import { RunsPage } from '../pages/RunsPage'
import { ProfilePage } from '../pages/ProfilePage'
import { StaffPage } from '../pages/StaffPage'
import { SuppliersPage } from '../pages/SuppliersPage'
import type { AppContext } from './context'

/** Health, the approvals badge and the agent's status, kept current without a reload. */
const POLL_MS = 20_000
/** How often to ask whether anything changed, so a teammate's change shows up by itself. */
const CHANGE_POLL_MS = 10_000

type NavItem = { route: Route; label: string; icon: IconName }

/** Pages only owners and admins see: the profit figures are theirs. */
const MANAGER_ROUTES = new Set<Route>(['profit', 'insights', 'autopilot'])

/**
 * Grouped by what people come to do: the day's check-in and what the agent
 * recommends; then one section each for stock, staffing, customers, the
 * invoices and prices, and the menu. Today, Inventory, Schedule and
 * Recommendations are also the phone's bottom bar. "Invoices & prices"
 * follows an invoice through, in order: it arrives and is read (Invoices),
 * the agent asks what it can't place (Questions), each confirmed line adds
 * to an item's price history (Items), the weekly findings go out (Digest),
 * and the loop closes in the conversation and drafts (Conversation). The
 * rest is setup and the longer view.
 */
const NAV: { label: string | null; items: NavItem[] }[] = [
  {
    label: null,
    items: [
      { route: 'overview', label: 'Today', icon: 'overview' },
      { route: 'approvals', label: 'Recommendations', icon: 'approvals' },
    ],
  },
  {
    label: 'Inventory',
    items: [
      { route: 'inventory', label: 'Inventory', icon: 'inventory' },
      { route: 'suppliers', label: 'Suppliers', icon: 'suppliers' },
    ],
  },
  {
    label: 'Invoices & prices',
    items: [
      { route: 'invoices', label: 'Invoices', icon: 'suppliers' },
      { route: 'digest', label: 'Digest', icon: 'profit' },
    ],
  },
  {
    label: 'Staffing',
    items: [{ route: 'staff', label: 'Schedule & staff', icon: 'staff' }],
  },
  {
    label: 'Customers',
    items: [{ route: 'customers', label: 'Customer requests', icon: 'chat' }],
  },
  {
    label: 'Menu',
    items: [{ route: 'menu', label: 'Menu', icon: 'menu' }],
  },
  {
    label: 'Agent',
    items: [
      { route: 'agents', label: 'Assistant Manager', icon: 'agents' },
      { route: 'runs', label: 'Agent activity', icon: 'runs' },
    ],
  },
  {
    label: 'Records',
    items: [
      { route: 'profit', label: 'Profit & forecast', icon: 'profit' },
      { route: 'history', label: 'History & undo', icon: 'clock' },
    ],
  },
  {
    label: 'Restaurant',
    items: [
      { route: 'profile', label: 'Profile', icon: 'store' },
      { route: 'help', label: 'Help', icon: 'info' },
    ],
  },
]

/** The phone's bottom bar: the day's work, and everything else behind More. */
const TABS: NavItem[] = [
  { route: 'overview', label: 'Today', icon: 'overview' },
  { route: 'inventory', label: 'Inventory', icon: 'inventory' },
  { route: 'staff', label: 'Schedule', icon: 'staff' },
  { route: 'approvals', label: 'Recommend', icon: 'approvals' },
]

/** The v3 group (behind v3_nav): the charts and the calculators, beside the pages they draw from. */
const INSIGHTS_GROUP: { label: string; items: NavItem[] } = {
  label: 'Insights',
  items: [
    { route: 'insights', label: 'Savings & costs', icon: 'trend' },
    { route: 'tools', label: 'Test a decision', icon: 'play' },
  ],
}

/** Pages that are tabs of another page (SectionTabs): the sidebar marks their section as the current page. */
const SECTION_OF: Partial<Record<Route, Route>> = { count: 'inventory', variance: 'inventory', receive: 'suppliers', questions: 'invoices', items: 'invoices', thread: 'invoices', documents: 'invoices' }

/** The Margin & Labor Engine's pages, each behind its own flag, with the spec's labels. */
const MARGIN_LABOR: { flag: string; item: NavItem }[] = [
  { flag: 'ml_menu_costs', item: { route: 'menu-costs', label: 'Menu costs', icon: 'menu' } },
  { flag: 'ml_usage', item: { route: 'usage', label: 'Usage', icon: 'inventory' } },
  { flag: 'ml_ordering', item: { route: 'ordering-prep', label: 'Ordering and prep', icon: 'suppliers' } },
  { flag: 'ml_labor', item: { route: 'labor', label: 'Labor', icon: 'staff' } },
  { flag: 'ml_channels', item: { route: 'channels', label: 'Channels', icon: 'trend' } },
]

/** Words people type for a page that its name doesn't contain. */
const PAGE_KEYWORDS: Partial<Record<Route, string>> = {
  overview: 'home dashboard',
  approvals: 'inbox approve proposals',
  inventory: 'stock count low',
  staff: 'shifts rota team',
  digest: 'prices weekly savings',
  thread: 'ask assistant chat messages',
  profit: 'revenue sales margin money',
  history: 'undo changes log',
  insights: 'charts prime cost spend money',
  tools: 'calculator par simulator what if tools run out switch supplier menu price',
  count: 'counting stocktake shelves areas walk-in',
  autopilot: 'auto order ordering pars order days limits send',
  receive: 'delivery deliveries check in receiving packing slip',
  variance: 'shrink waste missing loss spoiled',
  connect: 'pos payroll square gusto quickbooks integrations',
  profile: 'settings features team security',
}

/**
 * The public demo's short menu: the pages a five-minute walk-through uses (what the invoices found, the
 * what-if tools, counting, deliveries, autopilot). Every other page is still there: one click on "Show all
 * pages" brings the whole menu back, and search reaches all of them either way.
 */
const DEMO_MENU = new Set<Route>(['overview', 'approvals', 'insights', 'tools', 'inventory', 'suppliers', 'count', 'variance', 'receive', 'autopilot', 'invoices', 'questions', 'digest', 'help'])
const FULL_MENU_KEY = 'fullhouse.nav.full'

const COLLAPSED_KEY = 'fullhouse.nav.collapsed'
function readCollapsed(): string[] {
  try {
    const raw = window.localStorage.getItem(COLLAPSED_KEY)
    return raw ? (JSON.parse(raw) as string[]) : []
  } catch {
    return []
  }
}

/** Whether the browser thinks it's online, kept current. */
function useOnline(): boolean {
  const [online, setOnline] = useState(() => (typeof navigator === 'undefined' ? true : navigator.onLine))
  useEffect(() => {
    const up = () => setOnline(true)
    const down = () => setOnline(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    return () => {
      window.removeEventListener('online', up)
      window.removeEventListener('offline', down)
    }
  }, [])
  return online
}

type Props = {
  user: User
  restaurant: Restaurant
  setRestaurant: (restaurant: Restaurant) => void
  onSignOut: () => void
}

export function AppShell({ user, restaurant, setRestaurant, onSignOut }: Props) {
  const [route, navigate] = useHashRoute()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const online = useOnline()

  const [apiStatus, setApiStatus] = useState<AppContext['apiStatus']>('checking')
  const [agents, setAgents] = useState<AgentDefinition[]>([])
  const [pendingActions, setPendingActions] = useState<AgentAction[]>([])
  const [inventory, setInventory] = useState<InventoryItem[]>([])
  const [questions, setQuestions] = useState<AgentQuestion[]>([])
  const [runtime, setRuntime] = useState<Runtime | null>(null)
  const [flags, setFlags] = useState<Record<string, boolean>>({})
  const [notices, setNotices] = useState<Notices>({ unread: 0, items: [] })

  const [toastState, setToastState] = useState<ToastState>(null)
  const [taskAgent, setTaskAgent] = useState<string | null | undefined>(undefined)
  const [drawerAction, setDrawerAction] = useState<AgentAction | null>(null)
  const [busyActionId, setBusyActionId] = useState<string | null>(null)
  const [focusRunId, setFocusRunId] = useState<string | null>(null)
  const [commandOpen, setCommandOpen] = useState(false)
  // The demo's short menu, until someone asks for everything; remembered in this browser.
  const [fullMenu, setFullMenu] = useState(() => {
    try {
      return window.localStorage.getItem(FULL_MENU_KEY) === '1'
    } catch {
      return false
    }
  })
  const toggleFullMenu = () =>
    setFullMenu((held) => {
      try {
        window.localStorage.setItem(FULL_MENU_KEY, held ? '0' : '1')
      } catch {
        /* a private window forgets it */
      }
      return !held
    })
  const [tourOpen, setTourOpen] = useState(false)
  // Sidebar groups a person has folded away; every group starts open.
  const [collapsed, setCollapsed] = useState<string[]>(readCollapsed)
  const toggleGroup = (label: string) =>
    setCollapsed((held) => {
      const next = held.includes(label) ? held.filter((l) => l !== label) : [...held, label]
      try {
        window.localStorage.setItem(COLLAPSED_KEY, JSON.stringify(next))
      } catch {
        /* a private window forgets it; nothing else depends on it */
      }
      return next
    })

  const refresh = useCallback(() => {
    fetchHealth()
      .then(() => setApiStatus('ok'))
      .catch(() => setApiStatus('error'))
    listAgentDefinitions().then(setAgents).catch(() => {})
    listActions('pending').then(setPendingActions).catch(() => {})
    listInventoryItems().then(setInventory).catch(() => {})
    listQuestions().then(setQuestions).catch(() => {})
    fetchRuntime().then(setRuntime).catch(() => {})
    fetchFlags().then((f) => setFlags(f.flags)).catch(() => {})
    fetchNotices().then(setNotices).catch(() => {})
  }, [])

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh])

  // Live refresh: when the change history moves -- a teammate's save, an
  // approval, the agent -- every open page refetches. Cheap to ask, and
  // skipped while the tab is hidden.
  const lastVersion = useRef<number | null>(null)
  useEffect(() => {
    const check = () => {
      if (document.visibilityState === 'hidden') return
      fetchChangeVersion()
        .then(({ version }) => {
          if (lastVersion.current !== null && version !== lastVersion.current) {
            announceDataChanged()
            refresh()
          }
          lastVersion.current = version
        })
        .catch(() => {})
    }
    check()
    const timer = window.setInterval(check, CHANGE_POLL_MS)
    document.addEventListener('visibilitychange', check)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', check)
    }
  }, [refresh])

  // Back online: catch up on anything missed while away.
  useEffect(() => {
    if (online) refresh()
  }, [online, refresh])

  useEffect(() => setSidebarOpen(false), [route])

  // Search from anywhere: Ctrl or Cmd K, or "/" when not typing in a field.
  const navOn = flags.v3_nav === true
  useEffect(() => {
    if (!navOn) return
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const typing = !!target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable)
      if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setCommandOpen((open) => !open)
      } else if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault()
        setCommandOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navOn])

  // The tour: once per browser when the new navigation is on, and whenever Help asks for it.
  useEffect(() => {
    if (navOn && !tourSeen()) setTourOpen(true)
  }, [navOn])
  useEffect(() => {
    const show = () => setTourOpen(true)
    window.addEventListener(TOUR_EVENT, show)
    return () => window.removeEventListener(TOUR_EVENT, show)
  }, [])

  const toast = useCallback((title: string, subtitle?: string) => {
    setToastState({ title, subtitle })
    window.setTimeout(() => setToastState(null), 3200)
  }, [])

  // The main agent first, then any others alphabetically -- the order they appear everywhere.
  const sortedAgents = useMemo(
    () => [...agents].sort((a, b) => (a.role === b.role ? a.name.localeCompare(b.name) : a.role === 'boss' ? -1 : 1)),
    [agents],
  )

  const app: AppContext = {
    agents: sortedAgents,
    agentsById: new Map(agents.map((a) => [a.id, a])),
    pendingActions,
    inventory,
    runtime,
    restaurant,
    setRestaurant,
    apiStatus,
    canManage: canEditRestaurant(user),
    flags,
    navigate,
    refresh,
    toast,
    newTask: (agentKey) => setTaskAgent(agentKey ?? null),
    focusRunId,
    openAction: setDrawerAction,
    busyActionId,
    approve: async (action, overrides) => {
      setBusyActionId(action.id)
      try {
        const result = await approveAction(action.id, overrides)
        toast('Approved', result.applied_result ?? undefined)
        refresh()
        return true
      } catch (err) {
        toast('Could not approve', err instanceof Error ? err.message : String(err))
        return false
      } finally {
        setBusyActionId(null)
      }
    },
    reject: async (action) => {
      setBusyActionId(action.id)
      try {
        await rejectAction(action.id)
        toast('Rejected')
        refresh()
        return true
      } catch (err) {
        toast('Could not reject', err instanceof Error ? err.message : String(err))
        return false
      } finally {
        setBusyActionId(null)
      }
    },
  }

  const lowStock = inventory.filter((i) => i.quantity_on_hand <= i.reorder_threshold).length
  const openQuestions = questions.filter((q) => q.status === 'open').length
  const counts: Partial<Record<Route, { value: number; attention: boolean }>> = {
    invoices: { value: openQuestions, attention: openQuestions > 0 }, // questions are answered on the Invoices page
    approvals: { value: pendingActions.length, attention: pendingActions.length > 0 },
    inventory: { value: lowStock, attention: lowStock > 0 },
  }
  // On the price pages the top bar holds the agent's field; elsewhere, the Assistant Manager's controls.
  const pricePage = PRICE_ROUTES.has(route)

  const page = (() => {
    switch (route) {
      case 'digest':
        return <DigestPage app={app} />
      case 'menu-costs':
        return <MenuCostsPage app={app} />
      case 'usage':
        return <UsagePage app={app} />
      case 'channels':
        return <ChannelsPage app={app} />
      case 'ordering-prep':
        return <OrderingPage app={app} />
      case 'labor':
        return <LaborPage app={app} />
      case 'items':
        return <InvoiceHubPage app={app} section={route} />
      case 'item':
        return <ItemPage app={app} />
      case 'questions':
        return <InvoiceHubPage app={app} section={route} />
      case 'thread':
        return <InvoiceHubPage app={app} section={route} />
      case 'draft':
        return <DraftPage app={app} />
      case 'invoices':
        return <InvoiceHubPage app={app} section={route} />
      case 'approvals':
        return <ApprovalsPage app={app} />
      case 'runs':
        return <RunsPage app={app} />
      case 'agents':
        return <AgentsPage app={app} />
      case 'inventory':
        return <InventoryPage app={app} user={user} />
      case 'suppliers':
        return <SuppliersPage app={app} />
      case 'menu':
        return <MenuPage app={app} />
      case 'staff':
        return <StaffPage app={app} user={user} />
      case 'customers':
        return <CustomersPage app={app} />
      case 'connect':
        return <ConnectPage app={app} user={user} />
      case 'documents':
        return <InvoiceHubPage app={app} section={route} />
      case 'recipes':
        return <RecipesPage app={app} />
      case 'recipe':
        return <RecipePage app={app} />
      case 'po':
        return <PurchaseOrderPage app={app} />
      case 'calendar':
        return <CalendarPage app={app} />
      case 'prep':
        return readHashParam() ? <PrepSheetPage app={app} /> : <PrepSheetsPage app={app} />
      case 'receive':
        return flags.v3_receiving ? <ReceivePage app={app} /> : <InventoryPage app={app} user={user} />
      case 'autopilot':
        return flags.v3_autopilot && app.canManage ? <AutopilotPage app={app} /> : <InventoryPage app={app} user={user} />
      case 'count':
        return flags.v3_stock ? <CountPage app={app} /> : <InventoryPage app={app} user={user} />
      case 'variance':
        return flags.v3_stock ? <VariancePage app={app} /> : <InventoryPage app={app} user={user} />
      case 'insights':
        return app.canManage && navOn ? <InsightsPage app={app} /> : <OverviewPage app={app} />
      case 'tools':
        return navOn ? <ToolsPage app={app} /> : <OverviewPage app={app} />
      case 'profit':
        return app.canManage ? <ProfitPage app={app} /> : <OverviewPage app={app} />
      case 'overview':
        return <OverviewPage app={app} />
      case 'profile':
        return <ProfilePage app={app} user={user} />
      case 'history':
        return <HistoryPage app={app} />
      case 'help':
        return <HelpPage app={app} />
      default:
        return <OverviewPage app={app} />
    }
  })()

  const allowed = (item: { route: Route }) => app.canManage || !MANAGER_ROUTES.has(item.route)
  // Every group with the pages this person can open, flag-gated pages included: what the sidebar
  // draws and what search offers.
  const extra = (label: string | null): NavItem[] => {
    const more: NavItem[] = []
    if (label === 'Inventory' && flags.v3_autopilot) more.push({ route: 'autopilot', label: 'Ordering autopilot', icon: 'play' })
    if (label === 'Restaurant' && flags.v2_calendar) more.push({ route: 'calendar', label: 'Calendar', icon: 'clock' })
    if (label === 'Restaurant' && flags.v2_connect) more.push({ route: 'connect', label: 'Connections', icon: 'suppliers' })
    if (label === 'Menu' && flags.v2_documents) more.push({ route: 'recipes', label: 'Recipes', icon: 'menu' })
    if (label === 'Menu' && flags.v2_orders) more.push({ route: 'prep', label: 'Prep sheets', icon: 'inventory' })
    return more
  }
  const marginLabor = MARGIN_LABOR.filter((entry) => flags[entry.flag]).map((entry) => entry.item)
  // Today, then Inventory and Invoices & prices, then the newer groups, then the rest.
  const allGroups = [...NAV.slice(0, 3), ...(navOn ? [INSIGHTS_GROUP] : []), ...(marginLabor.length ? [{ label: 'Margin & labor', items: marginLabor }] : []), ...NAV.slice(3)]
    .map((group) => ({ label: group.label, items: [...group.items, ...extra(group.label)].filter(allowed) }))
    .filter((group) => group.items.length > 0)
  // In the demo the sidebar starts short; search still offers every page.
  const shortMenu = runtime?.demo_mode === true && !fullMenu
  const groups = shortMenu
    ? allGroups.map((group) => ({ label: group.label, items: group.items.filter((item) => DEMO_MENU.has(item.route)) })).filter((group) => group.items.length > 0)
    : allGroups
  const pageCount = allGroups.reduce((sum, group) => sum + group.items.length, 0)
  const tabs = shortMenu && flags.v3_receiving ? TABS.map((tab) => (tab.route === 'staff' ? { route: 'receive' as Route, label: 'Deliveries', icon: 'suppliers' as IconName } : tab)) : TABS
  const commandPages: CommandPage[] = allGroups.flatMap((group) =>
    group.items.map((item) => ({ label: item.label, href: `#${item.route}`, group: group.label ?? 'Today', icon: item.icon, keywords: PAGE_KEYWORDS[item.route] })),
  )
  // The tabs of Inventory and Suppliers have no sidebar entry of their own; search still reaches each one.
  if (flags.v3_stock)
    commandPages.push(
      { label: 'Count stock', href: '#count', group: 'Inventory', icon: 'check', keywords: PAGE_KEYWORDS.count },
      { label: 'Variance & waste', href: '#variance', group: 'Inventory', icon: 'trend', keywords: PAGE_KEYWORDS.variance },
    )
  // The sections of the Invoices page, by name.
  commandPages.push(
    { label: 'Questions', href: '#questions', group: 'Invoices', icon: 'approvals', keywords: PAGE_KEYWORDS.questions },
    { label: 'Items', href: '#items', group: 'Invoices', icon: 'inventory', keywords: PAGE_KEYWORDS.items },
    { label: 'Conversation', href: '#thread', group: 'Invoices', icon: 'chat', keywords: PAGE_KEYWORDS.thread },
  )
  if (flags.v2_documents) commandPages.push({ label: 'Documents', href: '#documents', group: 'Invoices', icon: 'clock', keywords: PAGE_KEYWORDS.documents })
  if (flags.v3_receiving) commandPages.push({ label: 'Deliveries', href: '#receive', group: 'Inventory', icon: 'suppliers', keywords: PAGE_KEYWORDS.receive })
  const navLink = (item: NavItem) => {
    const count = counts[item.route]
    return (
      <a key={item.route} className="nav-link" href={`#${item.route}`} aria-current={route === item.route || SECTION_OF[route] === item.route ? 'page' : undefined}>
        <Icon name={item.icon} />
        {item.label}
        {count && count.value > 0 && <span className={`nav-count${count.attention ? ' attention' : ''}`}>{count.value}</span>}
      </a>
    )
  }

  return (
    <div className="app">
      <aside className={`sidebar${sidebarOpen ? ' open' : ''}`} aria-label="Main navigation">
        <a className="brand" href="#overview">
          <span className="brand-mark">F</span>
          <span className="brand-text">
            <span className="brand-name">{restaurant.name ?? 'FullHouse'}</span>
            <span className="brand-sub">FullHouse</span>
          </span>
        </a>

        {navOn && (
          <button className="nav-search" onClick={() => setCommandOpen(true)}>
            <Icon name="search" size={16} />
            <span>Search</span>
            <kbd className="hide-mobile">{navigator.platform.toLowerCase().includes('mac') ? '⌘K' : 'Ctrl K'}</kbd>
          </button>
        )}

        {groups.map((group) => {
          const label = group.label
          const folded = navOn && !!label && collapsed.includes(label) && !group.items.some((item) => item.route === route)
          return (
            <nav key={label ?? 'main'} className="nav-group" aria-label={label ?? 'Today'}>
              {label &&
                (navOn ? (
                  <button className="nav-label nav-label-button" aria-expanded={!folded} onClick={() => toggleGroup(label)}>
                    {label}
                    <Icon name="chevron" size={14} className={folded ? 'nav-chevron folded' : 'nav-chevron'} />
                  </button>
                ) : (
                  <div className="nav-label">{label}</div>
                ))}
              {!folded && group.items.map(navLink)}
            </nav>
          )
        })}

        {runtime?.demo_mode && (
          <button className="nav-more" onClick={toggleFullMenu} aria-pressed={fullMenu}>
            {fullMenu ? 'Show the short menu' : `Show all ${pageCount} pages`}
          </button>
        )}

        <div className="sidebar-footer">
          <a className="sidebar-user" href="#profile" title="Your profile">
            <span className="avatar" aria-hidden="true">
              {user.name.trim().charAt(0).toUpperCase() || '?'}
            </span>
            <div className="sidebar-user-text">
              <div className="sidebar-user-name">{user.name}</div>
              <div className="faint sidebar-user-email">{user.email}</div>
            </div>
          </a>
          <button className="btn btn-secondary btn-sm sign-out" onClick={onSignOut}>
            Sign out
          </button>
          <FeedbackButton onSent={() => toast('Thanks, feedback sent', 'The people running FullHouse will see it.')} />
          <div className="row">
            <span
              className="dot"
              style={{ color: !online || apiStatus === 'error' ? 'var(--danger)' : apiStatus === 'ok' ? 'var(--success)' : 'var(--text-faint)' }}
            />
            {!online ? 'Offline' : apiStatus === 'ok' ? 'Connected' : apiStatus === 'error' ? 'Can’t reach FullHouse' : 'Connecting…'}
          </div>
          {runtime &&
            (runtime.ai_problem ? (
              <div className="faint" style={{ marginTop: 4 }} title={runtime.ai_problem}>
                AI assistant unavailable
              </div>
            ) : (
              <div className="faint" style={{ marginTop: 4 }} title={runtime.provider === 'scripted' ? 'No model is connected. Price questions are answered by rule from the invoices.' : undefined}>
                {runtime.provider === 'scripted' ? 'Rule-based assistant · no model' : `${runtime.provider} · ${runtime.model}`}
              </div>
            ))}
        </div>
      </aside>

      {sidebarOpen && <div className="overlay" style={{ zIndex: 25 }} onClick={() => setSidebarOpen(false)} />}

      <div className="main">
        <header className="topbar">
          <button className="btn btn-ghost btn-icon menu-button" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}>
            <Icon name="hamburger" />
          </button>
          {runtime?.demo_mode && (
            <div className="demo-banner" role="note">
              <span className="demo-pill">Live demo</span>
              <span className="hide-mobile">
                Change anything — it all resets{runtime.demo_resets_at ? ` ${relativeTime(runtime.demo_resets_at)}` : ' daily'}.
                {app.canManage && (
                  <>
                    {' '}Try{' '}
                    <button className="link-button" onClick={() => app.newTask('maestro')}>
                      a task for the Assistant Manager
                    </button>
                    .
                  </>
                )}
              </span>
            </div>
          )}
          <div className="topbar-spacer" />
          {(notices.items.length > 0 || flags.v3_stock || flags.v3_autopilot || flags.v3_receiving) && <NoticesBell notices={notices} onChange={setNotices} />}
          {navOn && (
            <button className="btn btn-ghost btn-icon topbar-search" aria-label="Search" title="Search (Ctrl or ⌘ K)" onClick={() => setCommandOpen(true)}>
              <Icon name="search" />
            </button>
          )}
          {!pricePage && pendingActions.length > 0 && route !== 'approvals' && (
            <a className="btn btn-secondary hide-mobile" href="#approvals">
              <span className="dot" style={{ color: 'var(--danger)' }} />
              {pendingActions.length} to review
            </a>
          )}
          {!pricePage && app.canManage && (
            <button className="btn btn-primary" onClick={() => app.newTask()}>
              <Icon name="plus" size={16} />
              New task
            </button>
          )}
          {pricePage && <AskField id="ask-desktop" className="pi-ask-top" onError={(m) => toast('Couldn’t ask that', m)} />}
        </header>

        {(!online || apiStatus === 'error') && (
          <div className="connection-banner" role="alert">
            <Icon name="alert" size={16} />
            <span>
              {!online
                ? 'You’re offline. You can look around, but changes won’t save until you’re back online.'
                : 'Can’t reach FullHouse right now. Changes won’t save until it’s back; we’ll keep trying.'}
            </span>
            {online && (
              <button className="btn btn-secondary btn-sm" onClick={refresh}>
                Try now
              </button>
            )}
          </div>
        )}

        {page}
      </div>

      <NewTaskModal
        open={taskAgent !== undefined}
        agents={sortedAgents}
        defaultAgentKey={taskAgent ?? null}
        onClose={() => setTaskAgent(undefined)}
        onSubmit={async (agentKey, task) => {
          const run = await triggerRun(agentKey, task)
          setTaskAgent(undefined)
          setFocusRunId(run.id)
          toast('Task started', 'Following it live in Agent activity.')
          navigate('runs')
        }}
      />

      {pricePage && (
        <div className="pi-ask-bar">
          <AskField id="ask-mobile" onError={(m) => toast('Couldn’t ask that', m)} />
        </div>
      )}

      <nav className="tabbar" aria-label="Daily tasks">
        {tabs.map((tab) => {
          const count = counts[tab.route]
          return (
            <a key={tab.route} className="tab" href={`#${tab.route}`} aria-current={route === tab.route ? 'page' : undefined}>
              <span className="tab-icon">
                <Icon name={tab.icon} size={20} />
                {count && count.value > 0 && (
                  <span className={`tab-count${count.attention ? ' attention' : ''}`} aria-hidden="true">
                    {count.value}
                  </span>
                )}
              </span>
              {tab.label}
              {/* Read after the name, not before it: "Recommend (1)". */}
              {count && count.value > 0 && <span className="visually-hidden"> ({count.value})</span>}
            </a>
          )
        })}
        <button className="tab" onClick={() => setSidebarOpen(true)}>
          <span className="tab-icon">
            <Icon name="hamburger" size={20} />
          </span>
          More
        </button>
      </nav>

      {flags.v3_voice && <VoiceButton app={app} />}
      <CommandBar open={commandOpen} onClose={() => setCommandOpen(false)} pages={commandPages} canAsk onError={(m) => toast('Couldn’t ask that', m)} />
      <Tour open={tourOpen && !commandOpen} onClose={() => setTourOpen(false)} />
      <ActionDrawer app={app} action={drawerAction} onClose={() => setDrawerAction(null)} />
      <Toast toast={toastState} />
    </div>
  )
}
