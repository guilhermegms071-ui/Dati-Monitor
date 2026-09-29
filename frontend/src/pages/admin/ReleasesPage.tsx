import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, Upload } from 'lucide-react';
import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { ConfirmButton, Dialog } from '../../components/ui/dialog';
import { Field, Input, Select, Textarea } from '../../components/ui/form';
import { Badge, Card, EmptyState, ErrorState, PageHeader, Spinner } from '../../components/ui/primitives';
import { api, postBinary, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtBytes, fmtDateTime, fmtDec } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';
import { parseSignOutput, sha256Hex, type SignedRelease } from '../../lib/releases';

type Release = Schemas['ReleaseOut'];

const COMPONENT: Record<string, string> = { agent: 'Coletor', watchdog: 'Watchdog' };

export function ReleasesPage() {
  const { user } = useAuth();
  const isRoot = user?.role === 'superadmin';
  const [publishing, setPublishing] = useState(false);
  const q = useQuery({ queryKey: ['releases'], queryFn: () => unwrap(api.GET('/api/v1/releases')) });
  return (
    <div className="space-y-3">
      <PageHeader
        title="Versões"
        subtitle="Versões assinadas do coletor e do watchdog; atualização automática por canal e liberação gradual"
        actions={
          isRoot ? (
            <Button
              size="sm"
              onClick={() => {
                setPublishing(true);
              }}
            >
              <Plus className="h-4 w-4" /> Publicar versão
            </Button>
          ) : null
        }
      />
      <Card className="overflow-x-auto">
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        ) : q.data.length === 0 ? (
          <EmptyState title="Nenhuma versão publicada">
            Assine o binário com <code>dm-tool sign</code> e publique aqui.
          </EmptyState>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2 text-left">Componente</th>
                <th className="px-3 py-2 text-left">Versão</th>
                <th className="px-3 py-2 text-left">Alvo</th>
                <th className="px-3 py-2 text-left">Canal / liberação</th>
                <th className="px-3 py-2 text-left">Atualizações</th>
                <th className="px-3 py-2 text-left">Falha no canary</th>
                <th className="px-3 py-2 text-left">Publicada</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {q.data.map((r) => (
                <ReleaseRow key={`${r.id}-${r.channel}-${String(r.rollout_percent)}`} release={r} editable={isRoot} />
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {publishing ? (
        <PublishDialog
          onClose={() => {
            setPublishing(false);
          }}
        />
      ) : null}
    </div>
  );
}

function ReleaseRow({ release: r, editable }: { release: Release; editable: boolean }) {
  const qc = useQueryClient();
  const [channel, setChannel] = useState(r.channel);
  const [rollout, setRollout] = useState(String(r.rollout_percent));
  const [busy, setBusy] = useState(false);
  const dirty = channel !== r.channel || rollout !== String(r.rollout_percent);
  const patch = (body: Schemas['ReleaseUpdate']) =>
    unwrap(api.PATCH('/api/v1/releases/{release_id}', { params: { path: { release_id: r.id } }, body })).then(() => {
      void qc.invalidateQueries({ queryKey: ['releases'] });
    });
  return (
    <tr className="border-t border-slate-100 align-top dark:border-slate-800">
      <td className="px-3 py-2">{COMPONENT[r.component] ?? r.component}</td>
      <td className="px-3 py-2">
        <span className="font-mono font-medium">{r.version}</span>
        <p className="font-mono text-[10px] text-slate-400" title={r.sha256}>
          {r.sha256.slice(0, 12)}… · {fmtBytes(r.size_bytes)}
        </p>
        {r.notes ? <p className="text-xs text-slate-500">{r.notes}</p> : null}
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-xs">
        {r.os}/{r.arch}
      </td>
      <td className="px-3 py-2">
        {editable && !r.yanked ? (
          <span className="flex items-center gap-1">
            <Select
              className="h-8 w-28"
              aria-label={`Canal da versão ${r.version}`}
              value={channel}
              onChange={(e) => {
                setChannel(e.target.value);
              }}
            >
              <option value="canary">canary</option>
              <option value="stable">estável</option>
            </Select>
            <Input
              className="h-8 w-16"
              type="number"
              min={0}
              max={100}
              aria-label={`Liberação gradual (%) da versão ${r.version}`}
              value={rollout}
              onChange={(e) => {
                setRollout(e.target.value);
              }}
            />
            <span className="text-xs text-slate-500">%</span>
            {dirty ? (
              <Button
                size="sm"
                loading={busy}
                onClick={() => {
                  setBusy(true);
                  patch({ channel: channel === 'stable' ? 'stable' : 'canary', rollout_percent: Number(rollout) })
                    .then(() => {
                      showSuccess('Versão atualizada');
                    })
                    .catch((err: unknown) => {
                      showError(err, 'Versão não atualizada');
                    })
                    .finally(() => {
                      setBusy(false);
                    });
                }}
              >
                Salvar
              </Button>
            ) : null}
          </span>
        ) : (
          <span className="text-xs">
            {r.channel === 'stable' ? 'estável' : 'canary'} · {r.rollout_percent}%
          </span>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-xs">
        <span className="text-emerald-700 dark:text-emerald-400">{r.updates_succeeded} ok</span> ·{' '}
        <span className="text-red-700 dark:text-red-400">{r.updates_failed} falha(s)</span> · {r.updates_in_progress} em
        andamento
      </td>
      <td className="px-3 py-2">
        <span className="text-xs">{fmtDec(r.canary_failure_percent)}%</span>{' '}
        {r.yanked ? (
          <Badge>retirada</Badge>
        ) : r.auto_update_blocked ? (
          <Badge tone="red">fora da atualização automática</Badge>
        ) : null}
      </td>
      <td className="whitespace-nowrap px-3 py-2 text-xs">{fmtDateTime(r.published_at)}</td>
      <td className="px-3 py-2 text-right">
        {editable ? (
          r.yanked ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                patch({ yanked: false }).catch((err: unknown) => {
                  showError(err, 'Versão não restaurada');
                });
              }}
            >
              Restaurar
            </Button>
          ) : (
            <ConfirmButton
              title={`Retirar a versão ${r.version}`}
              description="Ela deixa de ser oferecida e baixada. Quem já a instalou continua com ela até receber outra."
              confirmLabel="Retirar"
              danger
              onConfirm={() => patch({ yanked: true })}
            >
              Retirar
            </ConfirmButton>
          )
        ) : null}
      </td>
    </tr>
  );
}

function PublishDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [signText, setSignText] = useState('');
  const [channel, setChannel] = useState<'canary' | 'stable'>('canary');
  const [rollout, setRollout] = useState('100');
  const [notes, setNotes] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  let signed: SignedRelease | null = null;
  let parseError: string | null = null;
  if (signText.trim()) {
    try {
      signed = parseSignOutput(signText);
    } catch (err) {
      parseError = err instanceof Error ? err.message : String(err);
    }
  }

  async function submit(s: SignedRelease, f: File) {
    setProblem(null);
    if (f.size !== s.size_bytes || (await sha256Hex(f)) !== s.sha256) {
      // Conferido aqui para não subir 20 MB e só então descobrir que é outro arquivo.
      setProblem('O arquivo escolhido não é o que foi assinado (tamanho ou sha256 diferente).');
      return;
    }
    const qs = new URLSearchParams({
      component: s.component,
      version: s.version,
      os: s.os,
      arch: s.arch,
      signature: s.signature,
      channel,
      rollout_percent: rollout,
    });
    if (notes.trim()) qs.set('notes', notes.trim());
    await postBinary<Release>(`/api/v1/releases?${qs.toString()}`, f);
    showSuccess(`Versão ${s.version} publicada (${s.os}/${s.arch})`);
    void qc.invalidateQueries({ queryKey: ['releases'] });
    onClose();
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Publicar versão"
      description="Compile com scripts\build-agent.ps1 -Version x.y.z, assine com dm-tool sign (chave privada fora do servidor) e cole a saída abaixo."
      footer={
        <Button
          loading={busy}
          disabled={!signed || !file}
          onClick={() => {
            if (!signed || !file) return;
            setBusy(true);
            submit(signed, file)
              .catch((err: unknown) => {
                showError(err, 'Versão não publicada');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          <Upload className="h-4 w-4" /> Publicar
        </Button>
      }
    >
      <div className="grid gap-3">
        <Field label="Binário (dm-agent ou dm-watchdog)" htmlFor="rel-file">
          <Input
            id="rel-file"
            type="file"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
            }}
          />
        </Field>
        <Field label="Saída do dm-tool sign (JSON)" htmlFor="rel-sign" error={parseError}>
          <Textarea
            id="rel-sign"
            rows={6}
            className="font-mono text-xs"
            value={signText}
            onChange={(e) => {
              setSignText(e.target.value);
            }}
          />
        </Field>
        {signed ? (
          <p className="text-sm">
            {COMPONENT[signed.component]} <span className="font-mono">{signed.version}</span> para {signed.os}/
            {signed.arch} · {fmtBytes(signed.size_bytes)}
          </p>
        ) : null}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Canal" htmlFor="rel-channel">
            <Select
              id="rel-channel"
              value={channel}
              onChange={(e) => {
                setChannel(e.target.value === 'stable' ? 'stable' : 'canary');
              }}
            >
              <option value="canary">canary (poucos clientes)</option>
              <option value="stable">estável</option>
            </Select>
          </Field>
          <Field label="Liberação gradual (%)" htmlFor="rel-rollout">
            <Input
              id="rel-rollout"
              type="number"
              min={0}
              max={100}
              value={rollout}
              onChange={(e) => {
                setRollout(e.target.value);
              }}
            />
          </Field>
        </div>
        <Field label="Notas da versão" htmlFor="rel-notes">
          <Textarea
            id="rel-notes"
            rows={2}
            value={notes}
            onChange={(e) => {
              setNotes(e.target.value);
            }}
          />
        </Field>
        {problem ? (
          <p className="rounded-md bg-red-50 p-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300" role="alert">
            {problem}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
