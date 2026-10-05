import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { useState } from 'react';

import { LoadMore } from '../../components/paging';
import { CustomerPicker } from '../../components/pickers';
import { Button } from '../../components/ui/button';
import { ConfirmButton, Dialog } from '../../components/ui/dialog';
import { Field, Input, Select, Textarea } from '../../components/ui/form';
import {
  Badge,
  Card,
  CardHeader,
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
import { showError, showSuccess } from '../../lib/notify';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';

import { AlertsList } from './AlertsList';

type Rule = Schemas['AlertRuleOut'];
type Channel = Schemas['ChannelOut'];
type Params = Record<string, unknown>;

/** Alertas (seção 10, item 8): alertas, regras centralizadas, canais, registro de envios e silêncio. */
export function AlertsPage() {
  const { can } = useAuth();
  return (
    <div className="space-y-3">
      <PageHeader
        title="Alertas"
        subtitle="Tudo o que precisa de atenção, e as regras que decidem quando avisar"
        related={[
          { to: '/alertas-da-impressora', label: 'Alertas da impressora' },
          { to: '/trocas-de-toner', label: 'Trocas de toner' },
        ]}
      />
      <Tabs defaultValue="alerts">
        <TabsList>
          <TabsTrigger value="alerts">Alertas</TabsTrigger>
          <TabsTrigger value="rules">Regras</TabsTrigger>
          <TabsTrigger value="channels">Canais</TabsTrigger>
          <TabsTrigger value="log">Notificações enviadas</TabsTrigger>
          <TabsTrigger value="settings">Configurações</TabsTrigger>
        </TabsList>
        <TabsContent value="alerts">
          <AlertsList />
        </TabsContent>
        <TabsContent value="rules">
          <RulesTab editable={can('alert_rules.write')} />
        </TabsContent>
        <TabsContent value="channels">
          <ChannelsTab editable={can('notifications.write')} />
        </TabsContent>
        <TabsContent value="log">
          <NotificationLog editable={can('notifications.write')} />
        </TabsContent>
        <TabsContent value="settings">
          <SettingsTab editable={can('notifications.write')} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ----------------------------------------------------------------------------- regras

const CATEGORY: Record<string, string> = {
  service_call: 'Chamado técnico',
  consumable: 'Consumível',
  parts: 'Peças/manutenção',
  jam: 'Atolamento',
  other: 'Outros',
};

function ParamsEditor({
  type,
  params,
  onChange,
  disabled,
}: {
  type: string;
  params: Params;
  onChange: (p: Params) => void;
  disabled: boolean;
}) {
  const numberField = (key: string, label: string) => (
    <label className="flex items-center gap-1 text-xs">
      {label}
      <Input
        aria-label={label}
        className="h-7 w-20"
        inputMode="numeric"
        disabled={disabled}
        value={typeof params[key] === 'number' ? String(params[key]) : ''}
        onChange={(e) => {
          onChange({ ...params, [key]: Number(e.target.value.replace(/\D/g, '')) });
        }}
      />
    </label>
  );
  switch (type) {
    case 'agent_offline':
      return numberField('minutes', 'sem sinal há (min)');
    case 'device_no_reading':
      return numberField('hours', 'sem leitura há (h)');
    case 'toner_days_left':
      return (
        <span className="flex flex-wrap gap-2">
          {numberField('days', 'acaba em até (dias)')}
          <label className="flex items-center gap-1 text-xs">
            confiança mínima (%)
            <Input
              aria-label="confiança mínima"
              className="h-7 w-16"
              inputMode="numeric"
              disabled={disabled}
              value={String(Math.round(Number(params.min_confidence ?? 0.5) * 100))}
              onChange={(e) => {
                onChange({ ...params, min_confidence: Number(e.target.value.replace(/\D/g, '')) / 100 });
              }}
            />
          </label>
        </span>
      );
    case 'jam_recurrent':
      return (
        <span className="flex flex-wrap gap-2">
          {numberField('count', 'atolamentos')}
          {numberField('days', 'em (dias)')}
        </span>
      );
    case 'hardware_error':
      return (
        <label className="flex items-center gap-1 text-xs">
          bits de erro
          <Input
            aria-label="bits de erro"
            className="h-7 w-64"
            disabled={disabled}
            value={((params.flags as string[] | undefined) ?? []).join(', ')}
            onChange={(e) => {
              onChange({ ...params, flags: e.target.value.split(/[,\s]+/).filter(Boolean) });
            }}
          />
        </label>
      );
    case 'printer_alert': {
      const cats = (params.categories as string[] | undefined) ?? [];
      return (
        <span className="flex flex-wrap gap-2 text-xs">
          {Object.entries(CATEGORY).map(([c, label]) => (
            <label key={c} className="flex items-center gap-1">
              <input
                type="checkbox"
                disabled={disabled}
                checked={cats.includes(c)}
                onChange={(e) => {
                  onChange({ ...params, categories: e.target.checked ? [...cats, c] : cats.filter((x) => x !== c) });
                }}
              />
              {label}
            </label>
          ))}
        </span>
      );
    }
    case 'toner_low':
      return <span className="text-xs text-slate-500">limiares por cor no cadastro do cliente ou do equipamento</span>;
    default:
      return <span className="text-xs text-slate-500">aberto pela validação das leituras</span>;
  }
}

function RuleRow({ rule, channels, editable }: { rule: Rule; channels: Channel[]; editable: boolean }) {
  const qc = useQueryClient();
  const [params, setParams] = useState<Params>(rule.params);
  const [severity, setSeverity] = useState(rule.severity);
  const [enabled, setEnabled] = useState(rule.enabled);
  const [chosen, setChosen] = useState<string[]>(rule.channel_ids as string[]);
  const [busy, setBusy] = useState(false);
  const dirty =
    JSON.stringify(params) !== JSON.stringify(rule.params) ||
    severity !== rule.severity ||
    enabled !== rule.enabled ||
    JSON.stringify(chosen) !== JSON.stringify(rule.channel_ids);
  const save = () => {
    setBusy(true);
    unwrap(
      api.PATCH('/api/v1/alert-rules/{rule_id}', {
        params: { path: { rule_id: rule.id } },
        body: { params, severity: severity as 'info' | 'warning' | 'critical', enabled, channel_ids: chosen },
      }),
    )
      .then(() => {
        showSuccess(`Regra "${rule.name}" salva`);
        void qc.invalidateQueries({ queryKey: ['alert-rules'] });
      })
      .catch((err: unknown) => {
        showError(err, 'Regra não salva');
      })
      .finally(() => {
        setBusy(false);
      });
  };
  return (
    <tr className="border-t border-slate-100 align-top dark:border-slate-800">
      <td className="px-3 py-2">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            aria-label={`Ativar ${rule.name}`}
            disabled={!editable}
            checked={enabled}
            onChange={(e) => {
              setEnabled(e.target.checked);
            }}
          />
          <span>
            <span className="font-medium">{rule.type_label}</span>
            <span className="block text-xs text-slate-500">
              {rule.customer_name ? `Cliente: ${rule.customer_name}` : 'Toda a revenda'}
            </span>
          </span>
        </label>
      </td>
      <td className="px-3 py-2">
        <ParamsEditor type={rule.type} params={params} onChange={setParams} disabled={!editable} />
      </td>
      <td className="px-3 py-2">
        <Select
          aria-label="Gravidade"
          className="h-8 w-32"
          disabled={!editable}
          value={severity}
          onChange={(e) => {
            setSeverity(e.target.value);
          }}
        >
          <option value="critical">Crítico</option>
          <option value="warning">Atenção</option>
          <option value="info">Aviso</option>
        </Select>
      </td>
      <td className="px-3 py-2 text-xs">
        {channels.length ? (
          <span className="flex flex-col gap-0.5">
            {channels.map((c) => (
              <label key={c.id} className="flex items-center gap-1">
                <input
                  type="checkbox"
                  disabled={!editable}
                  checked={chosen.includes(c.id)}
                  onChange={(e) => {
                    setChosen(e.target.checked ? [...chosen, c.id] : chosen.filter((x) => x !== c.id));
                  }}
                />
                {c.name}
              </label>
            ))}
            {!chosen.length ? <span className="text-slate-500">nenhum marcado = todos os canais ativos</span> : null}
          </span>
        ) : (
          <span className="text-slate-500">cadastre um canal</span>
        )}
      </td>
      <td className="px-3 py-2 text-right">
        {editable ? (
          <span className="flex justify-end gap-1">
            <Button size="sm" loading={busy} disabled={!dirty} onClick={save}>
              Salvar
            </Button>
            {rule.customer_id ? (
              <ConfirmButton
                title="Excluir regra do cliente"
                description="O cliente volta a seguir a regra da revenda."
                danger
                confirmLabel="Excluir"
                onConfirm={() =>
                  unwrap(api.DELETE('/api/v1/alert-rules/{rule_id}', { params: { path: { rule_id: rule.id } } })).then(
                    () => void qc.invalidateQueries({ queryKey: ['alert-rules'] }),
                  )
                }
              >
                Excluir
              </ConfirmButton>
            ) : null}
          </span>
        ) : null}
      </td>
    </tr>
  );
}

function RulesTab({ editable }: { editable: boolean }) {
  const [creating, setCreating] = useState(false);
  const rules = useQuery({ queryKey: ['alert-rules'], queryFn: () => unwrap(api.GET('/api/v1/alert-rules')) });
  const channels = useQuery({
    queryKey: ['notification-channels'],
    queryFn: () => unwrap(api.GET('/api/v1/notification-channels')),
  });
  if (rules.isPending || channels.isPending) return <Spinner />;
  if (rules.isError) return <ErrorState error={rules.error} onRetry={() => void rules.refetch()} />;
  if (channels.isError) return <ErrorState error={channels.error} onRetry={() => void channels.refetch()} />;
  return (
    <Card className="overflow-x-auto">
      <CardHeader
        title="Regras"
        subtitle="Todas as regras ficam aqui. A regra de um cliente substitui a da revenda para aquele cliente."
        actions={
          editable ? (
            <Button
              size="sm"
              onClick={() => {
                setCreating(true);
              }}
            >
              <Plus className="h-4 w-4" /> Regra para um cliente
            </Button>
          ) : null
        }
      />
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-left">Regra</th>
            <th className="px-3 py-2 text-left">Condição</th>
            <th className="px-3 py-2 text-left">Gravidade</th>
            <th className="px-3 py-2 text-left">Avisar em</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {rules.data.map((r) => (
            <RuleRow key={`${r.id}-${r.updated_at}`} rule={r} channels={channels.data} editable={editable} />
          ))}
        </tbody>
      </table>
      {creating ? (
        <NewCustomerRule
          rules={rules.data}
          onClose={() => {
            setCreating(false);
          }}
        />
      ) : null}
    </Card>
  );
}

function NewCustomerRule({ rules, onClose }: { rules: Rule[]; onClose: () => void }) {
  const qc = useQueryClient();
  const types = rules.filter((r) => !r.customer_id);
  const [customer, setCustomer] = useState('');
  const [type, setType] = useState(types[0]?.type ?? 'agent_offline');
  const base = types.find((r) => r.type === type);
  const [params, setParams] = useState<Params>(base?.params ?? {});
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Regra para um cliente"
      description="Substitui a regra da revenda só para este cliente (ex.: tolerância maior de coletor offline)."
      footer={
        <Button
          loading={busy}
          disabled={!customer}
          onClick={() => {
            setBusy(true);
            unwrap(
              api.POST('/api/v1/alert-rules', {
                body: {
                  customer_id: customer,
                  name: base?.type_label ?? type,
                  type: type as Schemas['AlertRuleIn']['type'],
                  params,
                  severity: (base?.severity ?? 'warning') as 'info' | 'warning' | 'critical',
                },
              }),
            )
              .then(() => {
                showSuccess('Regra criada');
                void qc.invalidateQueries({ queryKey: ['alert-rules'] });
                onClose();
              })
              .catch((err: unknown) => {
                showError(err, 'Regra não criada');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          Criar
        </Button>
      }
    >
      <div className="grid gap-3">
        <Field label="Cliente" htmlFor="nr-customer">
          <CustomerPicker id="nr-customer" value={customer} onChange={setCustomer} />
        </Field>
        <Field label="Tipo" htmlFor="nr-type">
          <Select
            id="nr-type"
            value={type}
            onChange={(e) => {
              setType(e.target.value);
              setParams(types.find((r) => r.type === e.target.value)?.params ?? {});
            }}
          >
            {types.map((r) => (
              <option key={r.type} value={r.type}>
                {r.type_label}
              </option>
            ))}
          </Select>
        </Field>
        <ParamsEditor type={type} params={params} onChange={setParams} disabled={false} />
      </div>
    </Dialog>
  );
}

// ----------------------------------------------------------------------------- canais

const KIND_LABEL: Record<string, string> = { email: 'E-mail', whatsapp: 'WhatsApp', webhook: 'Webhook' };

function ChannelsTab({ editable }: { editable: boolean }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Channel | 'new' | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ['notification-channels'],
    queryFn: () => unwrap(api.GET('/api/v1/notification-channels')),
  });
  const test = (c: Channel) => {
    setTesting(c.id);
    unwrap(api.POST('/api/v1/notification-channels/{channel_id}/test', { params: { path: { channel_id: c.id } } }))
      .then((r) => {
        const failed = r.results.filter((x) => !x.ok);
        if (failed.length) {
          showError(
            new Error(failed.map((x) => `${String(x.destination)}: ${String(x.error)}`).join('; ')),
            'Teste falhou',
          );
        } else {
          showSuccess(`Teste enviado para ${String(r.results.length)} destino(s)`);
        }
        void qc.invalidateQueries({ queryKey: ['notifications'] });
      })
      .catch((err: unknown) => {
        showError(err, 'Teste não enviado');
      })
      .finally(() => {
        setTesting(null);
      });
  };
  return (
    <Card>
      <CardHeader
        title="Canais de notificação"
        subtitle="E-mail, WhatsApp (Meta ou provedor HTTP) e webhook. As senhas e tokens ficam cifrados."
        actions={
          editable ? (
            <Button
              size="sm"
              onClick={() => {
                setEditing('new');
              }}
            >
              <Plus className="h-4 w-4" /> Novo canal
            </Button>
          ) : null
        }
      />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data.length ? (
        <EmptyState title="Nenhum canal">Sem canal, os alertas aparecem só no portal.</EmptyState>
      ) : (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {q.data.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
              <span>
                <span className="font-medium">{c.name}</span> <Badge>{KIND_LABEL[c.kind] ?? c.kind}</Badge>
                {!c.enabled ? <Badge className="ml-1">desativado</Badge> : null}
                <span className="block text-xs text-slate-500">
                  {c.kind === 'webhook'
                    ? typeof c.config.url === 'string'
                      ? c.config.url
                      : ''
                    : c.recipients.join(', ')}
                </span>
              </span>
              {editable ? (
                <span className="flex gap-1">
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={testing === c.id}
                    onClick={() => {
                      test(c);
                    }}
                  >
                    Testar
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setEditing(c);
                    }}
                  >
                    Editar
                  </Button>
                  <ConfirmButton
                    title="Excluir canal"
                    description="As regras deixam de avisar por este canal."
                    danger
                    confirmLabel="Excluir"
                    onConfirm={() =>
                      unwrap(
                        api.DELETE('/api/v1/notification-channels/{channel_id}', {
                          params: { path: { channel_id: c.id } },
                        }),
                      ).then(() => void qc.invalidateQueries({ queryKey: ['notification-channels'] }))
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
      {editing ? (
        <ChannelDialog
          channel={editing === 'new' ? null : editing}
          onClose={() => {
            setEditing(null);
            void qc.invalidateQueries({ queryKey: ['notification-channels'] });
          }}
        />
      ) : null}
    </Card>
  );
}

const MASK = '••••';

function ChannelDialog({ channel, onClose }: { channel: Channel | null; onClose: () => void }) {
  const [kind, setKind] = useState(channel?.kind ?? 'email');
  const [name, setName] = useState(channel?.name ?? '');
  const [enabled, setEnabled] = useState(channel?.enabled ?? true);
  const [recipients, setRecipients] = useState((channel?.recipients ?? []).join('\n'));
  const [config, setConfig] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(channel?.config ?? {}).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)]),
    ),
  );
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string) => {
    setConfig((c) => ({ ...c, [k]: v }));
  };
  const text = (k: string, label: string, hint?: string, secret = false) => (
    <Field label={label} htmlFor={`ch-${k}`} hint={hint}>
      <Input
        id={`ch-${k}`}
        type={secret ? 'password' : 'text'}
        value={config[k] ?? ''}
        placeholder={secret && channel ? MASK : undefined}
        onChange={(e) => {
          set(k, e.target.value);
        }}
      />
    </Field>
  );
  const build = (): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(config)) {
      if (v === '') continue;
      if (k === 'headers') {
        try {
          out[k] = JSON.parse(v) as unknown;
        } catch {
          throw new Error('Cabeçalhos precisam ser um JSON (ex.: {"Client-Token": "abc"})');
        }
      } else if (k === 'smtp_port') out[k] = Number(v);
      else if (k === 'starttls' || k === 'daily_summary') out[k] = v === 'true';
      else out[k] = v;
    }
    return out;
  };
  const save = () => {
    let cfg: Record<string, unknown>;
    try {
      cfg = build();
    } catch (err) {
      showError(err, 'Configuração inválida');
      return;
    }
    const list = recipients
      .split(/[\n,;]+/)
      .map((r) => r.trim())
      .filter(Boolean);
    setBusy(true);
    const req = channel
      ? unwrap(
          api.PATCH('/api/v1/notification-channels/{channel_id}', {
            params: { path: { channel_id: channel.id } },
            body: { name, enabled, recipients: list, config: cfg },
          }),
        )
      : unwrap(
          api.POST('/api/v1/notification-channels', {
            body: { kind: kind as 'email' | 'whatsapp' | 'webhook', name, enabled, recipients: list, config: cfg },
          }),
        );
    req
      .then(() => {
        showSuccess('Canal salvo');
        onClose();
      })
      .catch((err: unknown) => {
        showError(err, 'Canal não salvo');
      })
      .finally(() => {
        setBusy(false);
      });
  };
  const provider = config.provider ?? 'meta';
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={channel ? 'Editar canal' : 'Novo canal'}
      footer={
        <Button loading={busy} disabled={name.trim().length < 2} onClick={save}>
          Salvar
        </Button>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Nome" htmlFor="ch-name">
          <Input
            id="ch-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
            }}
          />
        </Field>
        <Field label="Tipo" htmlFor="ch-kind">
          <Select
            id="ch-kind"
            disabled={Boolean(channel)}
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
            }}
          >
            <option value="email">E-mail</option>
            <option value="whatsapp">WhatsApp</option>
            <option value="webhook">Webhook</option>
          </Select>
        </Field>
        {kind !== 'webhook' ? (
          <Field
            label={kind === 'email' ? 'E-mails' : 'Telefones (com DDD)'}
            htmlFor="ch-recipients"
            hint="Um por linha"
            className="sm:col-span-2"
          >
            <Textarea
              id="ch-recipients"
              value={recipients}
              onChange={(e) => {
                setRecipients(e.target.value);
              }}
            />
          </Field>
        ) : null}
        {kind === 'email' ? (
          <>
            {text('smtp_host', 'Servidor SMTP (opcional)', 'Vazio = SMTP do servidor')}
            {text('smtp_port', 'Porta SMTP')}
            {text('username', 'Usuário SMTP')}
            {text('password', 'Senha SMTP', undefined, true)}
            {text('from', 'Remetente')}
            <Field label="Resumo diário às 07:00" htmlFor="ch-summary">
              <Select
                id="ch-summary"
                value={config.daily_summary ?? 'true'}
                onChange={(e) => {
                  set('daily_summary', e.target.value);
                }}
              >
                <option value="true">Receber</option>
                <option value="false">Não receber</option>
              </Select>
            </Field>
          </>
        ) : null}
        {kind === 'webhook' ? (
          <>
            {text('url', 'URL', 'Recebe um POST JSON a cada alerta')}
            {text('secret', 'Segredo (assinatura HMAC)', 'Cabeçalho X-Dati-Signature: sha256=…', true)}
            {text('headers', 'Cabeçalhos extras (JSON)')}
          </>
        ) : null}
        {kind === 'whatsapp' ? (
          <>
            <Field label="Provedor" htmlFor="ch-provider">
              <Select
                id="ch-provider"
                value={provider}
                onChange={(e) => {
                  set('provider', e.target.value);
                }}
              >
                <option value="meta">Meta WhatsApp Cloud API</option>
                <option value="generic">HTTP genérico (Z-API, Evolution…)</option>
              </Select>
            </Field>
            {provider === 'meta' ? (
              <>
                {text('phone_number_id', 'Phone number ID')}
                {text('access_token', 'Access token', undefined, true)}
              </>
            ) : (
              <>
                {text('url_template', 'URL', 'Use {phone}, {subject}, {message} e {text}')}
                {text('method', 'Método', 'POST (padrão) ou GET')}
                {text('body_template', 'Corpo JSON', 'Ex.: {"phone": "{phone}", "message": "{text}"}')}
                {text('headers', 'Cabeçalhos (JSON)')}
              </>
            )}
          </>
        ) : null}
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => {
              setEnabled(e.target.checked);
            }}
          />
          Ativo
        </label>
      </div>
    </Dialog>
  );
}

