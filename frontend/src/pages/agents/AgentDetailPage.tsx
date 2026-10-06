import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, FileText, Trash2, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { AgentState, CommandState, CommandWatch, LogView, RoleBadge } from '../../components/domain';
import { LoadMore } from '../../components/paging';
import { ReactivateButton } from '../../components/Reactivate';
import { Button } from '../../components/ui/button';
import { ConfirmButton, Dialog, Menu, MenuItem, MenuSeparator } from '../../components/ui/dialog';
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
import { api, downloadFile, fetchText, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { useSendCommand, type CommandType } from '../../lib/commands';
import { fmtBytes, fmtDateTime, fmtDayTime, fmtDec, fmtInt, fmtTime } from '../../lib/format';
import { CLUSTER_REASON, FINAL_COMMAND_STATES } from '../../lib/labels';
import { showError, showSuccess } from '../../lib/notify';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';
import { watchdogStatus } from '../../lib/watchdog';
import { EnrollmentInfo } from './AgentsPage';
import { PreferredMasterButton, UninstallDialog, UpdateDialog, WatchdogBadge, WatchdogCard } from './WatchdogPanels';
import { SetServerDialog } from './SetServerDialog';

type Agent = Schemas['AgentOut'];

export function AgentDetailPage() {
  const { agentId = '' } = useParams();
  const { can } = useAuth();
  const q = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => unwrap(api.GET('/api/v1/agents/{agent_id}', { params: { path: { agent_id: agentId } } })),
    refetchInterval: 30_000,
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const a = q.data;
  return (
    <div className="space-y-4">
      <PageHeader
        title={a.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            {a.customer_name} / {a.site_name}
            <AgentState state={a.state} wsConnected={a.ws_connected} />
            <RoleBadge role={a.cluster_role} />
            {a.revoked_at ? <Badge tone="red">Revogado</Badge> : null}
          </span>
        }
        actions={<AgentActions agent={a} canCommand={can('agents.command')} canWrite={can('agents.update')} />}
      />
      <Card className="p-4">
        <KeyValue
          items={[
            ['Último sinal', <RelativeTime key="s" value={a.last_seen_at} />],
            ['Canal WebSocket', a.ws_connected ? 'conectado' : 'desconectado'],
            ['Hostname', a.hostname ?? '—'],
            ['IPs locais', (a.local_ips as string[]).join(', ') || '—'],
            ['Sistema', `${a.os ?? '—'} (${a.arch ?? '—'})`],
            ['IP público', a.public_ip ?? '—'],
            ['Local de instalação', a.install_path ?? '—'],
            ['Versão', a.version ?? '—'],
            ['Fila pendente', fmtInt(a.queue_pending)],
            ['CPU / memória', `${fmtDec(a.cpu_percent)}% / ${fmtBytes(a.memory_bytes)}`],
            ['Latência média', a.avg_latency_ms !== null ? `${fmtDec(a.avg_latency_ms)} ms` : '—'],
            ['Configuração', `${String(a.applied_config_version)} de ${String(a.config_version)}`],
            ['Watchdog', <WatchdogBadge key="wd" agent={a} />],
            ['Último erro', a.last_error ?? 'nenhum'],
          ]}
        />
      </Card>
      <AgentStatsCard agentId={a.id} />
      <Tabs defaultValue="health">
        <TabsList>
          <TabsTrigger value="health">Saúde</TabsTrigger>
          <TabsTrigger value="commands">Comandos</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
          <TabsTrigger value="cluster">Cluster</TabsTrigger>
          <TabsTrigger value="ranges">Faixas de IP</TabsTrigger>
          <TabsTrigger value="credentials">Credenciais SNMP</TabsTrigger>
          <TabsTrigger value="settings">Intervalos, SNMP e proxy</TabsTrigger>
          <TabsTrigger value="versions">Versões</TabsTrigger>
        </TabsList>
        <TabsContent value="health">
          <div className="space-y-4">
            <WatchdogCard agent={a} />
            <HealthCharts agentId={a.id} />
          </div>
        </TabsContent>
        <TabsContent value="commands">
          <CommandsTab agentId={a.id} />
        </TabsContent>
        <TabsContent value="logs">
          <LogsTab agentId={a.id} canCommand={can('agents.command')} />
        </TabsContent>
        <TabsContent value="cluster">
          <ClusterTab siteId={a.site_id} canWrite={can('agents.update')} />
        </TabsContent>
        <TabsContent value="ranges">
          <RangesTab agent={a} canWrite={can('agents.update')} />
        </TabsContent>
        <TabsContent value="credentials">
          <CredentialsTab siteId={a.site_id} canWrite={can('agents.update')} />
        </TabsContent>
        <TabsContent value="settings">
          <SiteSettingsTab siteId={a.site_id} canWrite={can('customers.update')} />
        </TabsContent>
        <TabsContent value="versions">
          <VersionsTab agentId={a.id} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

const ACTIONS: [CommandType, string][] = [
  ['reconnect', 'Reconectar'],
  ['scan_now', 'Varrer agora'],
  ['read_now', 'Ler agora'],
  ['diagnostics', 'Diagnóstico'],
  ['set_config', 'Aplicar configuração'],
  ['get_logs', 'Baixar logs'],
  ['promote_master', 'Tornar MASTER'],
  ['restart_watchdog', 'Reiniciar o watchdog'],
];

function AgentActions({ agent: a, canCommand, canWrite }: { agent: Agent; canCommand: boolean; canWrite: boolean }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { send, watcher } = useSendCommand(a.id);
  const [code, setCode] = useState<Schemas['EnrollmentCodeOut'] | null>(null);
  const [editing, setEditing] = useState(false);
  const [dialog, setDialog] = useState<'update' | 'uninstall' | 'set_server' | null>(null);
  const { user } = useAuth();
  const enrolled = Boolean(a.enrolled_at) && !a.revoked_at;
  const watchdogSeen = Boolean(a.last_watchdog_seen_at);
  const previous = watchdogStatus(a).previousAgentVersion;
  // Seção 4.7: desinstalar exige papel admin (e confirmação dupla no diálogo).
  const isAdmin = user?.role === 'superadmin' || user?.role === 'reseller_admin';
  return (
    <>
      {canCommand && enrolled ? <ReactivateButton agentId={a.id} agentName={a.name} /> : null}
      {canCommand && enrolled ? (
        <Menu
          trigger={
            <Button variant="secondary" size="sm">
              Comandos
            </Button>
          }
        >
          {ACTIONS.map(([t, label]) =>
            t === 'scan_now' && a.cluster_role !== 'master' ? (
              // Varrer a rede é só do MASTER do local; o STANDBY assume se o MASTER cair.
              <MenuItem key={t} disabled onSelect={() => undefined}>
                {label} (só o MASTER)
              </MenuItem>
            ) : (
              <MenuItem key={t} onSelect={() => void send(t)}>
                {t === 'read_now' && a.cluster_role !== 'master' ? 'Ler agora (impressoras USB deste PC)' : label}
              </MenuItem>
            ),
          )}
          <MenuItem onSelect={() => void send(a.paused ? 'resume' : 'pause')}>
            {a.paused ? 'Retomar coletas' : 'Pausar coletas'}
          </MenuItem>
          <MenuSeparator />
          {watchdogSeen ? (
            <>
              <MenuItem onSelect={() => void send('restart_agent')}>Reiniciar o coletor (pelo watchdog)</MenuItem>
              <MenuItem onSelect={() => void send('get_logs', { source: 'watchdog' })}>
                Baixar logs do watchdog
              </MenuItem>
            </>
          ) : null}
          <MenuItem
            onSelect={() => {
              setDialog('update');
            }}
          >
            Atualizar…
          </MenuItem>
          {previous ? (
            <MenuItem onSelect={() => void send('rollback')}>Voltar para a versão {previous}</MenuItem>
          ) : null}
          {isAdmin ? (
            <MenuItem
              onSelect={() => {
                setDialog('set_server');
              }}
            >
              Mudar endereço do servidor…
            </MenuItem>
          ) : null}
          {isAdmin && canWrite && watchdogSeen ? (
            <>
              <MenuSeparator />
              <MenuItem
                danger
                onSelect={() => {
                  setDialog('uninstall');
                }}
              >
                Desinstalar do PC…
              </MenuItem>
            </>
          ) : null}
        </Menu>
      ) : null}
      {canWrite ? (
        <>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              setEditing(true);
            }}
          >
            Editar
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              unwrap(api.POST('/api/v1/agents/{agent_id}/enrollment-code', { params: { path: { agent_id: a.id } } }))
                .then(setCode)
                .catch((err: unknown) => {
                  showError(err, 'Código não gerado');
                });
            }}
          >
            {a.enrolled_at ? 'Reinstalar (novo código)' : 'Código de cadastro'}
          </Button>
          {!a.revoked_at ? (
            <ConfirmButton
              title="Revogar coletor"
              description="A credencial para de funcionar na hora e a conexão é derrubada. Para voltar a coletar, será preciso cadastrar de novo."
              confirmLabel="Revogar"
              danger
              onConfirm={() =>
                unwrap(api.POST('/api/v1/agents/{agent_id}/revoke', { params: { path: { agent_id: a.id } } })).then(
                  () => {
                    showSuccess('Coletor revogado');
                    void qc.invalidateQueries({ queryKey: ['agent', a.id] });
                  },
                )
              }
            >
              Revogar
            </ConfirmButton>
          ) : null}
          <ConfirmButton
            title="Excluir coletor"
            description="O coletor sai da lista (as leituras e o histórico continuam guardados)."
            confirmLabel="Excluir"
            danger
            variant="danger"
            onConfirm={() =>
              unwrap(api.DELETE('/api/v1/agents/{agent_id}', { params: { path: { agent_id: a.id } } })).then(() => {
                showSuccess('Coletor excluído');
                void qc.invalidateQueries({ queryKey: ['agents'] });
                void navigate('/coletores');
              })
            }
          >
            <Trash2 className="h-3.5 w-3.5" />
          </ConfirmButton>
        </>
      ) : null}
      {watcher}
      {dialog === 'update' ? (
        <UpdateDialog
          agent={a}
          send={send}
          onClose={() => {
            setDialog(null);
          }}
        />
      ) : null}
      {dialog === 'set_server' ? (
        <SetServerDialog
          agent={a}
          send={send}
          onClose={() => {
            setDialog(null);
          }}
        />
      ) : null}
      {dialog === 'uninstall' ? (
        <UninstallDialog
          agent={a}
          send={send}
          onClose={() => {
            setDialog(null);
          }}
        />
      ) : null}
      {code ? (
        <Dialog
          open
          onOpenChange={(o) => {
            if (!o) setCode(null);
          }}
          title="Código de cadastro"
        >
          <EnrollmentInfo enrollment={code} />
        </Dialog>
      ) : null}
      {editing ? (
        <EditAgentDialog
          agent={a}
          onClose={() => {
            setEditing(false);
          }}
        />
      ) : null}
    </>
  );
}

