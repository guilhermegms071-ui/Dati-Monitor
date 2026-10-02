import { createBrowserRouter, type RouteObject } from 'react-router';

import { AppShell } from './components/layout/AppShell';
import { RequireAuth } from './components/layout/RequireAuth';
import { DashboardPage } from './pages/DashboardPage';
import { NotFoundPage } from './pages/NotFoundPage';
import { AccountPage, AuditPage, CompaniesPage, ResellersPage } from './pages/admin/AdminPages';
import { CustomFieldsPage, PermissionsPage } from './pages/admin/PermissionsPage';
import { IntegrationPage } from './pages/admin/IntegrationPage';
import { ReleasesPage } from './pages/admin/ReleasesPage';
import { UsersPage } from './pages/admin/UsersPage';
import { AgentDetailPage } from './pages/agents/AgentDetailPage';
import { AgentsPage } from './pages/agents/AgentsPage';
import { ForgotPasswordPage, LimitedSessionPage, LoginPage, ResetPasswordPage } from './pages/auth/AuthPages';
import { CustomerDetailPage, CustomersPage } from './pages/customers/CustomersPages';
import { SitesMapPage } from './pages/customers/SitesMapPage';
import { DeviceDetailPage } from './pages/park/DeviceDetailPage';
import { DiscoveriesPage } from './pages/park/DiscoveriesPage';
import { PrinterAlertsPage, ReplacementsPage } from './pages/park/SupplyPages';
import { AlertsPage } from './pages/alerts/AlertsPage';
import { ParkPage } from './pages/park/ParkPage';
import { ProfileDetailPage, ProfilesPage } from './pages/profiles/ProfilesPages';
import { ReportsPage } from './pages/reports/ReportsPage';

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
          {
            path: 'perfis',
            handle: { crumb: 'Perfis de modelos' },
            children: [
              { index: true, element: <ProfilesPage /> },
              { path: ':profileKey', element: <ProfileDetailPage />, handle: { crumb: 'Perfil' } },
            ],
          },
          { path: 'relatorios', element: <ReportsPage />, handle: { crumb: 'Relatórios' } },
          { path: 'mapa', element: <SitesMapPage />, handle: { crumb: 'Mapa dos locais' } },
          { path: 'integracao', element: <IntegrationPage />, handle: { crumb: 'Integração ERP' } },
          { path: 'descobertas', element: <DiscoveriesPage />, handle: { crumb: 'Descobertas' } },
          { path: 'alertas', element: <AlertsPage />, handle: { crumb: 'Alertas' } },
          { path: 'trocas-de-toner', element: <ReplacementsPage />, handle: { crumb: 'Trocas de toner' } },
          { path: 'alertas-da-impressora', element: <PrinterAlertsPage />, handle: { crumb: 'Alertas da impressora' } },
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
