import { useQuery, useQueryClient } from '@tanstack/react-query';
import { MoreHorizontal, Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { AgentState, RoleBadge } from '../../components/domain';
import { ReactivateButton } from '../../components/Reactivate';
import { Button } from '../../components/ui/button';
import { Dialog, Menu, MenuItem, MenuSeparator } from '../../components/ui/dialog';
import { Field, Input, Select } from '../../components/ui/form';
import {
  Card,
  Checkbox,
  EmptyState,
  ErrorState,
  PageHeader,
  RelativeTime,
  Spinner,
} from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { useSendCommand, type CommandType } from '../../lib/commands';
import { fmtDateTime, fmtInt } from '../../lib/format';
import { AGENT_STATE } from '../../lib/labels';
import { showError, showSuccess } from '../../lib/notify';
import { WatchdogBadge } from './WatchdogPanels';

type Agent = Schemas['AgentOut'];

const ROW_COMMANDS: [CommandType, string][] = [
  ['reconnect', 'Reconectar'],
  ['scan_now', 'Varrer agora'],
  ['read_now', 'Ler agora'],
  ['diagnostics', 'Diagnóstico'],
  ['get_logs', 'Baixar logs'],
];

export function AgentsPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const [q, setQ] = useState('');
  const [state, setState] = useState(params.get('estado') ?? '');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const list = useQuery({
    queryKey: ['agents', q, state],
    queryFn: () =>
      unwrap(api.GET('/api/v1/agents', { params: { query: { q: q || null, state: state || null, limit: 500 } } })),
  });
  const agents = list.data?.items ?? [];

  async function bulk(type: CommandType, label: string) {
    try {
      const res = await unwrap(
        api.POST('/api/v1/agents/commands/bulk', { body: { agent_ids: [...selected], command: { type, params: {} } } }),
      );
      const ok = res.filter((r) => r.command_id).length;
      const failed = res.filter((r) => r.error);
      showSuccess(`${label}: enviado a ${String(ok)} coletor(es)`);
      failed.forEach((f) => {
        showError(new Error(f.error ?? 'erro'), `Coletor ${f.agent_id.slice(0, 8)}`);
      });
      void qc.invalidateQueries({ queryKey: ['commands'] });
    } catch (err) {
      showError(err, label);
    }
  }

  return (
    <div className="space-y-3">
      <PageHeader
        title="Coletores"
        subtitle="PCs com o dm-agent instalado nos clientes"
        actions={
          can('agents.write') ? (
            <Button
              onClick={() => {
                setCreating(true);
              }}
            >
              <Plus className="h-4 w-4" /> Novo coletor
            </Button>
          ) : null
        }
      />
      <Card className="flex flex-wrap items-center gap-3 p-3">
        <div className="relative min-w-60 flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
          <Input
            className="pl-8"
            placeholder="Nome ou hostname"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
            }}
            aria-label="Pesquisar coletores"
          />
        </div>
        <Select
          className="w-44"
          value={state}
          onChange={(e) => {
            setState(e.target.value);
          }}
          aria-label="Estado"
        >
          <option value="">Todos os estados</option>
          {Object.entries(AGENT_STATE).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label}
            </option>
          ))}
        </Select>
        {selected.size && can('agents.command') ? (
          <Menu trigger={<Button variant="secondary">Ações em massa ({selected.size})</Button>}>
            {ROW_COMMANDS.map(([t, label]) => (
              <MenuItem key={t} onSelect={() => void bulk(t, label)}>
                {label}
              </MenuItem>
            ))}
            <MenuSeparator />
            <MenuItem onSelect={() => void bulk('pause', 'Pausar')}>Pausar coletas</MenuItem>
            <MenuItem onSelect={() => void bulk('resume', 'Retomar')}>Retomar coletas</MenuItem>
          </Menu>
        ) : null}
      </Card>
      <Card className="overflow-hidden">
        {list.isPending ? (
          <Spinner />
        ) : list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : agents.length === 0 ? (
          <EmptyState title="Nenhum coletor">Cadastre um coletor com o botão “Novo coletor”.</EmptyState>
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
                <tr>
                  <th className="w-8 px-3 py-2">
                    <Checkbox
                      checked={selected.size === agents.length ? true : selected.size ? 'indeterminate' : false}
                      onCheckedChange={(v) => {
                        setSelected(v ? new Set(agents.map((a) => a.id)) : new Set());
                      }}
                      label="Selecionar todos"
                    />
                  </th>
                  <th className="px-3 py-2 text-left">Coletor</th>
                  <th className="px-3 py-2 text-left">Estado</th>
                  <th className="px-3 py-2 text-left">Papel</th>
                  <th className="px-3 py-2 text-left">Cliente / local</th>
                  <th className="px-3 py-2 text-left">Hostname / IP</th>
                  <th className="px-3 py-2 text-left">Versão</th>
                  <th className="px-3 py-2 text-left">Último sinal</th>
                  <th className="px-3 py-2 text-right">Fila</th>
                  <th className="px-3 py-2 text-left">Watchdog</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {agents.map((a) => (
                  <AgentRow
                    key={a.id}
                    agent={a}
                    selected={selected.has(a.id)}
                    onSelect={(v) => {
                      setSelected((s) => {
                        const n = new Set(s);
                        if (v) n.add(a.id);
                        else n.delete(a.id);
                        return n;
                      });
                    }}
                    canCommand={can('agents.command')}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {creating ? (
        <NewAgentDialog
          onClose={() => {
            setCreating(false);
          }}
        />
      ) : null}
    </div>
  );
}

function AgentRow({
  agent: a,
  selected,
  onSelect,
  canCommand,
}: {
  agent: Agent;
  selected: boolean;
  onSelect: (v: boolean) => void;
  canCommand: boolean;
}) {
  const { send, watcher } = useSendCommand(a.id);
  const enrolled = Boolean(a.enrolled_at) && !a.revoked_at;
  return (
    <tr className="border-t border-slate-100 dark:border-slate-800" data-testid="agent-row">
      <td className="px-3 py-2">
        <Checkbox checked={selected} onCheckedChange={onSelect} label={`Selecionar ${a.name}`} />
      </td>
      <td className="px-3 py-2">
        <Link to={`/coletores/${a.id}`} className="font-medium text-brand-600 hover:underline dark:text-brand-100">
          {a.name}
        </Link>
        {a.revoked_at ? (
          <p className="text-xs text-red-600">revogado</p>
        ) : !a.enrolled_at ? (
          <p className="text-xs text-amber-600">aguardando instalação</p>
        ) : null}
      </td>
      <td className="px-3 py-2">
        <AgentState state={a.state} wsConnected={a.ws_connected} />
      </td>
      <td className="px-3 py-2">
        <RoleBadge role={a.cluster_role} />
      </td>
      <td className="px-3 py-2 text-xs">
        {a.customer_name}
        <br />
        <span className="text-slate-500">{a.site_name}</span>
      </td>
      <td className="px-3 py-2 text-xs">
        {a.hostname ?? '—'}
        <br />
        <span className="text-slate-500">{(a.local_ips as string[]).join(', ')}</span>
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-xs">{a.version ?? '—'}</td>
      <td className="px-3 py-2 text-xs">
        <RelativeTime value={a.last_seen_at} />
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{fmtInt(a.queue_pending)}</td>
      <td className="px-3 py-2 text-xs">
        <WatchdogBadge agent={a} />
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-right">
        {canCommand && enrolled ? (
          <span className="flex items-center justify-end gap-1">
            <ReactivateButton agentId={a.id} agentName={a.name} />
            <Menu
              trigger={
                <Button size="icon" variant="ghost" aria-label={`Ações de ${a.name}`}>
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              }
            >
              {ROW_COMMANDS.map(([t, label]) => (
                <MenuItem key={t} onSelect={() => void send(t)}>
                  {label}
                </MenuItem>
              ))}
              <MenuSeparator />
              {a.paused ? (
                <MenuItem onSelect={() => void send('resume')}>Retomar coletas</MenuItem>
              ) : (
                <MenuItem onSelect={() => void send('pause')}>Pausar coletas</MenuItem>
              )}
              <MenuItem onSelect={() => void send('restart_watchdog')}>Reiniciar o watchdog</MenuItem>
              {a.last_watchdog_seen_at ? (
                <MenuItem onSelect={() => void send('restart_agent')}>Reiniciar o coletor (pelo watchdog)</MenuItem>
              ) : null}
            </Menu>
          </span>
        ) : null}
        {watcher}
      </td>
    </tr>
  );
}

export function EnrollmentInfo({ enrollment }: { enrollment: Schemas['EnrollmentCodeOut'] }) {
  return (
    <div className="space-y-3 text-sm">
      <div className="rounded-lg bg-brand-50 p-4 text-center dark:bg-slate-800">
        <p className="text-xs text-slate-500">Código de cadastro</p>
        <p className="font-mono text-3xl font-bold tracking-widest" data-testid="enrollment-code">
          {enrollment.code}
        </p>
        <p className="text-xs text-slate-500">válido até {fmtDateTime(enrollment.expires_at)} · uso único</p>
      </div>
      <ol className="list-decimal space-y-1 pl-5">
        {enrollment.instructions.map((i) => (
          <li key={i}>{i.replace(/^\d+\.\s*/, '')}</li>
        ))}
      </ol>
      <div>
        <p className="mb-1 text-xs text-slate-500">Instalação por linha de comando:</p>
        <code className="block break-all rounded bg-slate-900 p-2 text-xs text-slate-100">
          {enrollment.install_command}
        </code>
      </div>
    </div>
  );
}

function NewAgentDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [customer, setCustomer] = useState('');
  const [site, setSite] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Schemas['AgentCreated'] | null>(null);
  const customers = useQuery({
    queryKey: ['customers', 'all'],
    queryFn: () => unwrap(api.GET('/api/v1/customers', { params: { query: { limit: 500 } } })),
  });
  const sites = useQuery({
    queryKey: ['sites', customer],
    queryFn: () => unwrap(api.GET('/api/v1/sites', { params: { query: { customer_id: customer, limit: 500 } } })),
    enabled: Boolean(customer),
  });
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={created ? 'Coletor criado' : 'Novo coletor'}
      description={
        created ? 'Instale o coletor no PC do cliente com este código.' : 'Escolha o local onde o coletor vai ficar.'
      }
      footer={
        created ? (
          <Button onClick={onClose}>Fechar</Button>
        ) : (
          <Button
            loading={busy}
            disabled={!site || name.trim().length < 2}
            onClick={() => {
              setBusy(true);
              unwrap(api.POST('/api/v1/agents', { body: { site_id: site, name: name.trim() } }))
                .then((r) => {
                  setCreated(r);
                  void qc.invalidateQueries({ queryKey: ['agents'] });
                })
                .catch((err: unknown) => {
                  showError(err, 'Coletor não criado');
                })
                .finally(() => {
                  setBusy(false);
                });
            }}
          >
            Gerar código
          </Button>
        )
      }
    >
      {created ? (
        <EnrollmentInfo enrollment={created.enrollment} />
      ) : (
        <div className="grid gap-3">
          <Field label="Cliente" htmlFor="n-customer">
            <Select
              id="n-customer"
              value={customer}
              onChange={(e) => {
                setCustomer(e.target.value);
                setSite('');
              }}
            >
              <option value="">Escolha…</option>
              {(customers.data?.items ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Local" htmlFor="n-site">
            <Select
              id="n-site"
              value={site}
              onChange={(e) => {
                setSite(e.target.value);
              }}
              disabled={!customer}
            >
              <option value="">Escolha…</option>
              {(sites.data?.items ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Nome do coletor" htmlFor="n-name" hint="Ex.: PC da recepção">
            <Input
              id="n-name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
              }}
            />
          </Field>
        </div>
      )}
    </Dialog>
  );
}