// ----------------------------------------------------------------------------- registro e configurações

const STATUS: Record<string, { label: string; tone: 'green' | 'yellow' | 'red' | 'gray' }> = {
  sent: { label: 'Enviada', tone: 'green' },
  pending: { label: 'Na fila', tone: 'yellow' },
  failed: { label: 'Falhou', tone: 'red' },
  suppressed: { label: 'Suprimida', tone: 'gray' },
};

function NotificationLog({ editable }: { editable: boolean }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState('');
  const { query, rows } = useCursorList<Schemas['NotificationOut']>(['notifications', status], (cursor) =>
    unwrap(
      api.GET('/api/v1/notifications', {
        params: {
          query: {
            status: (status || null) as 'pending' | 'sent' | 'failed' | 'suppressed' | null,
            limit: PAGE_SIZE,
            cursor,
          },
        },
      }),
    ),
  );
  return (
    <Card className="overflow-x-auto">
      <div className="flex items-center gap-2 p-3">
        <Select
          aria-label="Situação do envio"
          className="w-44"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
          }}
        >
          <option value="">Todas</option>
          <option value="pending">Na fila</option>
          <option value="sent">Enviadas</option>
          <option value="failed">Falharam</option>
          <option value="suppressed">Suprimidas</option>
        </Select>
      </div>
      {query.isPending ? (
        <Spinner />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : !rows.length ? (
        <EmptyState title="Nenhuma notificação" />
      ) : (
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
            <tr>
              <th className="px-3 py-2 text-left">Quando</th>
              <th className="px-3 py-2 text-left">Destino</th>
              <th className="px-3 py-2 text-left">Assunto</th>
              <th className="px-3 py-2 text-left">Situação</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((n) => {
              const st = STATUS[n.status] ?? { label: n.status, tone: 'gray' as const };
              return (
                <tr key={n.id} className="border-t border-slate-100 align-top dark:border-slate-800">
                  <td className="px-3 py-2 text-xs">
                    <RelativeTime value={n.sent_at ?? n.created_at} />
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {KIND_LABEL[n.kind] ?? n.kind}: {n.destination}
                  </td>
                  <td className="px-3 py-2 text-xs">{n.subject}</td>
                  <td className="px-3 py-2 text-xs">
                    <Badge tone={st.tone}>{st.label}</Badge>
                    {n.attempts > 1 ? <span className="ml-1 text-slate-500">{n.attempts} tentativas</span> : null}
                    {n.error ? <span className="block text-red-600">{n.error}</span> : null}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {editable && (n.status === 'failed' || n.status === 'suppressed') ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                          unwrap(
                            api.POST('/api/v1/notifications/{notification_id}/retry', {
                              params: { path: { notification_id: n.id } },
                            }),
                          )
                            .then(() => {
                              showSuccess('Reenvio na fila');
                              void qc.invalidateQueries({ queryKey: ['notifications'] });
                            })
                            .catch((err: unknown) => {
                              showError(err, 'Reenvio');
                            });
                        }}
                      >
                        Reenviar
                      </Button>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <LoadMore query={query} shown={rows.length} />
    </Card>
  );
}

function SettingsTab({ editable }: { editable: boolean }) {
  const q = useQuery({
    queryKey: ['notification-settings'],
    queryFn: () => unwrap(api.GET('/api/v1/notification-settings')),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  return <SettingsForm key={JSON.stringify(q.data)} initial={q.data} editable={editable} />;
}

function SettingsForm({ initial, editable }: { initial: Schemas['NotificationSettings-Output']; editable: boolean }) {
  const qc = useQueryClient();
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const hours = Array.from({ length: 24 }, (_, h) => h);
  return (
    <Card className="grid gap-4 p-4 sm:grid-cols-2">
      <label className="flex items-center gap-2 text-sm sm:col-span-2">
        <input
          type="checkbox"
          disabled={!editable}
          checked={form.quiet_hours.enabled}
          onChange={(e) => {
            setForm({ ...form, quiet_hours: { ...form.quiet_hours, enabled: e.target.checked } });
          }}
        />
        Silêncio noturno: fora do horário só alertas críticos são enviados na hora; os outros saem no fim do silêncio
      </label>
      {(['start_hour', 'end_hour'] as const).map((k) => (
        <Field key={k} label={k === 'start_hour' ? 'Início do silêncio' : 'Fim do silêncio'} htmlFor={`qs-${k}`}>
          <Select
            id={`qs-${k}`}
            disabled={!editable || !form.quiet_hours.enabled}
            value={form.quiet_hours[k]}
            onChange={(e) => {
              setForm({ ...form, quiet_hours: { ...form.quiet_hours, [k]: Number(e.target.value) } });
            }}
          >
            {hours.map((h) => (
              <option key={h} value={h}>
                {String(h).padStart(2, '0')}:00
              </option>
            ))}
          </Select>
        </Field>
      ))}
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          disabled={!editable}
          checked={form.daily_summary}
          onChange={(e) => {
            setForm({ ...form, daily_summary: e.target.checked });
          }}
        />
        Resumo diário por e-mail às 07:00
      </label>
      <Field
        label="Troca de suprimento: subida mínima do nível (pontos)"
        htmlFor="qs-replacement"
        hint="Padrão 20: um toner que sobe 20 pontos ou mais foi trocado"
      >
        <Input
          id="qs-replacement"
          inputMode="numeric"
          disabled={!editable}
          value={String(form.replacement_threshold_points)}
          onChange={(e) => {
            setForm({ ...form, replacement_threshold_points: Number(e.target.value.replace(/\D/g, '')) });
          }}
        />
      </Field>
      {editable ? (
        <div className="sm:col-span-2">
          <Button
            loading={busy}
            onClick={() => {
              setBusy(true);
              unwrap(api.PUT('/api/v1/notification-settings', { body: form }))
                .then(() => {
                  showSuccess('Configurações salvas');
                  void qc.invalidateQueries({ queryKey: ['notification-settings'] });
                })
                .catch((err: unknown) => {
                  showError(err, 'Configurações não salvas');
                })
                .finally(() => {
                  setBusy(false);
                });
            }}
          >
            Salvar
          </Button>
        </div>
      ) : null}
    </Card>
  );
}