function EditAgentDialog({ agent, onClose }: { agent: Agent; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(agent.name);
  const [priority, setPriority] = useState(String(agent.priority));
  const [channel, setChannel] = useState(agent.update_channel);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Editar coletor"
      footer={
        <Button
          loading={busy}
          onClick={() => {
            setBusy(true);
            unwrap(
              api.PATCH('/api/v1/agents/{agent_id}', {
                params: { path: { agent_id: agent.id } },
                body: { name, priority: Number(priority), update_channel: channel as 'canary' | 'stable' },
              }),
            )
              .then(() => {
                void qc.invalidateQueries({ queryKey: ['agent', agent.id] });
                showSuccess('Coletor atualizado');
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
        <Field label="Nome" htmlFor="e-name">
          <Input
            id="e-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
            }}
          />
        </Field>
        <Field label="Prioridade no cluster" htmlFor="e-prio" hint="Menor número = preferido para assumir como MASTER">
          <Input
            id="e-prio"
            inputMode="numeric"
            value={priority}
            onChange={(e) => {
              setPriority(e.target.value.replace(/\D/g, ''));
            }}
          />
        </Field>
        <Field label="Canal de atualização" htmlFor="e-channel">
          <Select
            id="e-channel"
            value={channel}
            onChange={(e) => {
              setChannel(e.target.value);
            }}
          >
            <option value="stable">Estável</option>
            <option value="canary">Canary (recebe versões antes)</option>
          </Select>
        </Field>
      </div>
    </Dialog>
  );
}

function HealthCharts({ agentId }: { agentId: string }) {
  const [hours, setHours] = useState(24);
  const q = useQuery({
    queryKey: ['agent', agentId, 'heartbeats', hours],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/agents/{agent_id}/heartbeats', { params: { path: { agent_id: agentId }, query: { hours } } }),
      ),
  });
  const data = (q.data ?? []).map((h) => ({
    t: new Date(h.ts).getTime(),
    CPU: h.cpu_percent ?? 0,
    'Memória (MB)': h.memory_bytes ? Math.round(h.memory_bytes / 1_048_576) : 0,
    Fila: h.queue_pending ?? 0,
    'Latência (ms)': h.latency_ms ?? null,
  }));
  return (
    <Card>
      <CardHeader
        title="Heartbeats"
        actions={
          <Select
            className="h-8 w-32"
            value={hours}
            onChange={(e) => {
              setHours(Number(e.target.value));
            }}
            aria-label="Período"
          >
            <option value={6}>6 horas</option>
            <option value={24}>24 horas</option>
            <option value={168}>7 dias</option>
          </Select>
        }
      />
      <div className="h-72 p-3">
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : data.length < 2 ? (
          <EmptyState title="Poucos heartbeats no período" />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={['dataMin', 'dataMax']}
                tick={{ fontSize: 10 }}
                minTickGap={40}
                tickFormatter={(v: number) => (hours > 24 ? fmtDayTime(v) : fmtTime(v))}
              />
              <YAxis tick={{ fontSize: 11 }} />
              <ChartTooltip labelFormatter={(v) => fmtDateTime(new Date(Number(v)))} />
              <Legend />
              <Line dataKey="CPU" stroke="#2563eb" dot={false} />
              <Line dataKey="Memória (MB)" stroke="#16a34a" dot={false} />
              <Line dataKey="Fila" stroke="#dc2626" dot={false} />
              <Line dataKey="Latência (ms)" stroke="#9333ea" dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}

function CommandsTab({ agentId }: { agentId: string }) {
  const qc = useQueryClient();
  const [watch, setWatch] = useState<string | null>(null);
  const { query: q, rows } = useCursorList<Schemas['CommandOut']>(['commands', agentId], (cursor) =>
    unwrap(
      api.GET('/api/v1/agents/{agent_id}/commands', {
        params: { path: { agent_id: agentId }, query: { limit: PAGE_SIZE, cursor } },
      }),
    ),
  );
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!rows.length) return <EmptyState title="Nenhum comando enviado" />;
  return (
    <Card className="overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-left">Criado</th>
            <th className="px-3 py-2 text-left">Tipo</th>
            <th className="px-3 py-2 text-left">Estado</th>
            <th className="px-3 py-2 text-left">Resultado</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} className="border-t border-slate-100 dark:border-slate-800">
              <td className="px-3 py-1.5 text-xs">{fmtDateTime(c.created_at)}</td>
              <td className="px-3 py-1.5 text-xs">{c.type_label}</td>
              <td className="px-3 py-1.5">
                <CommandState state={c.state} />
              </td>
              <td className="max-w-md truncate px-3 py-1.5 text-xs text-slate-500">
                {typeof c.result?.error === 'string' ? c.result.error : (c.progress ?? '')}
              </td>
              <td className="whitespace-nowrap px-3 py-1.5 text-right">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setWatch(c.id);
                  }}
                >
                  Ver
                </Button>
                {!FINAL_COMMAND_STATES.has(c.state) ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      unwrap(
                        api.POST('/api/v1/commands/{command_id}/cancel', { params: { path: { command_id: c.id } } }),
                      )
                        .then(() => {
                          void qc.invalidateQueries({ queryKey: ['commands', agentId] });
                        })
                        .catch((err: unknown) => {
                          showError(err, 'Não foi possível cancelar');
                        });
                    }}
                  >
                    Cancelar
                  </Button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <LoadMore query={q} shown={rows.length} />
      {watch ? (
        <CommandWatch
          commandId={watch}
          onClose={() => {
            setWatch(null);
          }}
        />
      ) : null}
    </Card>
  );
}

