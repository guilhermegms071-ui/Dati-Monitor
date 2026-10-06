import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, RotateCcw } from 'lucide-react';
import { useState } from 'react';

import { DataList } from '../../components/domain';
import { LoadMore } from '../../components/paging';
import { Button } from '../../components/ui/button';
import { ConfirmButton, Dialog } from '../../components/ui/dialog';
import { Field, Input, Select } from '../../components/ui/form';
import {
  Badge,
  Card,
  CardHeader,
  Checkbox,
  EmptyState,
  ErrorState,
  PageHeader,
  RelativeTime,
  Spinner,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtDateTime } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';

type Connector = Schemas['ErpConnectorSettings-Output'];
type QueueItem = Schemas['ErpQueueItemOut'];

const KIND_LABEL: Record<QueueItem['kind'], string> = {
  counters: 'Contadores',
  supply_request: 'Requisição de suprimento',
  service_order: 'Ordem de serviço',
};
const STATUS: Record<QueueItem['status'], { label: string; tone: 'yellow' | 'green' | 'red' }> = {
  pending: { label: 'Pendente', tone: 'yellow' },
  sent: { label: 'Enviado', tone: 'green' },
  error: { label: 'Erro', tone: 'red' },
};
const OS_TYPES: [Schemas['ServiceOrderParams-Output']['alert_types'][number], string][] = [
  ['service_call', 'Chamado técnico'],
  ['consumable', 'Consumíveis'],
  ['jam_recurrent', 'Atolamento recorrente'],
  ['parts', 'Peças/manutenção'],
  ['other', 'Outros'],
];

