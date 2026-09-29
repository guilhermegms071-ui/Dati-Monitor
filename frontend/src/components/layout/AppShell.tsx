import { useQuery } from '@tanstack/react-query';
import {
  Bell,
  Building2,
  ChevronRight,
  ClipboardList,
  LayoutDashboard,
  LogOut,
  Menu as MenuIcon,
  Monitor,
  PackageCheck,
  Moon,
  Printer,
  Server,
  Sun,
  SunMoon,
  UserCircle,
  Users,
  X,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link, NavLink, Outlet, useMatches, useNavigate } from 'react-router';

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
  superadmin?: boolean;
}

const NAV: NavItem[] = [
  { to: '/', label: 'Dashboard', icon: <LayoutDashboard className="h-4 w-4" /> },
  { to: '/parque', label: 'Equipamentos', icon: <Printer className="h-4 w-4" />, permission: 'devices.read' },
  { to: '/coletores', label: 'Coletores', icon: <Server className="h-4 w-4" />, permission: 'agents.read' },
  { to: '/clientes', label: 'Clientes', icon: <Building2 className="h-4 w-4" />, permission: 'customers.read' },
  { to: '/empresas', label: 'Empresas', icon: <Building2 className="h-4 w-4" />, permission: 'companies.write' },
  { to: '/revendas', label: 'Revendas', icon: <Monitor className="h-4 w-4" />, superadmin: true },
  { to: '/usuarios', label: 'Usuários', icon: <Users className="h-4 w-4" />, permission: 'users.read' },
  { to: '/auditoria', label: 'Auditoria', icon: <ClipboardList className="h-4 w-4" />, permission: 'audit.read' },
  { to: '/versoes', label: 'Versões', icon: <PackageCheck className="h-4 w-4" />, permission: 'agents.read' },
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

export function AppShell() {
  const { user, logout, can } = useAuth();
  const navigate = useNavigate();
  const live = useLiveEvents(Boolean(user));
  const [theme, setTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const alerts = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => unwrap(api.GET('/api/v1/dashboard')),
    enabled: can('devices.read'),
  });
  const items = NAV.filter((n) => (n.superadmin ? user?.role === 'superadmin' : !n.permission || can(n.permission)));
  const openAlerts = alerts.data?.cards.alerts_open ?? 0;

  const nav = (
    <nav className="flex flex-col gap-0.5 p-2" aria-label="Menu principal">
      {items.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.to === '/'}
          onClick={() => {
            setOpen(false);
          }}
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-slate-300 hover:bg-slate-800 hover:text-white',
              isActive && 'bg-slate-800 font-medium text-white',
            )
          }
        >
          {n.icon}
          {n.label}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="flex h-full">
      <aside className="hidden w-56 shrink-0 flex-col bg-slate-900 lg:flex">
        <div className="flex h-14 items-center gap-2 px-4 font-semibold text-white">
          <Printer className="h-5 w-5 text-brand-500" aria-hidden />
          {product.name}
        </div>
        {nav}
      </aside>
      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Fechar menu"
            className="absolute inset-0 bg-slate-950/60"
            onClick={() => {
              setOpen(false);
            }}
          />
          <aside className="absolute inset-y-0 left-0 w-64 bg-slate-900">
            <div className="flex h-14 items-center justify-between px-4 font-semibold text-white">
              {product.name}
              <button
                type="button"
                aria-label="Fechar menu"
                onClick={() => {
                  setOpen(false);
                }}
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            {nav}
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-3 border-b border-slate-200 bg-white px-3 dark:border-slate-800 dark:bg-slate-900 sm:px-5">
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
            <p className="truncate text-sm font-medium">{user?.reseller_name}</p>
            <Breadcrumb />
          </div>
          <Tooltip content={LIVE_LABEL[live].text}>
            <span className="flex items-center gap-1.5 text-xs text-slate-500" data-testid="live-status">
              <span className={cn('h-2 w-2 rounded-full', LIVE_LABEL[live].dot)} aria-hidden />
              <span className="hidden sm:inline">{LIVE_LABEL[live].text}</span>
            </span>
          </Tooltip>
          <Tooltip content={openAlerts ? `${String(openAlerts)} alerta(s) aberto(s)` : 'Nenhum alerta aberto'}>
            <span className="relative p-1.5 text-slate-500" aria-label="Alertas">
              <Bell className="h-5 w-5" />
              {openAlerts ? (
                <span className="absolute -right-0.5 -top-0.5 rounded-full bg-red-600 px-1 text-[10px] font-bold text-white">
                  {openAlerts}
                </span>
              ) : null}
            </span>
          </Tooltip>
          <Menu
            trigger={
              <Button variant="ghost" size="sm" aria-label="Menu do usuário">
                <UserCircle className="h-5 w-5" />
                <span className="hidden max-w-40 truncate sm:inline">{user?.name}</span>
              </Button>
            }
          >
            <div className="px-2 py-1.5 text-xs text-slate-500">
              {user?.email}
              <br />
              {user?.role_name}
            </div>
            <MenuSeparator />
            <MenuItem
              onSelect={() => {
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
            <MenuSeparator />
            <MenuItem
              onSelect={() => {
                void logout();
              }}
            >
              <LogOut className="h-4 w-4" /> Sair
            </MenuItem>
          </Menu>
        </header>
        <main className="scroll-thin min-w-0 flex-1 overflow-auto p-3 sm:p-5">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
