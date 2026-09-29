import { createBrowserRouter, type RouteObject } from 'react-router';

import { AppShell } from './components/layout/AppShell';
import { RequireAuth } from './components/layout/RequireAuth';
import { DashboardPage } from './pages/DashboardPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { AccountPage, AuditPage, CompaniesPage, ResellersPage } from './pages/admin/AdminPages';
import { CustomFieldsPage, PermissionsPage } from './pages/admin/PermissionsPage';
import { ReleasesPage } from './pages/admin/ReleasesPage';
import { UsersPage } from './pages/admin/UsersPage';
import { AgentDetailPage } from './pages/agents/AgentDetailPage';
import { AgentsPage } from './pages/agents/AgentsPage';
import { ForgotPasswordPage, LimitedSessionPage, LoginPage, ResetPasswordPage } from './pages/auth/AuthPages';
import { CustomerDetailPage, CustomersPage } from './pages/customers/CustomersPages';
import { DeviceDetailPage } from './pages/park/DeviceDetailPage';
import { DiscoveriesPage } from './pages/park/DiscoveriesPage';
import { ParkPage } from './pages/park/ParkPage';

export const routes: RouteObject[] = [
  { path: '/login', element: <LoginPage /> },
  { path: '/esqueci-senha', element: <ForgotPasswordPage /> },
  { path: '/redefinir-senha', element: <ResetPasswordPage /> },
  { path: '/sessao', element: <LimitedSessionPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <AppShell />,
        handle: { crumb: 'Início' },
        children: [
          { index: true, element: <DashboardPage /> },
          {
            path: 'parque',
            handle: { crumb: 'Equipamentos' },
            children: [
              { index: true, element: <ParkPage /> },
              { path: ':deviceId', element: <DeviceDetailPage />, handle: { crumb: 'Equipamento' } },
            ],
          },
          {
            path: 'coletores',
            handle: { crumb: 'Coletores' },
            children: [
              { index: true, element: <AgentsPage /> },
              { path: ':agentId', element: <AgentDetailPage />, handle: { crumb: 'Coletor' } },
            ],
          },
          {
            path: 'clientes',
            handle: { crumb: 'Clientes' },
            children: [
              { index: true, element: <CustomersPage /> },
              { path: ':customerId', element: <CustomerDetailPage />, handle: { crumb: 'Cliente' } },
            ],
          },
          { path: 'descobertas', element: <DiscoveriesPage />, handle: { crumb: 'Descobertas' } },
          { path: 'usuarios', element: <UsersPage />, handle: { crumb: 'Usuários' } },
          { path: 'permissoes', element: <PermissionsPage />, handle: { crumb: 'Permissões' } },
          { path: 'campos-personalizados', element: <CustomFieldsPage />, handle: { crumb: 'Campos personalizados' } },
          { path: 'revendas', element: <ResellersPage />, handle: { crumb: 'Revendas' } },
          { path: 'empresas', element: <CompaniesPage />, handle: { crumb: 'Empresas' } },
          { path: 'auditoria', element: <AuditPage />, handle: { crumb: 'Auditoria' } },
          { path: 'versoes', element: <ReleasesPage />, handle: { crumb: 'Versões' } },
          { path: 'conta', element: <AccountPage />, handle: { crumb: 'Minha conta' } },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];

export function createRouter() {
  return createBrowserRouter(routes);
}
