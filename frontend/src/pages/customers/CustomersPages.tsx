import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router';

import { AgentState, DeviceStatus, RoleBadge } from '../../components/domain';
import { LoadMore } from '../../components/paging';
import { CompanyPicker } from '../../components/pickers';
import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Input } from '../../components/ui/form';
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  KeyValue,
  PageHeader,
  RelativeTime,
  Spinner,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '../../components/ui/primitives';
import { api, downloadFile, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtCommunication, fmtInt } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';
import { siteAddress } from '../../lib/viacep';

import { SiteDialog } from './SiteDialog';

type Customer = Schemas['CustomerOut'];

export function CustomersPage() {
  const { can } = useAuth();
  const [q, setQ] = useState('');
  const [editing, setEditing] = useState<Customer | 'new' | null>(null);
  const { query: list, rows } = useCursorList<Customer>(['customers', q], (cursor) =>
    unwrap(api.GET('/api/v1/customers', { params: { query: { q: q || null, limit: PAGE_SIZE, cursor } } })),
  );
  return (
    <div className="space-y-3">
      <PageHeader
        title="Clientes"
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                void downloadFile(
                  `/api/v1/customers/export?format=xlsx${q ? `&q=${encodeURIComponent(q)}` : ''}`,
                  'clientes.xlsx',
                ).catch((err: unknown) => {
                  showError(err, 'Exportação falhou');
                });
              }}
            >
              <Download className="h-3.5 w-3.5" /> Exportar
            </Button>
            {can('customers.create') ? (
              <Button
                size="sm"
                onClick={() => {
                  setEditing('new');
                }}
              >
                <Plus className="h-4 w-4" /> Novo cliente
              </Button>
            ) : null}
          </>
        }
      />
      <Card className="p-3">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
          <Input
            className="pl-8"
            placeholder="Nome, CNPJ ou código ERP"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
            }}
            aria-label="Pesquisar clientes"
          />
        </div>
      </Card>
      <Card className="overflow-hidden">
        {list.isPending ? (
          <Spinner />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : !rows.length ? (
          <EmptyState title="Nenhum cliente" />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2 text-left">Cliente</th>
                <th className="px-3 py-2 text-left">CNPJ</th>
                <th className="px-3 py-2 text-left">Contato</th>
                <th className="px-3 py-2 text-left">Código ERP</th>
                <th className="px-3 py-2 text-left">Situação</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-2">
                    <Link
                      to={`/clientes/${c.id}`}
                      className="font-medium text-brand-600 hover:underline dark:text-brand-100"
                    >
                      {c.name}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-xs">{c.cnpj ?? '—'}</td>
                  <td className="px-3 py-2 text-xs">
                    {c.contact_name ?? '—'}
                    {c.phone ? ` · ${c.phone}` : ''}
                  </td>
                  <td className="px-3 py-2 text-xs">{c.erp_code ?? '—'}</td>
                  <td className="px-3 py-2">{c.active ? <Badge tone="green">Ativo</Badge> : <Badge>Inativo</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <LoadMore query={list} shown={rows.length} />
      </Card>
      {editing ? (
        <CustomerDialog
          customer={editing === 'new' ? null : editing}
          onClose={() => {
            setEditing(null);
          }}
        />
      ) : null}
    </div>
  );
}

function CustomerDialog({ customer, onClose }: { customer: Customer | null; onClose: () => void }) {
  const qc = useQueryClient();
  // Cliente novo: a primeira empresa da revenda já vem escolhida (a maioria das revendas tem uma só).
  const firstCompany = useQuery({
    queryKey: ['companies', 'first'],
    queryFn: () => unwrap(api.GET('/api/v1/companies', { params: { query: { limit: 1 } } })),
    enabled: !customer,
  });
  const [form, setForm] = useState({
    company_id: customer?.company_id ?? '',
    name: customer?.name ?? '',
    cnpj: customer?.cnpj ?? '',
    contact_name: customer?.contact_name ?? '',
    phone: customer?.phone ?? '',
    email: customer?.email ?? '',
    erp_code: customer?.erp_code ?? '',
    active: customer?.active ?? true,
  });
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<typeof form>) => {
    setForm((f) => ({ ...f, ...p }));
  };
  const nullable = (v: string) => (v.trim() ? v.trim() : null);
  const companyId = form.company_id || firstCompany.data?.items[0]?.id || '';
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={customer ? 'Editar cliente' : 'Novo cliente'}
      footer={
        <Button
          loading={busy}
          onClick={() => {
            setBusy(true);
            const body = {
              name: form.name,
              cnpj: nullable(form.cnpj),
              contact_name: nullable(form.contact_name),
              phone: nullable(form.phone),
              email: nullable(form.email),
              erp_code: nullable(form.erp_code),
              active: form.active,
            };
            const req = customer
              ? unwrap(
                  api.PATCH('/api/v1/customers/{customer_id}', {
                    params: { path: { customer_id: customer.id } },
                    body: { ...body, company_id: companyId },
                  }),
                )
              : unwrap(api.POST('/api/v1/customers', { body: { ...body, company_id: companyId } }));
            req
              .then(() => {
                showSuccess('Cliente salvo');
                void qc.invalidateQueries({ queryKey: ['customers'] });
                if (customer) void qc.invalidateQueries({ queryKey: ['customer', customer.id] });
                onClose();
              })
              .catch((err: unknown) => {
                showError(err, 'Cliente não salvo');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          Salvar
        </Button>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Empresa" htmlFor="c-company" className="sm:col-span-2">
          <CompanyPicker
            id="c-company"
            value={companyId}
            onChange={(id) => {
              set({ company_id: id });
            }}
          />
        </Field>
        <Field label="Nome" htmlFor="c-name" className="sm:col-span-2">
          <Input
            id="c-name"
            value={form.name}
            onChange={(e) => {
              set({ name: e.target.value });
            }}
          />
        </Field>
        <Field label="CNPJ" htmlFor="c-cnpj">
          <Input
            id="c-cnpj"
            value={form.cnpj}
            onChange={(e) => {
              set({ cnpj: e.target.value });
            }}
          />
        </Field>
        <Field label="Código no ERP" htmlFor="c-erp">
          <Input
            id="c-erp"
            value={form.erp_code}
            onChange={(e) => {
              set({ erp_code: e.target.value });
            }}
          />
        </Field>
        <Field label="Contato" htmlFor="c-contact">
          <Input
            id="c-contact"
            value={form.contact_name}
            onChange={(e) => {
              set({ contact_name: e.target.value });
            }}
          />
        </Field>
        <Field label="Telefone" htmlFor="c-phone">
          <Input
            id="c-phone"
            value={form.phone}
            onChange={(e) => {
              set({ phone: e.target.value });
            }}
          />
        </Field>
        <Field label="E-mail" htmlFor="c-email" className="sm:col-span-2">
          <Input
            id="c-email"
            value={form.email}
            onChange={(e) => {
              set({ email: e.target.value });
            }}
          />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.active}
            onChange={(e) => {
              set({ active: e.target.checked });
            }}
          />{' '}
          Ativo
        </label>
      </div>
    </Dialog>
  );
}

export function CustomerDetailPage() {
  const { customerId = '' } = useParams();
  const { can } = useAuth();
  const [editing, setEditing] = useState(false);
  const q = useQuery({
    queryKey: ['customer', customerId],
    queryFn: () =>
      unwrap(api.GET('/api/v1/customers/{customer_id}', { params: { path: { customer_id: customerId } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const c = q.data;
  return (
    <div className="space-y-4">
      <PageHeader
        title={c.name}
        subtitle={c.cnpj ?? undefined}
        actions={
          can('customers.update') ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setEditing(true);
              }}
            >
              Editar
            </Button>
          ) : null
        }
      />
      <Tabs defaultValue="sites">
        <TabsList>
          <TabsTrigger value="sites">Locais</TabsTrigger>
          <TabsTrigger value="agents">Coletores</TabsTrigger>
          <TabsTrigger value="devices">Equipamentos</TabsTrigger>
          <TabsTrigger value="data">Contatos e ERP</TabsTrigger>
        </TabsList>
        <TabsContent value="sites">
          <SitesTab customerId={c.id} canWrite={can('customers.update')} />
        </TabsContent>
        <TabsContent value="agents">
          <CustomerAgents customerId={c.id} />
        </TabsContent>
        <TabsContent value="devices">
          <CustomerDevices customerId={c.id} />
        </TabsContent>
        <TabsContent value="data">
          <Card className="p-4">
            <KeyValue
              items={[
                ['Contato', c.contact_name ?? '—'],
                ['Telefone', c.phone ?? '—'],
                ['E-mail', c.email ?? '—'],
                ['Código no ERP', c.erp_code ?? '—'],
                ['Situação', c.active ? 'Ativo' : 'Inativo'],
              ]}
            />
          </Card>
        </TabsContent>
      </Tabs>
      {editing ? (
        <CustomerDialog
          customer={c}
          onClose={() => {
            setEditing(false);
          }}
        />
      ) : null}
    </div>
  );
}

function SitesTab({ customerId, canWrite }: { customerId: string; canWrite: boolean }) {
  const [editing, setEditing] = useState<Schemas['SiteOut'] | 'new' | null>(null);
  const { query: q, rows } = useCursorList<Schemas['SiteOut']>(['sites', customerId], (cursor) =>
    unwrap(api.GET('/api/v1/sites', { params: { query: { customer_id: customerId, limit: PAGE_SIZE, cursor } } })),
  );
  return (
    <Card>
      <CardHeader
        title="Locais"
        subtitle="Cada local tem seus coletores, faixas de IP e credenciais SNMP."
        actions={
          canWrite ? (
            <Button
              size="sm"
              onClick={() => {
                setEditing('new');
              }}
            >
              <Plus className="h-4 w-4" /> Novo local
            </Button>
          ) : null
        }
      />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : !rows.length ? (
        <EmptyState title="Nenhum local" />
      ) : (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {rows.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
              <span>
                <span className="font-medium">{s.name}</span>
                {siteAddress(s) ? <span className="text-xs text-slate-500"> · {siteAddress(s)}</span> : null}
                {s.auto_activate_devices ? <Badge className="ml-2">ativa descobertos</Badge> : null}
              </span>
              <span className="flex items-center gap-2">
                <Link to={`/parque?site=${s.id}`} className="text-xs text-brand-600 hover:underline">
                  ver equipamentos
                </Link>
                {canWrite ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setEditing(s);
                    }}
                  >
                    Editar
                  </Button>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}
      <LoadMore query={q} shown={rows.length} />
      {editing ? (
        <SiteDialog
          customerId={customerId}
          site={editing === 'new' ? null : editing}
          onClose={() => {
            setEditing(null);
          }}
        />
      ) : null}
    </Card>
  );
}

function CustomerAgents({ customerId }: { customerId: string }) {
  const { query: q, rows } = useCursorList<Schemas['AgentOut']>(['agents', 'customer', customerId], (cursor) =>
    unwrap(api.GET('/api/v1/agents', { params: { query: { customer_id: customerId, limit: PAGE_SIZE, cursor } } })),
  );
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!rows.length) return <EmptyState title="Nenhum coletor neste cliente" />;
  return (
    <Card>
      <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
        {rows.map((a) => (
          <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
            <Link to={`/coletores/${a.id}`} className="font-medium text-brand-600 hover:underline dark:text-brand-100">
              {a.name}
            </Link>
            <span className="flex items-center gap-2 text-xs">
              {a.site_name} <RoleBadge role={a.cluster_role} />{' '}
              <AgentState state={a.state} wsConnected={a.ws_connected} /> <RelativeTime value={a.last_seen_at} />
            </span>
          </li>
        ))}
      </ul>
      <LoadMore query={q} shown={rows.length} />
    </Card>
  );
}

function CustomerDevices({ customerId }: { customerId: string }) {
  const {
    query: q,
    rows,
    total,
  } = useCursorList<Schemas['ParkRow']>(['park', 'customer', customerId], (cursor) =>
    unwrap(api.GET('/api/v1/park', { params: { query: { customer_id: customerId, limit: PAGE_SIZE, cursor } } })),
  );
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!rows.length) return <EmptyState title="Nenhum equipamento neste cliente" />;
  return (
    <Card className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-left">Serial</th>
            <th className="px-3 py-2 text-left">Modelo</th>
            <th className="px-3 py-2 text-left">Local</th>
            <th className="px-3 py-2 text-left">Status</th>
            <th className="px-3 py-2 text-left">Comunicação</th>
            <th className="px-3 py-2 text-right">Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.id} className="border-t border-slate-100 dark:border-slate-800">
              <td className="px-3 py-1.5 font-mono text-xs">
                <Link to={`/parque/${d.id}`} className="text-brand-600 hover:underline dark:text-brand-100">
                  {d.serial}
                </Link>
              </td>
              <td className="px-3 py-1.5 text-xs">{d.model ?? '—'}</td>
              <td className="px-3 py-1.5 text-xs">{d.site_name}</td>
              <td className="px-3 py-1.5">
                <DeviceStatus status={d.last_status} disconnected={d.disconnected} />
              </td>
              <td className="px-3 py-1.5 text-xs">{fmtCommunication(d.last_read_at)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(d.last_total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <LoadMore query={q} shown={rows.length} total={total} />
    </Card>
  );
}