export function IntegrationPage() {
  return (
    <div className="space-y-4">
      <PageHeader
        title="Integração ERP"
        subtitle="API somente leitura para o ERP e o conector do Dataclassic (requisições, OS e contadores)."
      />
      <Tabs defaultValue="connector">
        <TabsList>
          <TabsTrigger value="connector">Conector Dataclassic</TabsTrigger>
          <TabsTrigger value="queue">Fila de envio</TabsTrigger>
          <TabsTrigger value="tokens">Tokens da API</TabsTrigger>
        </TabsList>
        <TabsContent value="connector">
          <ConnectorForm />
        </TabsContent>
        <TabsContent value="queue">
          <QueueList />
        </TabsContent>
        <TabsContent value="tokens">
          <TokensCard />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ----------------------------------------------------------------------------- conector

function LabeledCheck({
  checked,
  onCheckedChange,
  label,
}: {
  checked: boolean;
  onCheckedChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm">
      <Checkbox checked={checked} onCheckedChange={onCheckedChange} label={label} />
      {label}
    </label>
  );
}

function ConnectorForm() {
  const q = useQuery({ queryKey: ['erp-connector'], queryFn: () => unwrap(api.GET('/api/v1/erp-connector')) });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  return <ConnectorEditor initial={q.data} />;
}

function ConnectorEditor({ initial }: { initial: Connector }) {
  const qc = useQueryClient();
  const { can } = useAuth();
  const editable = can('integration.update');
  const [c, setC] = useState<Connector>(initial);
  const [busy, setBusy] = useState(false);
  const sr = c.supply_request;
  const so = c.service_order;
  const t = c.transport;
  const text = (v: string | null | undefined) => v ?? '';
  const nul = (v: string) => (v.trim() === '' ? null : v);
  return (
    <Card>
      <CardHeader
        title="Parâmetros da empresa"
        subtitle={
          c.enabled_since
            ? `Ligado desde ${fmtDateTime(c.enabled_since)}: só alertas abertos depois disso são enviados.`
            : 'Desligado.'
        }
      />
      <fieldset disabled={!editable} className="space-y-5 p-4">
        <div className="flex flex-wrap gap-6">
          <LabeledCheck
            checked={c.enabled}
            onCheckedChange={(v) => {
              setC({ ...c, enabled: v });
            }}
            label="Integração habilitada"
          />
          <LabeledCheck
            checked={c.send_counters}
            onCheckedChange={(v) => {
              setC({ ...c, send_counters: v });
            }}
            label="Enviar contadores (leitura de corte diária)"
          />
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Código da empresa" htmlFor="e-company">
            <Input
              id="e-company"
              value={text(c.company_code)}
              onChange={(e) => {
                setC({ ...c, company_code: nul(e.target.value) });
              }}
            />
          </Field>
          <Field label="Operador" htmlFor="e-operator">
            <Input
              id="e-operator"
              value={text(c.operator)}
              onChange={(e) => {
                setC({ ...c, operator: nul(e.target.value) });
              }}
            />
          </Field>
          <Field label="Hora do envio dos contadores" htmlFor="e-hour" hint="Horário de Brasília">
            <Input
              id="e-hour"
              type="number"
              min={0}
              max={23}
              value={c.counters_hour}
              onChange={(e) => {
                setC({ ...c, counters_hour: Number(e.target.value) });
              }}
            />
          </Field>
        </div>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Requisição de suprimento (alerta de toner baixo)</h3>
          <LabeledCheck
            checked={sr.enabled}
            onCheckedChange={(v) => {
              setC({ ...c, supply_request: { ...sr, enabled: v } });
            }}
            label="Gerar requisição de suprimento"
          />
          <div className="grid gap-3 sm:grid-cols-4">
            {(
              [
                ['operation', 'Operação'],
                ['desc_type', 'Tipo desc'],
                ['status', 'Status'],
                ['situation', 'Situação'],
                ['payment_condition', 'Condição de pagamento'],
                ['seller', 'Vendedor'],
                ['freight_type', 'Tipo de frete'],
                ['notify_email', 'E-mail de notificação'],
              ] as const
            ).map(([k, label]) => (
              <Field key={k} label={label} htmlFor={`sr-${k}`}>
                <Input
                  id={`sr-${k}`}
                  value={text(sr[k])}
                  onChange={(e) => {
                    setC({ ...c, supply_request: { ...sr, [k]: nul(e.target.value) } });
                  }}
                />
              </Field>
            ))}
          </div>
          <LabeledCheck
            checked={sr.email_only}
            onCheckedChange={(v) => {
              setC({ ...c, supply_request: { ...sr, email_only: v } });
            }}
            label="Apenas enviar e-mail (não cria no ERP)"
          />
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Ordem de serviço</h3>
          <LabeledCheck
            checked={so.enabled}
            onCheckedChange={(v) => {
              setC({ ...c, service_order: { ...so, enabled: v } });
            }}
            label="Gerar OS a partir dos alertas"
          />
          <div className="grid gap-3 sm:grid-cols-4">
            {(
              [
                ['technician_code', 'Código do técnico'],
                ['reason', 'Motivo'],
                ['intervention_type', 'Tipo de intervenção'],
                ['status', 'Status'],
              ] as const
            ).map(([k, label]) => (
              <Field key={k} label={label} htmlFor={`so-${k}`}>
                <Input
                  id={`so-${k}`}
                  value={text(so[k])}
                  onChange={(e) => {
                    setC({ ...c, service_order: { ...so, [k]: nul(e.target.value) } });
                  }}
                />
              </Field>
            ))}
          </div>
          <div className="flex flex-wrap gap-4">
            {OS_TYPES.map(([v, label]) => (
              <LabeledCheck
                key={v}
                checked={so.alert_types.includes(v)}
                onCheckedChange={(on) => {
                  const types = on ? [...so.alert_types, v] : so.alert_types.filter((x) => x !== v);
                  setC({ ...c, service_order: { ...so, alert_types: types } });
                }}
                label={label}
              />
            ))}
          </div>
          <Field
            label="Códigos prtAlert importáveis"
            htmlFor="so-codes"
            hint="Separados por vírgula; vazio = todos. Vale para os alertas da impressora."
          >
            <Input
              id="so-codes"
              value={so.prt_alert_codes.join(', ')}
              onChange={(e) => {
                const codes = e.target.value
                  .split(/[,\s]+/)
                  .filter(Boolean)
                  .map(Number)
                  .filter((n) => Number.isInteger(n) && n > 0);
                setC({ ...c, service_order: { ...so, prt_alert_codes: codes } });
              }}
            />
          </Field>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Transporte</h3>
          <p className="text-xs text-slate-500">
            Até a Databit informar o layout do Dataclassic, os envios usam o JSON documentado em
            docs/erp-dataclassic.md.
          </p>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Tipo" htmlFor="t-kind">
              <Select
                id="t-kind"
                value={t.kind}
                onChange={(e) => {
                  setC({ ...c, transport: { ...t, kind: e.target.value as 'file' | 'http' } });
                }}
              >
                <option value="file">Arquivo (pasta lida pelo ERP)</option>
                <option value="http">API (POST JSON)</option>
              </Select>
            </Field>
            {t.kind === 'file' ? (
              <Field label="Pasta" htmlFor="t-dir" className="sm:col-span-2">
                <Input
                  id="t-dir"
                  value={text(t.directory)}
                  onChange={(e) => {
                    setC({ ...c, transport: { ...t, directory: nul(e.target.value) } });
                  }}
                />
              </Field>
            ) : (
              <>
                <Field label="URL" htmlFor="t-url">
                  <Input
                    id="t-url"
                    value={text(t.url)}
                    onChange={(e) => {
                      setC({ ...c, transport: { ...t, url: nul(e.target.value) } });
                    }}
                  />
                </Field>
                <Field label="Cabeçalho Authorization" htmlFor="t-auth" hint="Fica cifrado; •••• = já salvo">
                  <Input
                    id="t-auth"
                    type="password"
                    value={text(t.auth_header)}
                    onChange={(e) => {
                      setC({ ...c, transport: { ...t, auth_header: nul(e.target.value) } });
                    }}
                  />
                </Field>
              </>
            )}
          </div>
        </section>
        {editable ? (
          <Button
            loading={busy}
            onClick={() => {
              setBusy(true);
              unwrap(api.PUT('/api/v1/erp-connector', { body: c }))
                .then((saved) => {
                  setC(saved);
                  showSuccess('Parâmetros do conector salvos');
                  void qc.invalidateQueries({ queryKey: ['erp-connector'] });
                })
                .catch((err: unknown) => {
                  showError(err, 'Parâmetros não salvos');
                })
                .finally(() => {
                  setBusy(false);
                });
            }}
          >
            Salvar
          </Button>
        ) : null}
      </fieldset>
    </Card>
  );
}

// ----------------------------------------------------------------------------- fila

function QueueList() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const [status, setStatus] = useState<'' | QueueItem['status']>('');
  const [detail, setDetail] = useState<string | null>(null);
  const counts = useQuery({
    queryKey: ['erp-queue', 'counts'],
    queryFn: () => unwrap(api.GET('/api/v1/erp-queue/counts')),
  });
  const { query: q, rows } = useCursorList<QueueItem>(['erp-queue', status], (cursor) =>
    unwrap(
      api.GET('/api/v1/erp-queue', {
        params: { query: { limit: PAGE_SIZE, cursor, ...(status ? { status } : {}) } },
      }),
    ),
  );
  const retry = (ids: string[]) => {
    unwrap(api.POST('/api/v1/erp-queue/retry', { body: { ids } }))
      .then((r) => {
        showSuccess(`${String(r.requeued)} item(ns) voltaram para a fila`);
        void qc.invalidateQueries({ queryKey: ['erp-queue'] });
      })
      .catch((err: unknown) => {
        showError(err, 'Reenvio falhou');
      });
  };
  return (
    <Card>
      <CardHeader
        title="Fila de envio"
        subtitle={
          counts.data
            ? `Pendentes ${String(counts.data.pending)} · enviados ${String(counts.data.sent)} · com erro ${String(counts.data.error)}`
            : undefined
        }
        actions={
          <Select
            aria-label="Status"
            className="h-8 w-40"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as '' | QueueItem['status']);
            }}
          >
            <option value="">Todos</option>
            <option value="pending">Pendentes</option>
            <option value="sent">Enviados</option>
            <option value="error">Com erro</option>
          </Select>
        }
      />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState title="Nada na fila" />
      ) : (
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full text-sm" data-testid="erp-queue">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2 text-left">Criado</th>
                <th className="px-3 py-2 text-left">Tipo</th>
                <th className="px-3 py-2 text-left">Resumo</th>
                <th className="px-3 py-2 text-left">Status</th>
                <th className="px-3 py-2 text-left">Tentativas / último erro</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((i) => (
                <tr key={i.id} className="border-t border-slate-100 align-top dark:border-slate-800">
                  <td className="px-3 py-1.5 whitespace-nowrap">
                    <RelativeTime value={i.created_at} />
                  </td>
                  <td className="px-3 py-1.5">{KIND_LABEL[i.kind]}</td>
                  <td className="px-3 py-1.5">{i.summary}</td>
                  <td className="px-3 py-1.5">
                    <Badge tone={STATUS[i.status].tone}>{STATUS[i.status].label}</Badge>
                    {i.delivered_via ? <span className="ml-1 text-xs text-slate-500">({i.delivered_via})</span> : null}
                  </td>
                  <td className="px-3 py-1.5 text-xs">
                    {i.attempts}
                    {i.last_error ? <span className="block text-red-600">{i.last_error}</span> : null}
                  </td>
                  <td className="space-x-1 px-3 py-1.5 text-right whitespace-nowrap">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setDetail(i.id);
                      }}
                    >
                      Conteúdo
                    </Button>
                    {can('integration.update') && i.status !== 'pending' ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          retry([i.id]);
                        }}
                      >
                        <RotateCcw className="h-3.5 w-3.5" /> Reenviar
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <LoadMore query={q} shown={rows.length} />
        </div>
      )}
      {detail ? (
        <QueueDetail
          id={detail}
          onClose={() => {
            setDetail(null);
          }}
        />
      ) : null}
    </Card>
  );
}

function QueueDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['erp-queue', 'item', id],
    queryFn: () => unwrap(api.GET('/api/v1/erp-queue/{item_id}', { params: { path: { item_id: id } } })),
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Conteúdo enviado ao ERP"
    >
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : (
        <div className="scroll-thin max-h-[60vh] overflow-auto">
          <DataList data={q.data.payload} />
        </div>
      )}
    </Dialog>
  );
}

// ----------------------------------------------------------------------------- tokens

function TokensCard() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const q = useQuery({ queryKey: ['erp-tokens'], queryFn: () => unwrap(api.GET('/api/v1/erp-tokens')) });
  const [name, setName] = useState('');
  const [created, setCreated] = useState<Schemas['ErpTokenCreated'] | null>(null);
  return (
    <Card>
      <CardHeader
        title="Tokens da API do ERP"
        subtitle="GET /api/erp/v1/readings, /cutoff e /devices com Authorization: Bearer <token>. Somente leitura."
      />
      {can('integration.create') ? (
        <form
          className="flex flex-wrap items-end gap-2 p-3"
          onSubmit={(e) => {
            e.preventDefault();
            unwrap(api.POST('/api/v1/erp-tokens', { body: { name } }))
              .then((t) => {
                setCreated(t);
                setName('');
                void qc.invalidateQueries({ queryKey: ['erp-tokens'] });
              })
              .catch((err: unknown) => {
                showError(err, 'Token não criado');
              });
          }}
        >
          <Field label="Nome do token" htmlFor="tk-name">
            <Input
              id="tk-name"
              value={name}
              placeholder="Ex.: Dataclassic produção"
              onChange={(e) => {
                setName(e.target.value);
              }}
            />
          </Field>
          <Button type="submit" disabled={name.trim().length < 2}>
            Criar token
          </Button>
        </form>
      ) : null}
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : q.data.length === 0 ? (
        <EmptyState title="Nenhum token" />
      ) : (
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
            <tr>
              <th className="px-3 py-2 text-left">Nome</th>
              <th className="px-3 py-2 text-left">Início</th>
              <th className="px-3 py-2 text-left">Criado por</th>
              <th className="px-3 py-2 text-left">Último uso</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {q.data.map((t) => (
              <tr key={t.id} className="border-t border-slate-100 dark:border-slate-800">
                <td className="px-3 py-1.5">{t.name}</td>
                <td className="px-3 py-1.5 font-mono text-xs">{t.token_prefix}…</td>
                <td className="px-3 py-1.5">{t.created_by ?? '—'}</td>
                <td className="px-3 py-1.5">
                  <RelativeTime value={t.last_used_at} />
                </td>
                <td className="px-3 py-1.5 text-right">
                  {t.revoked_at ? (
                    <Badge tone="red">revogado</Badge>
                  ) : can('integration.delete') ? (
                    <ConfirmButton
                      size="sm"
                      variant="secondary"
                      title="Revogar token?"
                      description="O ERP que usa este token para de conseguir ler os dados na hora."
                      confirmLabel="Revogar"
                      onConfirm={() =>
                        unwrap(
                          api.POST('/api/v1/erp-tokens/{token_id}/revoke', {
                            params: { path: { token_id: t.id } },
                          }),
                        ).then(() => {
                          showSuccess('Token revogado');
                          void qc.invalidateQueries({ queryKey: ['erp-tokens'] });
                        })
                      }
                    >
                      Revogar
                    </ConfirmButton>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {created ? (
        <Dialog
          open
          onOpenChange={(o) => {
            if (!o) setCreated(null);
          }}
          title="Token criado"
          description="Copie agora: o token não será mostrado de novo."
        >
          <div className="flex items-center gap-2">
            <code
              className="flex-1 break-all rounded bg-slate-100 p-2 text-xs dark:bg-slate-800"
              data-testid="erp-token"
            >
              {created.token}
            </code>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                navigator.clipboard
                  .writeText(created.token)
                  .then(() => {
                    showSuccess('Token copiado');
                  })
                  .catch((err: unknown) => {
                    showError(err, 'Não foi possível copiar');
                  });
              }}
            >
              <Copy className="h-3.5 w-3.5" /> Copiar
            </Button>
          </div>
        </Dialog>
      ) : null}
    </Card>
  );
}
