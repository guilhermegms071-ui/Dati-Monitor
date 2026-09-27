import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Plus } from 'lucide-react';
import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { ConfirmButton, Dialog } from '../../components/ui/dialog';
import { Field, Input } from '../../components/ui/form';
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  KeyValue,
  PageHeader,
  Spinner,
} from '../../components/ui/primitives';
import { api, downloadFile, unwrap } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtDateTime } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';
import { useTheme } from '../../lib/theme';
import { ChangePasswordForm, TotpSetupForm } from '../auth/AuthPages';

/** Cadastro simples de nome + CNPJ (revendas e empresas). */
function NameCnpjDialog({
  title,
  initial,
  onSave,
  onClose,
}: {
  title: string;
  initial: { name: string; cnpj: string };
  onSave: (v: { name: string; cnpj: string | null }) => Promise<unknown>;
  onClose: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [cnpj, setCnpj] = useState(initial.cnpj);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={title}
      footer={
        <Button
          loading={busy}
          onClick={() => {
            setBusy(true);
            onSave({ name, cnpj: cnpj.trim() || null })
              .then(() => {
                showSuccess('Salvo');
                onClose();
              })
              .catch((err: unknown) => {
                showError(err, 'Não foi possível salvar');
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
      <div className="grid gap-3">
        <Field label="Nome" htmlFor="nc-name">
          <Input
            id="nc-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
            }}
          />
        </Field>
        <Field label="CNPJ" htmlFor="nc-cnpj">
          <Input
            id="nc-cnpj"
            value={cnpj}
            onChange={(e) => {
              setCnpj(e.target.value);
            }}
          />
        </Field>
      </div>
    </Dialog>
  );
}

export function ResellersPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<{ id?: string; name: string; cnpj: string } | null>(null);
  const q = useQuery({
    queryKey: ['resellers'],
    queryFn: () => unwrap(api.GET('/api/v1/resellers', { params: { query: { limit: 500 } } })),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ['resellers'] });
  return (
    <div className="space-y-3">
      <PageHeader
        title="Revendas"
        actions={
          <Button
            size="sm"
            onClick={() => {
              setEditing({ name: '', cnpj: '' });
            }}
          >
            <Plus className="h-4 w-4" /> Nova revenda
          </Button>
        }
      />
      <Card>
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : !q.data.items.length ? (
          <EmptyState title="Nenhuma revenda" />
        ) : (
          <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {q.data.items.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-2 px-4 py-2">
                <span>
                  <span className="font-medium">{r.name}</span>{' '}
                  <span className="text-xs text-slate-500">{r.cnpj ?? ''}</span>
                </span>
                <span className="flex gap-1">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setEditing({ id: r.id, name: r.name, cnpj: r.cnpj ?? '' });
                    }}
                  >
                    Editar
                  </Button>
                  <ConfirmButton
                    title="Excluir revenda"
                    description="Só é possível excluir revendas sem empresas, clientes e usuários."
                    danger
                    confirmLabel="Excluir"
                    onConfirm={() =>
                      unwrap(
                        api.DELETE('/api/v1/resellers/{reseller_id}', { params: { path: { reseller_id: r.id } } }),
                      ).then(refresh)
                    }
                  >
                    Excluir
                  </ConfirmButton>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {editing ? (
        <NameCnpjDialog
          title={editing.id ? 'Editar revenda' : 'Nova revenda'}
          initial={editing}
          onClose={() => {
            setEditing(null);
            refresh();
          }}
          onSave={(v) =>
            editing.id
              ? unwrap(
                  api.PATCH('/api/v1/resellers/{reseller_id}', {
                    params: { path: { reseller_id: editing.id } },
                    body: v,
                  }),
                )
              : unwrap(api.POST('/api/v1/resellers', { body: v }))
          }
        />
      ) : null}
    </div>
  );
}

export function CompaniesPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<{ id?: string; name: string; cnpj: string } | null>(null);
  const q = useQuery({
    queryKey: ['companies'],
    queryFn: () => unwrap(api.GET('/api/v1/companies', { params: { query: { limit: 500 } } })),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ['companies'] });
  return (
    <div className="space-y-3">
      <PageHeader
        title="Empresas"
        subtitle="Agrupam os clientes da revenda"
        actions={
          <Button
            size="sm"
            onClick={() => {
              setEditing({ name: '', cnpj: '' });
            }}
          >
            <Plus className="h-4 w-4" /> Nova empresa
          </Button>
        }
      />
      <Card>
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : !q.data.items.length ? (
          <EmptyState title="Nenhuma empresa" />
        ) : (
          <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {q.data.items.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-2 px-4 py-2">
                <span>
                  <span className="font-medium">{c.legal_name}</span>{' '}
                  <span className="text-xs text-slate-500">{c.cnpj ?? ''}</span>
                </span>
                <span className="flex gap-1">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setEditing({ id: c.id, name: c.legal_name, cnpj: c.cnpj ?? '' });
                    }}
                  >
                    Editar
                  </Button>
                  <ConfirmButton
                    title="Excluir empresa"
                    description="Só é possível excluir empresas sem clientes."
                    danger
                    confirmLabel="Excluir"
                    onConfirm={() =>
                      unwrap(
                        api.DELETE('/api/v1/companies/{company_id}', { params: { path: { company_id: c.id } } }),
                      ).then(refresh)
                    }
                  >
                    Excluir
                  </ConfirmButton>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {editing ? (
        <NameCnpjDialog
          title={editing.id ? 'Editar empresa' : 'Nova empresa'}
          initial={editing}
          onClose={() => {
            setEditing(null);
            refresh();
          }}
          onSave={(v) =>
            editing.id
              ? unwrap(
                  api.PATCH('/api/v1/companies/{company_id}', {
                    params: { path: { company_id: editing.id } },
                    body: { legal_name: v.name, cnpj: v.cnpj },
                  }),
                )
              : unwrap(api.POST('/api/v1/companies', { body: { legal_name: v.name, cnpj: v.cnpj } }))
          }
        />
      ) : null}
    </div>
  );
}

/** Registro sem usuário: tentativa sem login, ação de um coletor ou tarefa automática do servidor. */
function auditActor(action: string): string {
  if (action.startsWith('auth.')) return 'não autenticado';
  if (action.startsWith('agent.')) return 'coletor';
  return 'sistema';
}

export function AuditPage() {
  const [entity, setEntity] = useState('');
  const [action, setAction] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const query = {
    entity: entity || null,
    action: action || null,
    date_from: from ? new Date(`${from}T00:00:00-03:00`).toISOString() : null,
    date_to: to ? new Date(`${to}T23:59:59-03:00`).toISOString() : null,
  };
  const q = useInfiniteQuery({
    queryKey: ['audit', query],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/v1/audit', {
          params: { query: { ...query, limit: 100, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor,
  });
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  const exportQs = new URLSearchParams(
    Object.entries(query).filter(([, v]) => v !== null) as [string, string][],
  ).toString();
  return (
    <div className="space-y-3">
      <PageHeader
        title="Auditoria"
        subtitle="Quem fez o quê e quando"
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              void downloadFile(`/api/v1/audit/export?format=xlsx&${exportQs}`, 'auditoria.xlsx').catch(
                (err: unknown) => {
                  showError(err, 'Exportação falhou');
                },
              );
            }}
          >
            <Download className="h-3.5 w-3.5" /> Exportar
          </Button>
        }
      />
      <Card className="grid gap-3 p-3 sm:grid-cols-4">
        <Field label="Entidade" htmlFor="au-entity">
          <Input
            id="au-entity"
            placeholder="device, agent, command…"
            value={entity}
            onChange={(e) => {
              setEntity(e.target.value);
            }}
          />
        </Field>
        <Field label="Ação" htmlFor="au-action">
          <Input
            id="au-action"
            placeholder="update, command.scan_now…"
            value={action}
            onChange={(e) => {
              setAction(e.target.value);
            }}
          />
        </Field>
        <Field label="De" htmlFor="au-from">
          <Input
            id="au-from"
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
            }}
          />
        </Field>
        <Field label="Até" htmlFor="au-to">
          <Input
            id="au-to"
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
            }}
          />
        </Field>
      </Card>
      <Card className="overflow-x-auto">
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : !rows.length ? (
          <EmptyState title="Nenhum registro" />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2 text-left">Quando</th>
                <th className="px-3 py-2 text-left">Usuário</th>
                <th className="px-3 py-2 text-left">Ação</th>
                <th className="px-3 py-2 text-left">Entidade</th>
                <th className="px-3 py-2 text-left">Detalhes</th>
                <th className="px-3 py-2 text-left">IP</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-slate-100 align-top dark:border-slate-800">
                  <td className="whitespace-nowrap px-3 py-1.5 text-xs">{fmtDateTime(r.created_at)}</td>
                  <td className="px-3 py-1.5 text-xs">{r.user_email ?? auditActor(r.action)}</td>
                  <td className="px-3 py-1.5 font-mono text-xs">{r.action}</td>
                  <td className="px-3 py-1.5 text-xs">
                    {r.entity} <span className="text-slate-400">{r.entity_id?.slice(0, 8)}</span>
                  </td>
                  <td className="max-w-md px-3 py-1.5">
                    {r.after || r.before ? (
                      <details>
                        <summary className="cursor-pointer text-xs text-brand-600">ver</summary>
                        <pre className="mt-1 overflow-x-auto text-[11px]">
                          {JSON.stringify({ antes: r.before, depois: r.after }, null, 2)}
                        </pre>
                      </details>
                    ) : null}
                  </td>
                  <td className="px-3 py-1.5 text-xs">{r.ip ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {q.hasNextPage ? (
          <div className="p-3 text-center">
            <Button size="sm" variant="secondary" loading={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
              Carregar mais
            </Button>
          </div>
        ) : null}
      </Card>
    </div>
  );
}

export function AccountPage() {
  const { user, setUser } = useAuth();
  const [theme, setTheme] = useTheme();
  const [changing, setChanging] = useState(false);
  const [totp, setTotp] = useState(false);
  const [disable, setDisable] = useState(false);
  const [pw, setPw] = useState('');
  const [code, setCode] = useState('');
  if (!user) return null;
  return (
    <div className="max-w-2xl space-y-4">
      <PageHeader title="Minha conta" />
      <Card className="p-4">
        <KeyValue
          items={[
            ['Nome', user.name],
            ['E-mail', user.email],
            ['Papel', user.role_name],
            ['Revenda', user.reseller_name],
            ['Último acesso', fmtDateTime(user.last_login_at)],
          ]}
        />
      </Card>
      <Card>
        <CardHeader title="Aparência" />
        <div className="flex gap-2 p-4">
          {(['light', 'dark', 'system'] as const).map((t) => (
            <Button
              key={t}
              size="sm"
              variant={theme === t ? 'primary' : 'secondary'}
              onClick={() => {
                setTheme(t);
              }}
            >
              {t === 'light' ? 'Claro' : t === 'dark' ? 'Escuro' : 'Do sistema'}
            </Button>
          ))}
        </div>
      </Card>
      <Card>
        <CardHeader title="Segurança" />
        <div className="flex flex-wrap gap-2 p-4">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setChanging(true);
            }}
          >
            Trocar a senha
          </Button>
          {user.totp_enabled ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setDisable(true);
              }}
            >
              Desativar autenticador
            </Button>
          ) : (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setTotp(true);
              }}
            >
              Ativar autenticador (TOTP)
            </Button>
          )}
        </div>
      </Card>
      {changing ? (
        <Dialog open onOpenChange={setChanging} title="Trocar a senha">
          <ChangePasswordForm
            onDone={() => {
              setChanging(false);
            }}
          />
        </Dialog>
      ) : null}
      {totp ? (
        <Dialog open onOpenChange={setTotp} title="Ativar autenticador">
          <TotpSetupForm
            onDone={() => {
              setTotp(false);
            }}
          />
        </Dialog>
      ) : null}
      {disable ? (
        <Dialog
          open
          onOpenChange={setDisable}
          title="Desativar autenticador"
          footer={
            <Button
              variant="danger"
              onClick={() => {
                unwrap(api.POST('/api/v1/auth/totp/disable', { body: { password: pw, code } }))
                  .then(() => unwrap(api.GET('/api/v1/auth/me')))
                  .then((me) => {
                    setUser(me);
                    setDisable(false);
                    showSuccess('Autenticador desativado');
                  })
                  .catch((err: unknown) => {
                    showError(err, 'Não foi possível desativar');
                  });
              }}
            >
              Desativar
            </Button>
          }
        >
          <div className="grid gap-3">
            <Field label="Senha" htmlFor="td-pw">
              <Input
                id="td-pw"
                type="password"
                value={pw}
                onChange={(e) => {
                  setPw(e.target.value);
                }}
              />
            </Field>
            <Field label="Código atual do autenticador" htmlFor="td-code">
              <Input
                id="td-code"
                inputMode="numeric"
                maxLength={6}
                value={code}
                onChange={(e) => {
                  setCode(e.target.value.replace(/\D/g, ''));
                }}
              />
            </Field>
          </div>
        </Dialog>
      ) : null}
    </div>
  );
}
