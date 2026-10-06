import { useQuery } from '@tanstack/react-query';
import {
  Bell,
  Building2,
  ChevronDown,
  ChevronRight,
  Download,
  FileBarChart,
  LayoutDashboard,
  LogOut,
  Menu as MenuIcon,
  Moon,
  MoreHorizontal,
  Plug,
  Printer,
  Server,
  Settings,
  Shield,
  SlidersHorizontal,
  Sun,
  SunMoon,
  UserCircle,
  Users,
  X,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link, NavLink, Outlet, useLocation, useMatches, useNavigate } from 'react-router';

import logoMark from '../../assets/logo-dc.svg';
import { api, unwrap } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { useLiveEvents, type LiveStatus } from '../../lib/live';
import { useTheme, type ThemeChoice } from '../../lib/theme';
import { cn } from '../../lib/utils';
import { product } from '../../product';
import { Button } from '../ui/button';
import { Menu, MenuItem, MenuSeparator } from '../ui/dialog';
import { Tooltip } from '../ui/primitives';

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  permission?: string;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const ICON = 'h-4 w-4 shrink-0';

// Menu principal. As telas de apoio (Descobertas, Computadores, Trocas de toner, Mapa, Empresas,
// Permissões, Versões…) continuam acessíveis pelos atalhos das telas a que pertencem.
const GROUPS: NavGroup[] = [
  {
    label: 'Monitoramento',
    items: [
      { to: '/', label: 'Visão geral', icon: <LayoutDashboard className={ICON} /> },
      { to: '/parque', label: 'Parque', icon: <Printer className={ICON} />, permission: 'devices.read' },
      { to: '/coletores', label: 'Coletores', icon: <Server className={ICON} />, permission: 'agents.read' },
      { to: '/alertas', label: 'Alertas', icon: <Bell className={ICON} />, permission: 'alerts.read' },
    ],
  },
  {
    label: 'Gestão',
    items: [
      { to: '/clientes', label: 'Clientes', icon: <Building2 className={ICON} />, permission: 'customers.read' },
      { to: '/relatorios', label: 'Relatórios', icon: <FileBarChart className={ICON} />, permission: 'reports.read' },
    ],
  },
];

const SETTINGS: NavItem[] = [
  { to: '/usuarios', label: 'Usuários', icon: <Users className={ICON} />, permission: 'users.read' },
  {
    to: '/perfis',
    label: 'Perfis de modelos',
    icon: <SlidersHorizontal className={ICON} />,
    permission: 'profiles.read',
  },
  { to: '/integracao', label: 'Integração ERP', icon: <Plug className={ICON} />, permission: 'integration.read' },
  { to: '/auditoria', label: 'Auditoria', icon: <Shield className={ICON} />, permission: 'audit.read' },
  { to: '/downloads', label: 'Downloads', icon: <Download className={ICON} />, permission: 'agents.read' },
];

export interface Crumb {
  crumb?: string | ((data: unknown) => string);
}