function LogsTab({ agentId, canCommand }: { agentId: string; canCommand: boolean }) {
  const { send, busy, watcher } = useSendCommand(agentId);
  const [text, setText] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ['agent', agentId, 'logs'],
    queryFn: () => unwrap(api.GET('/api/v1/agents/{agent_id}/logs', { params: { path: { agent_id: agentId } } })),
    enabled: canCommand,
  });
  if (!canCommand) return <EmptyState title="Sem permissão para ver os logs" />;
  return (
    <Card>
      <CardHeader
        title="Logs enviados pelo coletor"
        actions={
          <Button size="sm" loading={busy} onClick={() => void send('get_logs', { hours: 24 })}>
            Pedir logs (24 h)
          </Button>
        }
      />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : !q.data.length ? (
        <EmptyState title="Nenhum log enviado ainda" />
      ) : (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {q.data.map((l) => (
            <li key={l.id} className="flex items-center justify-between gap-3 px-4 py-2">
              <span>
                {fmtDateTime(l.created_at)} · {l.hours ?? '?'} h · {fmtBytes(l.size_bytes)}
              </span>
              <span className="flex gap-1">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    fetchText(`/api/v1/agent-logs/${l.id}/tail?lines=500`)
                      .then(setText)
                      .catch((err: unknown) => {
                        showError(err, 'Não foi possível abrir o log');
                      });
                  }}
                >
                  <FileText className="h-3.5 w-3.5" /> Ver últimas linhas
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    void downloadFile(`/api/v1/agent-logs/${l.id}/download`, 'logs.zip').catch((err: unknown) => {
                      showError(err, 'Download falhou');
                    });
                  }}
                >
                  <Download className="h-3.5 w-3.5" />
                </Button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {text !== null ? (
        <Dialog
          open
          onOpenChange={(o) => {
            if (!o) setText(null);
          }}
          title="Log do coletor (últimas 500 linhas, mais novas primeiro)"
          wide
        >
          <LogView text={text} />
        </Dialog>
      ) : null}
      {watcher}
    </Card>
  );
}

