import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, MoreHorizontal, Plus, Search } from 'lucide-react';
import { useState } from 'react';

import { LoadMore } from '../../components/paging';
import { CustomerPicker } from '../../components/pickers';
import { Button } from '../../components/ui/button';
import { Dialog, Menu, MenuItem, MenuSeparator } from '../../components/ui/dialog';
import { Field, Input, Select } from '../../components/ui/form';
import { Badge, Card, EmptyState, ErrorState, PageHeader, RelativeTime, Spinner } from '../../components/ui/primitives';
import { api, downloadFile, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { ROLE_LABEL } from '../../lib/labels';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';
import { showError, showSuccess } from '../../lib/notify';

type User = Schemas['UserOut'];

export function UsersPage() {
  const { can, user: me } = useAuth();
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [editing, setEditing] = useState<User | 'new' | null>(null);
  const [secret, setSecret] = useState<{ email: string; password: string } | null>(null);
  const { query: list, rows } = useCursorList<User>(['users', q], (cursor) =>
    unwrap(api.GET('/api/v1/users', { params: { query: { q: q || null, limit: PAGE_SIZE, cursor } } })),
  );
  const refresh = () => void qc.invalidateQueries({ queryKey: ['users'] });

  async function action(u: User, fn: () => Promise<unknown>, done: string) {
    try {
      await fn();
      showSuccess(done);
      refresh();
    } catch (err) {
      showError(err, u.email);
    }
  }

  return (
    <div className="space-y-3">
      <PageHeader
        title="Usuários"
        related={[{ to: '/permissoes', label: 'Permissões' }]}
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                void downloadFile('/api/v1/users/export?format=xlsx', 'usuarios.xlsx').catch((err: unknown) => {
                  showError(err, 'Exportação falhou');
                });
              }}
            >
              <Download className="h-3.5 w-3.5" /> Exportar
            </Button>
            {can('users.create') ? (
              <Button
                size="sm"
                onClick={() => {
                  setEditing('new');
                }}
              >
                <Plus className="h-4 w-4" /> Novo usuário
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
            placeholder="Nome ou e-mail"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
            }}
            aria-label="Pesquisar usuários"
          />
        </div>
      </Card>
      <Card className="overflow-x-auto">
        {list.isPending ? (
          <Spinner />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : !rows.length ? (
          <EmptyState title="Nenhum usuário" />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2 text-left">Nome</th>
                <th className="px-3 py-2 text-left">E-mail</th>
                <th className="px-3 py-2 text-left">Papel</th>
                <th className="px-3 py-2 text-left">Situação</th>
                <th className="px-3 py-2 text-left">Último acesso</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.id} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-2 font-medium">{u.name}</td>
                  <td className="px-3 py-2 text-xs">{u.email}</td>
                  <td className="px-3 py-2 text-xs">
                    {ROLE_LABEL[u.role_code] ?? u.role_code}
                    {u.customer_id ? <Badge className="ml-1">cliente</Badge> : null}
                  </td>
                  <td className="px-3 py-2">
                    <span className="flex flex-wrap gap-1">
                      {u.active ? <Badge tone="green">Ativo</Badge> : <Badge>Inativo</Badge>}
                      {u.totp_enabled ? <Badge tone="blue">TOTP</Badge> : null}
                      {u.locked_until && new Date(u.locked_until) > new Date() ? (
                        <Badge tone="red">Bloqueado</Badge>
                      ) : null}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <RelativeTime value={u.last_login_at} />
                  </td>
                  <td className="px-3 py-2 text-right">
                    {can('users.update') && u.id !== me?.id ? (
                      <Menu
                        trigger={
                          <Button size="icon" variant="ghost" aria-label={`Ações de ${u.email}`}>
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        }
                      >
                        <MenuItem
                          onSelect={() => {
                            setEditing(u);
                          }}
                        >
                          Editar
                        </MenuItem>
                        <MenuItem
                          onSelect={() => {
                            void unwrap(
                              api.POST('/api/v1/users/{user_id}/reset-password', {
                                params: { path: { user_id: u.id } },
                                body: { mode: 'temporary' },
                              }),
                            )
                              .then((r) => {
                                if (r.temporary_password) setSecret({ email: u.email, password: r.temporary_password });
                                refresh();
                              })
                              .catch((err: unknown) => {
                                showError(err, 'Senha não redefinida');
                              });
                          }}
                        >
                          Gerar senha temporária
                        </MenuItem>
                        <MenuItem
                          onSelect={() =>
                            void action(
                              u,
                              () =>
                                unwrap(
                                  api.POST('/api/v1/users/{user_id}/reset-password', {
                                    params: { path: { user_id: u.id } },
                                    body: { mode: 'email' },
                                  }),
                                ),
                              'Link enviado por e-mail',
                            )
                          }
                        >
                          Enviar link de nova senha
                        </MenuItem>
                        {u.totp_enabled ? (
                          <MenuItem
                            onSelect={() =>
                              void action(
                                u,
                                () =>
                                  unwrap(
                                    api.POST('/api/v1/users/{user_id}/reset-totp', {
                                      params: { path: { user_id: u.id } },
                                    }),
                                  ),
                                'Autenticador removido',
                              )
                            }
                          >
                            Resetar autenticador (TOTP)
                          </MenuItem>
                        ) : null}
                        <MenuSeparator />
                        <MenuItem
                          danger
                          onSelect={() => {
                            if (!window.confirm(`Excluir o usuário ${u.email}?`)) return;
                            void action(
                              u,
                              () =>
                                unwrap(api.DELETE('/api/v1/users/{user_id}', { params: { path: { user_id: u.id } } })),
                              'Usuário excluído',
                            );
                          }}
                        >
                          Excluir
                        </MenuItem>
                      </Menu>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <LoadMore query={list} shown={rows.length} />
      </Card>
      {editing ? (
        <UserDialog
          user={editing === 'new' ? null : editing}
          onClose={(created) => {
            setEditing(null);
            if (created?.temporary_password)
              setSecret({ email: created.user.email, password: created.temporary_password });
            refresh();
          }}
        />
      ) : null}
      {secret ? (
        <Dialog
          open
          onOpenChange={(o) => {
            if (!o) setSecret(null);
          }}
          title="Senha temporária"
          description="Mostrada só agora. O usuário troca no primeiro acesso."
        >
          <p className="text-sm">{secret.email}</p>
          <code
            className="mt-2 block rounded bg-slate-900 p-3 text-center font-mono text-lg text-white"
            data-testid="temporary-password"
          >
            {secret.password}
          </code>
        </Dialog>
      ) : null}
    </div>
  );
}

function UserDialog({ user, onClose }: { user: User | null; onClose: (created?: Schemas['UserCreated']) => void }) {
  const roles = useQuery({ queryKey: ['roles'], queryFn: () => unwrap(api.GET('/api/v1/roles')) });
  const [form, setForm] = useState({
    name: user?.name ?? '',
    email: user?.email ?? '',
    role: user?.role_code ?? 'operator',
    customer_id: user?.customer_id ?? '',
    active: user?.active ?? true,
  });
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<typeof form>) => {
    setForm((f) => ({ ...f, ...p }));
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={user ? 'Editar usuário' : 'Novo usuário'}
      description={
        user ? undefined : 'Sem senha informada, o sistema gera uma temporária (troca obrigatória no primeiro acesso).'
      }
      footer={
        <Button
          loading={busy}
          onClick={() => {
            setBusy(true);
            const req = user
              ? unwrap(
                  api.PATCH('/api/v1/users/{user_id}', {
                    params: { path: { user_id: user.id } },
                    body: {
                      name: form.name,
                      email: form.email,
                      role: form.role,
                      active: form.active,
                      customer_id: form.customer_id || null,
                      clear_customer: !form.customer_id,
                    },
                  }),
                ).then(() => undefined)
              : unwrap(
                  api.POST('/api/v1/users', {
                    body: {
                      name: form.name,
                      email: form.email,
                      role: form.role,
                      active: form.active,
                      customer_id: form.customer_id || null,
                    },
                  }),
                );
            req
              .then((created) => {
                showSuccess('Usuário salvo');
                onClose(created ?? undefined);
              })
              .catch((err: unknown) => {
                showError(err, 'Usuário não salvo');
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
        <Field label="Nome" htmlFor="u-name">
          <Input
            id="u-name"
            value={form.name}
            onChange={(e) => {
              set({ name: e.target.value });
            }}
          />
        </Field>
        <Field label="E-mail" htmlFor="u-email">
          <Input
            id="u-email"
            value={form.email}
            onChange={(e) => {
              set({ email: e.target.value });
            }}
          />
        </Field>
        <Field label="Papel" htmlFor="u-role">
          <Select
            id="u-role"
            value={form.role}
            onChange={(e) => {
              set({ role: e.target.value });
            }}
          >
            {(roles.data ?? []).map((r) => (
              <option key={r.code} value={r.code}>
                {r.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Restringir a um cliente (opcional)"
          htmlFor="u-customer"
          hint="Usuário de cliente só vê o próprio parque."
        >
          <CustomerPicker
            id="u-customer"
            value={form.customer_id}
            onChange={(id) => {
              set({ customer_id: id });
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
