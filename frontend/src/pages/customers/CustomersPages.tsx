import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router';

import { AgentState, DeviceStatus, RoleBadge } from '../../components/domain';
import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Input, Select } from '../../components/ui/form';
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

type Customer = Schemas['CustomerOut'];

export function CustomersPage() {
  const { can } = useAuth();
  const [q, setQ] = useState('');
  const [editing, setEditing] = useState<Customer | 'new' | null>(null);
  const list = useQuery({
    queryKey: ['customers', q],
    queryFn: () => unwrap(api.GET('/api/v1/customers', { params: { query: { q: q || null, limit: 500 } } })),
  });
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
            {can('customers.write') ? (
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
        ) : !list.data.items.length ? (
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
              {list.data.items.map((c) => (
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
  const companies = useQuery({
    queryKey: ['companies'],
    queryFn: () => unwrap(api.GET('/api/v1/companies', { params: { query: { limit: 500 } } })),
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
  const companyId = form.company_id || companies.data?.items[0]?.id || '';
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
          <Select
            id="c-company"
            value={companyId}
            onChange={(e) => {
              set({ company_id: e.target.value });
            }}
          >
            {(companies.data?.items ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.legal_name}
              </option>
            ))}
          </Select>
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
          can('customers.write') ? (
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
          <SitesTab customerId={c.id} canWrite={can('sites.write')} />
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
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const q = useQuery({
    queryKey: ['sites', customerId],
    queryFn: () => unwrap(api.GET('/api/v1/sites', { params: { query: { customer_id: customerId, limit: 500 } } })),
  });
  return (
    <Card>
      <CardHeader title="Locais" subtitle="Cada local tem seus coletores, faixas de IP e credenciais SNMP." />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : !q.data.items.length ? (
        <EmptyState title="Nenhum local" />
      ) : (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {q.data.items.map((s) => (
            <li key={s.id} className="flex items-center justify-between px-4 py-2">
              <span>
                <span className="font-medium">{s.name}</span>
                {s.address ? <span className="text-xs text-slate-500"> · {s.address}</span> : null}
              </span>
              <Link to={`/parque?site=${s.id}`} className="text-xs text-brand-600 hover:underline">
                ver equipamentos
              </Link>
            </li>
          ))}
        </ul>
      )}
      {canWrite ? (
        <form
          className="flex flex-wrap items-end gap-2 border-t border-slate-200 p-4 dark:border-slate-800"
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            unwrap(api.POST('/api/v1/sites', { body: { customer_id: customerId, name } }))
              .then(() => {
                setName('');
                showSuccess('Local criado');
                void qc.invalidateQueries({ queryKey: ['sites', customerId] });
              })
              .catch((err: unknown) => {
                showError(err, 'Local não criado');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          <Field label="Novo local" htmlFor="s-name">
            <Input
              id="s-name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
              }}
              required
            />
          </Field>
          <Button type="submit" loading={busy}>
            Adicionar
          </Button>
        </form>
      ) : null}
    </Card>
  );
}

function CustomerAgents({ customerId }: { customerId: string }) {
  const q = useQuery({
    queryKey: ['agents', 'customer', customerId],
    queryFn: () => unwrap(api.GET('/api/v1/agents', { params: { query: { customer_id: customerId, limit: 500 } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!q.data.items.length) return <EmptyState title="Nenhum coletor neste cliente" />;
  return (
    <Card>
      <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
        {q.data.items.map((a) => (
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
    </Card>
  );
}

function CustomerDevices({ customerId }: { customerId: string }) {
  const q = useQuery({
    queryKey: ['park', 'customer', customerId],
    queryFn: () => unwrap(api.GET('/api/v1/park', { params: { query: { customer_id: customerId, limit: 500 } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!q.data.items.length) return <EmptyState title="Nenhum equipamento neste cliente" />;
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
          {q.data.items.map((d) => (
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
    </Card>
  );
}