function ClusterTab({ siteId, canWrite }: { siteId: string; canWrite: boolean }) {
  const q = useQuery({
    queryKey: ['cluster', siteId],
    queryFn: () => unwrap(api.GET('/api/v1/sites/{site_id}/cluster', { params: { path: { site_id: siteId } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  const names = new Map(q.data.members.map((m) => [m.id, m.name]));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader
          title="Coletores do local"
          subtitle={
            q.data.master_lease_expires_at
              ? `Lease do MASTER até ${fmtDateTime(q.data.master_lease_expires_at)}`
              : undefined
          }
        />
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {q.data.members.map((m) => (
            <li key={m.id} className="flex items-center justify-between gap-2 px-4 py-2">
              <span className="flex items-center gap-2 font-medium">
                {m.name}
                {m.is_preferred ? <Badge tone="blue">preferido</Badge> : null}
              </span>
              <span className="flex items-center gap-2">
                <RoleBadge role={m.cluster_role} />
                <AgentState state={m.state} wsConnected={m.ws_connected} />
                {canWrite ? <PreferredMasterButton siteId={siteId} member={m} /> : null}
              </span>
            </li>
          ))}
        </ul>
      </Card>
      <Card>
        <CardHeader title="Histórico de trocas de MASTER" />
        {q.data.events.length ? (
          <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
            {q.data.events.map((e) => (
              <li key={`${e.created_at}-${e.reason}`} className="px-4 py-2">
                <p>
                  {CLUSTER_REASON[e.reason] ?? e.reason}:{' '}
                  {e.from_agent_id ? (e.from_name ?? names.get(e.from_agent_id) ?? 'coletor removido') : '—'} →{' '}
                  {e.to_agent_id ? (e.to_name ?? names.get(e.to_agent_id) ?? 'coletor removido') : '—'}
                </p>
                <p className="text-xs text-slate-500">{fmtDateTime(e.created_at)}</p>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="Sem trocas registradas" />
        )}
      </Card>
    </div>
  );
}

/** Converte o que o operador digitou em CIDR, início–fim ou IP/hostname avulso (seção 16.10). */
function rangeBody(text: string): { cidr?: string; start_ip?: string; end_ip?: string; host?: string } {
  const v = text.trim();
  if (v.includes('/')) return { cidr: v };
  const ipv4 = /^\d{1,3}(\.\d{1,3}){3}$/;
  const [a, b] = v.split('-').map((x) => x.trim());
  if (b !== undefined && a && ipv4.test(a) && ipv4.test(b)) return { start_ip: a, end_ip: b };
  return { host: v };
}

function rangeLabel(r: Schemas['IpRangeOut']): string {
  if (r.cidr) return r.cidr;
  if (r.host) return r.host;
  return `${r.start_ip ?? ''} – ${r.end_ip ?? ''}`;
}

function RangesTab({ agent, canWrite }: { agent: Agent; canWrite: boolean }) {
  const siteId = agent.site_id;
  const suggested = agent.suggested_ranges as string[];
  const qc = useQueryClient();
  const [entry, setEntry] = useState('');
  const [ports, setPorts] = useState('161');
  const [busy, setBusy] = useState(false);
  const [imported, setImported] = useState<Schemas['RangeImportOut'] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const q = useQuery({
    queryKey: ['ranges', siteId],
    queryFn: () => unwrap(api.GET('/api/v1/sites/{site_id}/ip-ranges', { params: { path: { site_id: siteId } } })),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ['ranges', siteId] });
  const portList = () =>
    ports
      .split(/[,\s]+/)
      .filter(Boolean)
      .map(Number);

  const importFile = (file: File) => {
    setBusy(true);
    file
      .text()
      .then((content) =>
        unwrap(
          api.POST('/api/v1/sites/{site_id}/ip-ranges/import', {
            params: { path: { site_id: siteId } },
            body: { content, ports: portList() },
          }),
        ),
      )
      .then((out) => {
        setImported(out);
        refresh();
      })
      .catch((err: unknown) => {
        showError(err, 'Importação não concluída');
      })
      .finally(() => {
        setBusy(false);
        if (fileRef.current) fileRef.current.value = '';
      });
  };

  const setLocalNetworks = (on: boolean) => {
    unwrap(
      api.PATCH('/api/v1/agents/{agent_id}', {
        params: { path: { agent_id: agent.id } },
        body: { monitor_local_networks: on },
      }),
    )
      .then(() => {
        showSuccess(on ? 'O coletor passa a varrer as redes do PC' : 'Redes do PC deixam de ser varridas');
        void qc.invalidateQueries({ queryKey: ['agent', agent.id] });
      })
      .catch((err: unknown) => {
        showError(err, 'Não foi possível alterar');
      });
  };

  return (
    <Card>
      <CardHeader
        title="Faixas de IP varridas"
        subtitle="O coletor só varre faixas aprovadas, IPs/hostnames avulsos e, se ligado, as redes do próprio PC."
      />
      <label className="flex items-start gap-2 border-b border-slate-200 px-4 py-3 text-sm dark:border-slate-800">
        <input
          type="checkbox"
          className="mt-1"
          checked={agent.monitor_local_networks}
          disabled={!canWrite}
          onChange={(e) => {
            setLocalNetworks(e.target.checked);
          }}
        />
        <span>
          Monitorar redes conectadas
          <span className="block text-xs text-slate-500">
            Varre também as redes /24 privadas das placas de rede deste PC (
            {(agent.local_ips as string[]).join(', ') || 'sem IP'}), acompanhando mudanças de rede. Ligar vale como
            aprovação.
          </span>
        </span>
      </label>
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {q.data.length === 0 ? (
            <li className="px-4 py-3 text-slate-500">
              Nenhuma faixa cadastrada{suggested.length ? ` (o coletor sugeriu ${suggested.join(', ')})` : ''}.
            </li>
          ) : null}
          {q.data.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2">
              <span className="font-mono">
                {rangeLabel(r)} {r.host ? <Badge className="font-sans">avulso</Badge> : null}{' '}
                <span className="text-xs text-slate-500">portas {(r.ports as number[]).join(', ')}</span>
              </span>
              <span className="flex items-center gap-2">
                {r.status === 'suggested' ? (
                  <Badge tone="yellow">Sugerida pelo coletor</Badge>
                ) : (
                  <Badge tone="green">Aprovada</Badge>
                )}
                {canWrite && r.status === 'suggested' ? (
                  <Button
                    size="sm"
                    onClick={() => {
                      unwrap(api.POST('/api/v1/ip-ranges/{range_id}/approve', { params: { path: { range_id: r.id } } }))
                        .then(() => {
                          showSuccess('Faixa aprovada');
                          refresh();
                        })
                        .catch((err: unknown) => {
                          showError(err, 'Não foi possível aprovar');
                        });
                    }}
                  >
                    Aprovar
                  </Button>
                ) : null}
                {canWrite ? (
                  <ConfirmButton
                    title="Remover faixa"
                    description="Os equipamentos dessa faixa deixam de ser lidos por este local."
                    danger
                    confirmLabel="Remover"
                    onConfirm={() =>
                      unwrap(api.DELETE('/api/v1/ip-ranges/{range_id}', { params: { path: { range_id: r.id } } })).then(
                        refresh,
                      )
                    }
                  >
                    Remover
                  </ConfirmButton>
                ) : null}
              </span>
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
            unwrap(
              api.POST('/api/v1/sites/{site_id}/ip-ranges', {
                params: { path: { site_id: siteId } },
                body: { ...rangeBody(entry), ports: portList() },
              }),
            )
              .then(() => {
                setEntry('');
                showSuccess('Adicionado');
                refresh();
              })
              .catch((err: unknown) => {
                showError(err, 'Não adicionado');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          <Field label="Faixa, IP ou hostname" htmlFor="r-cidr">
            <Input
              id="r-cidr"
              className="w-80"
              placeholder="192.168.0.0/24 · 10.0.0.10-10.0.0.50 · impressora-rh"
              value={entry}
              onChange={(e) => {
                setEntry(e.target.value);
              }}
              required
            />
          </Field>
          <Field label="Portas SNMP" htmlFor="r-ports">
            <Input
              id="r-ports"
              className="w-32"
              value={ports}
              onChange={(e) => {
                setPorts(e.target.value);
              }}
            />
          </Field>
          <Button type="submit" loading={busy}>
            Adicionar
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".txt,text/plain"
            className="hidden"
            aria-label="Arquivo .txt de faixas"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importFile(f);
            }}
          />
          <Button
            type="button"
            variant="secondary"
            loading={busy}
            onClick={() => {
              fileRef.current?.click();
            }}
          >
            <Upload className="h-4 w-4" /> Importar .txt
          </Button>
        </form>
      ) : null}
      {imported ? (
        <Dialog
          open
          onOpenChange={(o) => {
            if (!o) setImported(null);
          }}
          title="Importação de faixas"
          description={`${String(imported.created)} adicionada(s), ${String(imported.duplicates)} já existia(m), ${String(imported.errors.length)} com erro.`}
          footer={
            <Button
              onClick={() => {
                setImported(null);
              }}
            >
              Fechar
            </Button>
          }
        >
          {imported.errors.length ? (
            <ul className="max-h-72 space-y-1 overflow-auto text-sm">
              {imported.errors.map((e) => (
                <li key={e.line}>
                  <span className="font-mono">
                    Linha {e.line}: {e.content}
                  </span>{' '}
                  <span className="text-red-600">— {e.error}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm">Todas as linhas válidas foram importadas.</p>
          )}
        </Dialog>
      ) : null}
    </Card>
  );
}

function AgentStatsCard({ agentId }: { agentId: string }) {
  const q = useQuery({
    queryKey: ['agent', agentId, 'stats'],
    queryFn: () => unwrap(api.GET('/api/v1/agents/{agent_id}/stats', { params: { path: { agent_id: agentId } } })),
    refetchInterval: 60_000,
  });
  if (q.isPending) return null;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const st = q.data;
  const items: [string, string][] = [
    ['Leituras (24 h)', fmtInt(st.readings_24h)],
    ['Itens enviados (24 h)', fmtInt(st.items_24h)],
    ['Falhas de leitura (24 h)', fmtInt(st.failures_24h)],
    ['Equipamentos lidos', fmtInt(st.devices_total)],
    ['Sem resposta', fmtInt(st.devices_offline)],
  ];
  return (
    <Card className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-5">
      {items.map(([label, value]) => (
        <div key={label}>
          <p className="text-xs text-slate-500">{label}</p>
          <p className="text-lg font-semibold tabular-nums">{value}</p>
        </div>
      ))}
    </Card>
  );
}

function CredentialsTab({ siteId, canWrite }: { siteId: string; canWrite: boolean }) {
  const qc = useQueryClient();
  const [version, setVersion] = useState<'v1' | 'v2c' | 'v3'>('v2c');
  const [community, setCommunity] = useState('');
  const [v3, setV3] = useState({ user: '', authProto: 'SHA256', authPass: '', privProto: 'AES256', privPass: '' });
  const [busy, setBusy] = useState(false);
  const q = useQuery({
    queryKey: ['credentials', siteId],
    queryFn: () =>
      unwrap(api.GET('/api/v1/sites/{site_id}/snmp-credentials', { params: { path: { site_id: siteId } } })),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ['credentials', siteId] });
  return (
    <Card>
      <CardHeader
        title="Credenciais SNMP"
        subtitle="Testadas em ordem até uma responder. Os segredos nunca aparecem aqui."
      />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : (
        <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
          {q.data.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-2 px-4 py-2">
              <span>
                {c.position}. <Badge>{c.version}</Badge>{' '}
                {c.version === 'v3'
                  ? `usuário ${c.v3_username ?? ''} (${c.v3_auth_protocol ?? 'sem auth'}/${c.v3_priv_protocol ?? 'sem criptografia'})`
                  : `comunidade ${c.community_hint ?? ''}`}
              </span>
              {canWrite ? (
                <ConfirmButton
                  title="Remover credencial"
                  description="Impressoras que só respondem a ela deixam de ser lidas."
                  danger
                  confirmLabel="Remover"
                  onConfirm={() =>
                    unwrap(
                      api.DELETE('/api/v1/snmp-credentials/{cred_id}', { params: { path: { cred_id: c.id } } }),
                    ).then(refresh)
                  }
                >
                  Remover
                </ConfirmButton>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {canWrite ? (
        <form
          className="grid gap-2 border-t border-slate-200 p-4 sm:grid-cols-3 dark:border-slate-800"
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            const body: Schemas['SnmpCredentialIn'] =
              version === 'v3'
                ? {
                    version,
                    v3_username: v3.user,
                    v3_auth_protocol: v3.authProto as 'SHA' | 'SHA256',
                    v3_auth_password: v3.authPass,
                    v3_priv_protocol: v3.privPass ? (v3.privProto as 'AES' | 'AES256') : null,
                    v3_priv_password: v3.privPass || null,
                  }
                : { version, community };
            unwrap(
              api.POST('/api/v1/sites/{site_id}/snmp-credentials', { params: { path: { site_id: siteId } }, body }),
            )
              .then(() => {
                setCommunity('');
                showSuccess('Credencial adicionada');
                refresh();
              })
              .catch((err: unknown) => {
                showError(err, 'Credencial não adicionada');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          <Field label="Versão" htmlFor="c-version">
            <Select
              id="c-version"
              value={version}
              onChange={(e) => {
                setVersion(e.target.value as 'v1' | 'v2c' | 'v3');
              }}
            >
              <option value="v2c">v2c</option>
              <option value="v1">v1</option>
              <option value="v3">v3</option>
            </Select>
          </Field>
          {version === 'v3' ? (
            <>
              <Field label="Usuário" htmlFor="c-user">
                <Input
                  id="c-user"
                  value={v3.user}
                  onChange={(e) => {
                    setV3({ ...v3, user: e.target.value });
                  }}
                  required
                />
              </Field>
              <Field label="Autenticação" htmlFor="c-auth">
                <Select
                  id="c-auth"
                  value={v3.authProto}
                  onChange={(e) => {
                    setV3({ ...v3, authProto: e.target.value });
                  }}
                >
                  <option value="SHA256">SHA-256</option>
                  <option value="SHA">SHA</option>
                </Select>
              </Field>
              <Field label="Senha de autenticação" htmlFor="c-authpass">
                <Input
                  id="c-authpass"
                  type="password"
                  value={v3.authPass}
                  onChange={(e) => {
                    setV3({ ...v3, authPass: e.target.value });
                  }}
                  required
                  minLength={8}
                />
              </Field>
              <Field label="Criptografia" htmlFor="c-priv">
                <Select
                  id="c-priv"
                  value={v3.privProto}
                  onChange={(e) => {
                    setV3({ ...v3, privProto: e.target.value });
                  }}
                >
                  <option value="AES256">AES-256</option>
                  <option value="AES">AES</option>
                </Select>
              </Field>
              <Field label="Senha de criptografia (opcional)" htmlFor="c-privpass">
                <Input
                  id="c-privpass"
                  type="password"
                  value={v3.privPass}
                  onChange={(e) => {
                    setV3({ ...v3, privPass: e.target.value });
                  }}
                />
              </Field>
            </>
          ) : (
            <Field label="Comunidade" htmlFor="c-community">
              <Input
                id="c-community"
                value={community}
                onChange={(e) => {
                  setCommunity(e.target.value);
                }}
                required
              />
            </Field>
          )}
          <div className="flex items-end">
            <Button type="submit" loading={busy}>
              Adicionar
            </Button>
          </div>
        </form>
      ) : null}
    </Card>
  );
}

type CollectionConfig = Schemas['CollectionConfig'];
const INTERVAL_FIELDS: [keyof CollectionConfig, string, string][] = [
  ['counters_minutes', 'Contadores (min)', 'padrão 60'],
  ['supplies_minutes', 'Suprimentos (min)', 'padrão 60'],
  ['status_minutes', 'Status/erros (min)', 'padrão 10'],
  ['attributes_minutes', 'Cadastro/atributos (min)', 'padrão 1440'],
  ['discovery_minutes', 'Descoberta (min)', 'padrão 360'],
  ['snmp_timeout_ms', 'Timeout SNMP na descoberta (ms)', 'padrão 1500'],
  ['snmp_read_timeout_ms', 'Timeout SNMP nas leituras (ms)', 'padrão 2000'],
];

function SiteSettingsTab({ siteId, canWrite }: { siteId: string; canWrite: boolean }) {
  const q = useQuery({
    queryKey: ['site', siteId],
    queryFn: () => unwrap(api.GET('/api/v1/sites/{site_id}', { params: { path: { site_id: siteId } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  // Remonta o formulário quando a configuração salva muda (outro usuário, ou depois de salvar).
  return (
    <SiteSettingsForm
      key={JSON.stringify(q.data.collection_config)}
      siteId={siteId}
      canWrite={canWrite}
      config={q.data.collection_config}
    />
  );
}

function initialSiteForm(cfg: CollectionConfig): Record<string, string> {
  const f: Record<string, string> = {};
  for (const [k] of INTERVAL_FIELDS) {
    const v = cfg[k];
    f[k] = typeof v === 'number' ? String(v) : '';
  }
  f.proxy_url = typeof cfg.proxy_url === 'string' ? cfg.proxy_url : '';
  return f;
}

function SiteSettingsForm({
  siteId,
  canWrite,
  config,
}: {
  siteId: string;
  canWrite: boolean;
  config: CollectionConfig;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState(() => initialSiteForm(config));
  const [keepAwake, setKeepAwake] = useState(config.keep_awake === true);
  // Tentativas por consulta SNMP (1 a 5) = retentativas + 1 (seção 16.10).
  const [attempts, setAttempts] = useState(
    typeof config.snmp_retries === 'number' ? String(config.snmp_retries + 1) : '',
  );
  const [busy, setBusy] = useState(false);
  return (
    <Card className="p-4">
      <form
        className="grid gap-3 sm:grid-cols-3"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          const cfg: Record<string, unknown> = { keep_awake: keepAwake, proxy_url: form.proxy_url || null };
          for (const [k] of INTERVAL_FIELDS) cfg[k] = form[k] ? Number(form[k]) : null;
          cfg.snmp_retries = attempts ? Number(attempts) - 1 : null;
          unwrap(
            api.PATCH('/api/v1/sites/{site_id}', {
              params: { path: { site_id: siteId } },
              body: { collection_config: cfg },
            }),
          )
            .then(() => {
              showSuccess('Configuração salva; os coletores aplicam no próximo heartbeat');
              void qc.invalidateQueries({ queryKey: ['site', siteId] });
            })
            .catch((err: unknown) => {
              showError(err, 'Configuração não salva');
            })
            .finally(() => {
              setBusy(false);
            });
        }}
      >
        {INTERVAL_FIELDS.map(([k, label, hint]) => (
          <Field key={k} label={label} htmlFor={`s-${k}`} hint={`Vazio = ${hint}`}>
            <Input
              id={`s-${k}`}
              inputMode="numeric"
              disabled={!canWrite}
              value={form[k] ?? ''}
              onChange={(e) => {
                setForm({ ...form, [k]: e.target.value.replace(/\D/g, '') });
              }}
            />
          </Field>
        ))}
        <Field label="Tentativas SNMP" htmlFor="s-attempts" hint="Vazio = padrão 2">
          <Select
            id="s-attempts"
            disabled={!canWrite}
            value={attempts}
            onChange={(e) => {
              setAttempts(e.target.value);
            }}
          >
            <option value="">Padrão</option>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={String(n)}>
                {n}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Proxy HTTP (opcional)"
          htmlFor="s-proxy"
          className="sm:col-span-2"
          hint="Ex.: http://proxy.cliente.local:3128"
        >
          <Input
            id="s-proxy"
            disabled={!canWrite}
            value={form.proxy_url ?? ''}
            onChange={(e) => {
              setForm({ ...form, proxy_url: e.target.value });
            }}
          />
        </Field>
        <label className="flex items-center gap-2 self-end text-sm">
          <input
            type="checkbox"
            checked={keepAwake}
            disabled={!canWrite}
            onChange={(e) => {
              setKeepAwake(e.target.checked);
            }}
          />
          Impedir suspensão do PC (mantém o PC acordado)
        </label>
        {canWrite ? (
          <div className="sm:col-span-3">
            <Button type="submit" loading={busy}>
              Salvar
            </Button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}

function VersionsTab({ agentId }: { agentId: string }) {
  const q = useQuery({
    queryKey: ['agent', agentId, 'versions'],
    queryFn: () => unwrap(api.GET('/api/v1/agents/{agent_id}/versions', { params: { path: { agent_id: agentId } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!q.data.length) return <EmptyState title="Sem histórico de versões (últimos 30 dias)" />;
  return (
    <Card>
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-left">Versão</th>
            <th className="px-3 py-2 text-left">Primeira vez</th>
            <th className="px-3 py-2 text-left">Última vez</th>
          </tr>
        </thead>
        <tbody>
          {q.data.map((v) => (
            <tr key={v.version} className="border-t border-slate-100 dark:border-slate-800">
              <td className="px-3 py-1.5 font-mono">{v.version}</td>
              <td className="px-3 py-1.5">{fmtDateTime(v.first_seen)}</td>
              <td className="px-3 py-1.5">{fmtDateTime(v.last_seen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