function Breadcrumb() {
  const matches = useMatches();
  const crumbs = matches
    .map((m) => ({ path: m.pathname, handle: m.handle as Crumb | undefined }))
    .filter((m) => typeof m.handle?.crumb === 'string') as { path: string; handle: { crumb: string } }[];
  if (!crumbs.length) return null;
  return (
    <nav aria-label="Você está em" className="flex items-center gap-1 text-xs text-slate-500">
      <span>Você está em:</span>
      {crumbs.map((c, i) => (
        <span key={c.path} className="flex items-center gap-1">
          {i > 0 ? <ChevronRight className="h-3 w-3" aria-hidden /> : null}
          {i < crumbs.length - 1 ? (
            <Link to={c.path} className="hover:underline">
              {c.handle.crumb}
            </Link>
          ) : (
            <span className="font-medium text-slate-700 dark:text-slate-300">{c.handle.crumb}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

const LIVE_LABEL: Record<LiveStatus, { text: string; dot: string }> = {
  live: { text: 'Ao vivo', dot: 'bg-emerald-500' },
  connecting: { text: 'Conectando…', dot: 'bg-amber-400' },
  offline: { text: 'Sem atualização ao vivo', dot: 'bg-red-500' },
};

const THEME_ICON: Record<ThemeChoice, ReactNode> = {
  light: <Sun className="h-4 w-4" />,
  dark: <Moon className="h-4 w-4" />,
  system: <SunMoon className="h-4 w-4" />,
};

/** Item do menu (grafite, estilo PaperCut): ativo com fundo mais claro, texto branco e barra verde à esquerda. */
function SideLink({ item, onNavigate }: { item: NavItem; onNavigate: () => void }) {
  return (
    <NavLink
      to={item.to}
      end={item.to === '/'}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          'relative flex h-10 items-center gap-3 rounded-md px-3 text-sm text-nav-200 transition-colors',
          'hover:bg-white/5 hover:text-white',
          isActive &&
            'bg-white/10 font-semibold text-white before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-r before:bg-brand-400 hover:bg-white/10',
        )
      }
    >
      {item.icon}
      <span className="truncate">{item.label}</span>
    </NavLink>
  );
}

function GroupLabel({ children }: { children: ReactNode }) {
  return <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-nav-400">{children}</p>;
}

function Sidebar({ onNavigate }: { onNavigate: () => void }) {
  const { user, logout, can } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [theme, setTheme] = useTheme();
  const visible = (n: NavItem) => !n.permission || can(n.permission);
  const settings = SETTINGS.filter(visible);
  const inSettings = settings.some((s) => pathname === s.to || pathname.startsWith(`${s.to}/`));
  // Configurações começa fechado; abre sozinho quando a tela atual é uma das configurações.
  const [settingsOpen, setSettingsOpen] = useState(false);
  const open = settingsOpen || inSettings;

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 shrink-0 items-center gap-2.5 border-b border-white/5 px-5 text-[15px] font-semibold text-white">
        <img src={logoMark} alt="" className="h-8 w-auto" aria-hidden />
        <span className="leading-tight">
          {product.name}
          <span className="block text-[11px] font-medium tracking-wide text-nav-400">Daticopy</span>
        </span>
      </div>
      <nav className="scroll-thin flex flex-1 flex-col gap-6 overflow-y-auto px-3 pt-5" aria-label="Menu principal">
        {GROUPS.map((g) => {
          const items = g.items.filter(visible);
          if (!items.length) return null;
          return (
            <div key={g.label}>
              <GroupLabel>{g.label}</GroupLabel>
              <div className="flex flex-col gap-0.5">
                {items.map((n) => (
                  <SideLink key={n.to} item={n} onNavigate={onNavigate} />
                ))}
              </div>
            </div>
          );
        })}
      </nav>
      <div className="shrink-0 border-t border-white/5 px-3 py-3">
        {settings.length ? (
          <nav className="mb-2" aria-label="Configurações">
            <button
              type="button"
              className={cn(
                'flex h-10 w-full items-center gap-3 rounded-md px-3 text-sm text-nav-200 hover:bg-white/5 hover:text-white',
                inSettings && 'font-semibold text-white',
              )}
              aria-expanded={open}
              aria-controls="menu-configuracoes"
              onClick={() => {
                setSettingsOpen(!open);
              }}
            >
              <Settings className={ICON} />
              <span className="flex-1 text-left">Configurações</span>
              <ChevronDown className={cn('h-4 w-4 transition-transform', open && 'rotate-180')} aria-hidden />
            </button>
            {open ? (
              <div id="menu-configuracoes" className="mt-0.5 flex flex-col gap-0.5 pl-3">
                {settings.map((n) => (
                  <SideLink key={n.to} item={n} onNavigate={onNavigate} />
                ))}
              </div>
            ) : null}
          </nav>
        ) : null}
        <div className="flex items-center gap-2 rounded-md px-2 py-1.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-nav-700 text-xs font-semibold text-white">
            {initials(user?.name)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-white" data-testid="sidebar-user">
              {user?.name}
            </p>
            <p className="truncate text-xs text-nav-400">{user?.role_name}</p>
          </div>
          <Menu
            trigger={
              <Button
                variant="ghost"
                size="icon"
                aria-label="Menu do usuário"
                className="text-nav-200 hover:bg-white/10 hover:text-white dark:hover:bg-white/10"
              >
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            }
          >
            <div className="px-2 py-1.5 text-xs text-slate-500">{user?.email}</div>
            <MenuSeparator />
            <MenuItem
              onSelect={() => {
                onNavigate();
                void navigate('/conta');
              }}
            >
              <UserCircle className="h-4 w-4" /> Minha conta
            </MenuItem>
            {(['light', 'dark', 'system'] as const).map((t) => (
              <MenuItem
                key={t}
                onSelect={() => {
                  setTheme(t);
                }}
              >
                {THEME_ICON[t]} Tema {t === 'light' ? 'claro' : t === 'dark' ? 'escuro' : 'do sistema'}
                {theme === t ? ' ✓' : ''}
              </MenuItem>
            ))}
          </Menu>
        </div>
        <button
          type="button"
          className="mt-1 flex h-10 w-full items-center gap-3 rounded-md px-3 text-sm text-nav-200 hover:bg-white/5 hover:text-white"
          onClick={() => void logout()}
        >
          <LogOut className={ICON} /> Sair
        </button>
      </div>
    </div>
  );
}

function initials(name: string | undefined): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? '') + (parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? '') : '')).toUpperCase() || '?';
}

export function AppShell() {
  const { user, can } = useAuth();
  const live = useLiveEvents(Boolean(user));
  const [open, setOpen] = useState(false);
  const alerts = useQuery({
    queryKey: ['alert-counts'],
    queryFn: () => unwrap(api.GET('/api/v1/alerts/counts')),
    enabled: can('alerts.read'),
    refetchInterval: 60_000,
  });
  const openAlerts = alerts.data?.open ?? 0;
  const criticalAlerts = alerts.data?.critical ?? 0;
  const close = () => {
    setOpen(false);
  };

  return (
    <div className="flex h-full">
      <aside className="hidden w-[248px] shrink-0 bg-nav-900 lg:block">
        <Sidebar onNavigate={close} />
      </aside>
      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button type="button" aria-label="Fechar menu" className="absolute inset-0 bg-zinc-950/50" onClick={close} />
          <aside className="absolute inset-y-0 left-0 w-[248px] bg-nav-900 shadow-xl">
            <button
              type="button"
              aria-label="Fechar menu"
              className="absolute right-3 top-5 text-nav-200"
              onClick={close}
            >
              <X className="h-5 w-5" />
            </button>
            <Sidebar onNavigate={close} />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-slate-200 bg-white px-3 dark:border-slate-800 dark:bg-slate-900 sm:px-6">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            aria-label="Abrir menu"
            onClick={() => {
              setOpen(true);
            }}
          >
            <MenuIcon className="h-5 w-5" />
          </Button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{user?.reseller_name}</p>
            <Breadcrumb />
          </div>
          <Tooltip content={LIVE_LABEL[live].text}>
            <span className="flex items-center gap-1.5 text-xs text-slate-500" data-testid="live-status">
              <span className={cn('h-2 w-2 rounded-full', LIVE_LABEL[live].dot)} aria-hidden />
              <span className="hidden sm:inline">{LIVE_LABEL[live].text}</span>
            </span>
          </Tooltip>
          <Tooltip
            content={
              openAlerts
                ? `${String(openAlerts)} alerta(s) aberto(s)${criticalAlerts ? `, ${String(criticalAlerts)} crítico(s)` : ''}`
                : 'Nenhum alerta aberto'
            }
          >
            <Link
              to="/alertas"
              className="relative rounded p-1.5 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
              aria-label="Alertas"
              data-testid="alerts-bell"
            >
              <Bell className="h-5 w-5" />
              {openAlerts ? (
                <span
                  className={cn(
                    'absolute -right-0.5 -top-0.5 rounded-full px-1 text-[10px] font-bold text-white',
                    criticalAlerts ? 'bg-red-600' : 'bg-amber-500',
                  )}
                >
                  {openAlerts}
                </span>
              ) : null}
            </Link>
          </Tooltip>
        </header>
        <main className="scroll-thin min-w-0 flex-1 overflow-auto p-3 sm:p-6">
          <div className="mx-auto max-w-[1600px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
