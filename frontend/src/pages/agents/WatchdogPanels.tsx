import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldAlert } from 'lucide-react';
import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Input, Select } from '../../components/ui/form';
import { Badge, Card, CardHeader, EmptyState, ErrorState, KeyValue, Spinner } from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import type { CommandType } from '../../lib/commands';
import { fmtBytes, fmtDateTime } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';
import { watchdogStatus } from '../../lib/watchdog';

type Agent = Schemas['AgentOut'];

const SERVICE_STATE: Record<string, string> = {
  running: 'rodando',
  stopped: 'parado',
  starting: 'iniciando',
  not_installed: 'não instalado',
  unknown: 'desconhecido',
};

/** Selo do vigia na lista de coletores e no cabeçalho. */
export function WatchdogBadge({ agent }: { agent: Agent }) {
  if (agent.watchdog_alive) {
    return <Badge tone="green">ativo{agent.watchdog_version ? ` · ${agent.watchdog_version}` : ''}</Badge>;
  }
  if (agent.last_watchdog_seen_at) {
    return <Badge tone="red">sem sinal desde {fmtDateTime(agent.last_watchdog_seen_at)}</Badge>;
  }
  return <span className="text-xs text-slate-400">não instalado</span>;
}

/** Cartão da aba Saúde: o que o dm-watchdog relata e o que o coletor vê dele (vigilância mútua). */
export function WatchdogCard({ agent }: { agent: Agent }) {
  const s = watchdogStatus(agent);
  const restarts = [...s.restarts].reverse();
  return (
    <Card>
      <CardHeader
        title="Vigia (dm-watchdog)"
        subtitle="Reinicia o coletor travado, atualiza e volta versões; o coletor também vigia o watchdog."
        actions={<WatchdogBadge agent={agent} />}
      />
      <div className="grid gap-4 p-4 lg:grid-cols-2">
        <KeyValue
          items={[
            ['Último sinal do vigia', agent.last_watchdog_seen_at ? fmtDateTime(agent.last_watchdog_seen_at) : 'nunca'],
            ['Versão do vigia', agent.watchdog_version ?? '—'],
            ['Serviço do coletor (visto pelo vigia)', SERVICE_STATE[s.agentState ?? ''] ?? '—'],
            ['/health do coletor', s.agentHealthy === undefined ? '—' : s.agentHealthy ? 'saudável' : 'com problema'],
            ['Memória do coletor', s.agentMemoryBytes ? fmtBytes(s.agentMemoryBytes) : '—'],
            ['Versão guardada para voltar', s.previousAgentVersion || 'nenhuma'],
            ['Serviço do vigia (visto pelo coletor)', SERVICE_STATE[s.serviceState ?? ''] ?? '—'],
          ]}
        />
        <div>
          <p className="mb-2 text-sm font-medium">Reinícios do coletor (24 h)</p>
          {restarts.length ? (
            <ul className="space-y-1 text-sm">
              {restarts.map((r) => (
                <li key={r.at} className="rounded bg-amber-50 px-2 py-1 dark:bg-amber-950/40">
                  <span className="text-xs text-slate-500">{fmtDateTime(r.at)}</span> — {r.reason}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-500">Nenhum reinício.</p>
          )}
          {s.errors.length ? (
            <div className="mt-3 text-sm text-red-700 dark:text-red-400" role="alert">
              {s.errors.map((e) => (
                <p key={e}>{e}</p>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

type Send = (type: CommandType, params?: Record<string, unknown>) => Promise<unknown>;

/** Atualizar o coletor (executado pelo watchdog) ou o watchdog (executado pelo coletor). */
export function UpdateDialog({ agent, send, onClose }: { agent: Agent; send: Send; onClose: () => void }) {
  const [component, setComponent] = useState<'agent' | 'watchdog'>('agent');
  const [version, setVersion] = useState('');
  const q = useQuery({ queryKey: ['releases'], queryFn: () => unwrap(api.GET('/api/v1/releases')) });
  const current = component === 'agent' ? agent.version : agent.watchdog_version;
  const options = (q.data ?? []).filter(
    (r) => r.component === component && r.os === agent.kind && r.arch === agent.arch && !r.yanked,
  );
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Atualizar"
      description="A versão é baixada, conferida (sha256 + assinatura) e instalada; se não ficar saudável em 2 min, volta sozinha para a anterior."
      footer={
        <Button
          disabled={!version}
          onClick={() => {
            void send('update', { version, component }).then(onClose);
          }}
        >
          Atualizar para {version || '…'}
        </Button>
      }
    >
      <div className="grid gap-3">
        <Field label="O que atualizar" htmlFor="up-component">
          <Select
            id="up-component"
            value={component}
            onChange={(e) => {
              setComponent(e.target.value === 'watchdog' ? 'watchdog' : 'agent');
              setVersion('');
            }}
          >
            <option value="agent">Coletor (o watchdog troca o binário)</option>
            <option value="watchdog">Watchdog (o coletor troca o binário)</option>
          </Select>
        </Field>
        <p className="text-sm text-slate-500">
          Versão atual: <span className="font-medium">{current ?? 'desconhecida'}</span> · {agent.kind}/
          {agent.arch ?? '?'}
        </p>
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : options.length === 0 ? (
          <EmptyState title="Nenhuma versão publicada para este PC" />
        ) : (
          <Field label="Versão" htmlFor="up-version">
            <Select
              id="up-version"
              value={version}
              onChange={(e) => {
                setVersion(e.target.value);
              }}
            >
              <option value="">Escolha…</option>
              {options.map((r) => (
                <option key={r.id} value={r.version}>
                  {r.version} ({r.channel === 'canary' ? 'canary' : 'estável'}
                  {r.auto_update_blocked ? ', bloqueada por falhas' : ''})
                </option>
              ))}
            </Select>
          </Field>
        )}
      </div>
    </Dialog>
  );
}

/** Desinstalar (seção 4.7): confirmação dupla — aviso e o nome do coletor digitado de novo. */
export function UninstallDialog({ agent, send, onClose }: { agent: Agent; send: Send; onClose: () => void }) {
  const [typed, setTyped] = useState('');
  const [step, setStep] = useState<1 | 2>(1);
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Desinstalar do PC do cliente"
      footer={
        step === 1 ? (
          <Button
            variant="danger"
            onClick={() => {
              setStep(2);
            }}
          >
            Entendi, continuar
          </Button>
        ) : (
          <Button
            variant="danger"
            disabled={typed.trim() !== agent.name}
            onClick={() => {
              void send('uninstall', { confirm_name: typed.trim() }).then(onClose);
            }}
          >
            Desinstalar agora
          </Button>
        )
      }
    >
      {step === 1 ? (
        <div className="flex gap-3 text-sm">
          <ShieldAlert className="h-6 w-6 shrink-0 text-red-600" aria-hidden />
          <p>
            O vigia remove os serviços do coletor e dele mesmo do PC <strong>{agent.hostname ?? agent.name}</strong>. As
            coletas deste PC param até alguém reinstalar no local. O histórico continua no portal.
          </p>
        </div>
      ) : (
        <Field label={`Digite o nome do coletor: ${agent.name}`} htmlFor="un-name">
          <Input
            id="un-name"
            autoComplete="off"
            value={typed}
            onChange={(e) => {
              setTyped(e.target.value);
            }}
          />
        </Field>
      )}
    </Dialog>
  );
}

/** Fixar/remover o MASTER preferido do local (4.8): assume assim que estiver online. */
export function PreferredMasterButton({ siteId, member }: { siteId: string; member: Schemas['ClusterMember'] }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  return (
    <Button
      size="sm"
      variant={member.is_preferred ? 'secondary' : 'ghost'}
      loading={busy}
      onClick={() => {
        setBusy(true);
        unwrap(
          api.PUT('/api/v1/sites/{site_id}/preferred-master', {
            params: { path: { site_id: siteId } },
            body: { agent_id: member.is_preferred ? null : member.id },
          }),
        )
          .then((cluster) => {
            qc.setQueryData(['cluster', siteId], cluster);
            showSuccess(
              member.is_preferred
                ? 'Preferência removida'
                : `“${member.name}” fixado como MASTER preferido (assume em até 30 s se estiver online)`,
            );
          })
          .catch((err: unknown) => {
            showError(err, 'Preferência não salva');
          })
          .finally(() => {
            setBusy(false);
          });
      }}
    >
      {member.is_preferred ? 'Remover preferência' : 'Fixar como MASTER'}
    </Button>
  );
}
