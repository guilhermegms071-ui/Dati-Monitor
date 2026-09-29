import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { ConfirmButton, Dialog } from '../../components/ui/dialog';
import { Field, Input, Select } from '../../components/ui/form';
import { Badge, Card, CardHeader, EmptyState, ErrorState, PageHeader, Spinner } from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { showError, showSuccess } from '../../lib/notify';

type Matrix = Schemas['PermissionMatrix'];
type RoleMatrix = Schemas['RoleMatrix'];

/** Usuários > Permissões (seção 16.14): matriz módulo × Consultar/Incluir/Alterar/Excluir por papel. */
export function PermissionsPage() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ['permission-matrix'], queryFn: () => unwrap(api.GET('/api/v1/permissions/matrix')) });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  return (
    <div className="space-y-4">
      <PageHeader
        title="Permissões"
        subtitle="O que cada papel pode fazer nesta revenda. Administradores têm sempre acesso total."
      />
      {q.data.roles.map((r) => (
        <RoleCard
          key={`${r.role}-${r.permissions.join(',')}`}
          matrix={q.data}
          role={r}
          editable={can('permissions.write')}
        />
      ))}
    </div>
  );
}

function RoleCard({ matrix, role, editable }: { matrix: Matrix; role: RoleMatrix; editable: boolean }) {
  const qc = useQueryClient();
  const [perms, setPerms] = useState(() => new Set(role.permissions));
  const [busy, setBusy] = useState(false);
  const grantable = new Set(role.grantable);
  const dirty = perms.size !== role.permissions.length || role.permissions.some((p) => !perms.has(p));
  const toggle = (perm: string, on: boolean) => {
    setPerms((s) => {
      const n = new Set(s);
      if (on) n.add(perm);
      else n.delete(perm);
      return n;
    });
  };
  const save = (permissions: string[] | null) => {
    setBusy(true);
    return unwrap(
      api.PUT('/api/v1/permissions/matrix/{role}', {
        params: { path: { role: role.role } },
        body: { permissions },
      }),
    )
      .then((m) => {
        qc.setQueryData(['permission-matrix'], m);
        showSuccess(`Permissões de ${role.role_name} salvas`);
      })
      .catch((err: unknown) => {
        showError(err, 'Permissões não salvas');
      })
      .finally(() => {
        setBusy(false);
      });
  };
  const cell = (perm: string, label: string) => {
    const allowed = grantable.has(perm);
    return (
      <input
        type="checkbox"
        aria-label={label}
        checked={perms.has(perm)}
        disabled={!editable || !allowed}
        title={allowed ? undefined : 'Este papel não pode receber esta permissão'}
        onChange={(e) => {
          toggle(perm, e.target.checked);
        }}
      />
    );
  };
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            {role.role_name}
            {role.customized ? <Badge tone="blue">ajustado</Badge> : <Badge>padrão do sistema</Badge>}
          </span>
        }
        actions={
          editable ? (
            <>
              {role.customized ? (
                <ConfirmButton
                  title="Voltar ao padrão"
                  description={`A matriz de ${role.role_name} volta a ser a padrão do sistema.`}
                  onConfirm={() => save(null)}
                >
                  Voltar ao padrão
                </ConfirmButton>
              ) : null}
              <Button size="sm" loading={busy} disabled={!dirty} onClick={() => void save([...perms].sort())}>
                Salvar
              </Button>
            </>
          ) : null
        }
      />
      <div className="overflow-x-auto p-4">
        <table className="text-sm">
          <thead className="text-xs text-slate-500">
            <tr>
              <th className="pr-6 text-left">Módulo</th>
              {Object.entries(matrix.actions).map(([code, label]) => (
                <th key={code} className="px-3 text-center">
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.modules.map((m) => (
              <tr key={m.code} className="border-t border-slate-100 dark:border-slate-800">
                <td className="py-1.5 pr-6">{m.name}</td>
                {Object.entries(matrix.actions).map(([code, label]) => (
                  <td key={code} className="px-3 text-center">
                    {cell(`${m.code}.${code}`, `${m.name}: ${label}`)}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="border-t border-slate-100 dark:border-slate-800">
              <td className="py-1.5 pr-6">Monitorar suprimentos</td>
              <td className="px-3 text-center">{cell(matrix.supplies_permission, 'Monitorar suprimentos')}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </Card>
  );
}

const FIELD_TYPE: Record<string, string> = { text: 'Texto', number: 'Número', date: 'Data' };

/** Configurações > Campos personalizados de equipamento (seção 16.7). */
export function CustomFieldsPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Schemas['CustomFieldOut'] | 'new' | null>(null);
  const q = useQuery({ queryKey: ['custom-fields'], queryFn: () => unwrap(api.GET('/api/v1/custom-fields')) });
  const editable = can('settings.write');
  return (
    <div className="space-y-3">
      <PageHeader
        title="Campos personalizados"
        subtitle="Campos extras do cadastro de equipamento (contrato, centro de custo…), preenchidos no detalhe do equipamento."
        actions={
          editable ? (
            <Button
              size="sm"
              onClick={() => {
                setEditing('new');
              }}
            >
              <Plus className="h-4 w-4" /> Novo campo
            </Button>
          ) : null
        }
      />
      <Card>
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        ) : !q.data.length ? (
          <EmptyState title="Nenhum campo personalizado" />
        ) : (
          <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {q.data.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
                <span>
                  <span className="font-medium">{f.label}</span>{' '}
                  <span className="font-mono text-xs text-slate-500">{f.key}</span>{' '}
                  <Badge>{FIELD_TYPE[f.field_type] ?? f.field_type}</Badge>
                  {!f.active ? <Badge className="ml-1">inativo</Badge> : null}
                </span>
                {editable ? (
                  <span className="flex gap-1">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => {
                        setEditing(f);
                      }}
                    >
                      Editar
                    </Button>
                    <ConfirmButton
                      title="Excluir campo"
                      description="Os valores já gravados nos equipamentos ficam no histórico, mas deixam de aparecer."
                      danger
                      confirmLabel="Excluir"
                      onConfirm={() =>
                        unwrap(
                          api.DELETE('/api/v1/custom-fields/{field_id}', { params: { path: { field_id: f.id } } }),
                        ).then(() => void qc.invalidateQueries({ queryKey: ['custom-fields'] }))
                      }
                    >
                      Excluir
                    </ConfirmButton>
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>
      {editing ? (
        <CustomFieldDialog
          field={editing === 'new' ? null : editing}
          onClose={() => {
            setEditing(null);
            void qc.invalidateQueries({ queryKey: ['custom-fields'] });
          }}
        />
      ) : null}
    </div>
  );
}

function CustomFieldDialog({ field, onClose }: { field: Schemas['CustomFieldOut'] | null; onClose: () => void }) {
  const [form, setForm] = useState({
    key: field?.key ?? '',
    label: field?.label ?? '',
    field_type: field?.field_type ?? 'text',
    position: String(field?.position ?? 0),
    active: field?.active ?? true,
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
      title={field ? 'Editar campo' : 'Novo campo'}
      footer={
        <Button
          loading={busy}
          onClick={() => {
            setBusy(true);
            const position = Number(form.position) || 0;
            const req = field
              ? unwrap(
                  api.PATCH('/api/v1/custom-fields/{field_id}', {
                    params: { path: { field_id: field.id } },
                    body: { label: form.label, position, active: form.active },
                  }),
                )
              : unwrap(
                  api.POST('/api/v1/custom-fields', {
                    body: {
                      key: form.key,
                      label: form.label,
                      field_type: form.field_type as 'text' | 'number' | 'date',
                      position,
                      active: form.active,
                    },
                  }),
                );
            req
              .then(() => {
                showSuccess('Campo salvo');
                onClose();
              })
              .catch((err: unknown) => {
                showError(err, 'Campo não salvo');
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
        <Field label="Rótulo" htmlFor="cf-label">
          <Input
            id="cf-label"
            value={form.label}
            onChange={(e) => {
              set({ label: e.target.value });
            }}
          />
        </Field>
        <Field label="Chave" htmlFor="cf-key" hint="Minúsculas, números e _ (não muda depois)">
          <Input
            id="cf-key"
            disabled={Boolean(field)}
            value={form.key}
            onChange={(e) => {
              set({ key: e.target.value });
            }}
          />
        </Field>
        <Field label="Tipo" htmlFor="cf-type">
          <Select
            id="cf-type"
            disabled={Boolean(field)}
            value={form.field_type}
            onChange={(e) => {
              set({ field_type: e.target.value });
            }}
          >
            <option value="text">Texto</option>
            <option value="number">Número</option>
            <option value="date">Data</option>
          </Select>
        </Field>
        <Field label="Ordem" htmlFor="cf-pos">
          <Input
            id="cf-pos"
            inputMode="numeric"
            value={form.position}
            onChange={(e) => {
              set({ position: e.target.value.replace(/\D/g, '') });
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
          />
          Ativo
        </label>
      </div>
    </Dialog>
  );
}
