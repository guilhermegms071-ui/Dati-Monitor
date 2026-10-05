import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Upload } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

import { Button } from '../../components/ui/button';
import { Field, Input, Select, Textarea } from '../../components/ui/form';
import { Badge, Card, CardHeader, EmptyState, ErrorState, PageHeader, Spinner } from '../../components/ui/primitives';
import { api, downloadFile, postBinary, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtBytes, fmtDateTime } from '../../lib/format';
import { guessInstaller as guess } from '../../lib/installers';
import { showError, showSuccess } from '../../lib/notify';

type Installer = Schemas['InstallerOut'];

const KIND_LABEL: Record<Installer['kind'], string> = {
  windows: 'Windows (setup.exe)',
  deb: 'Linux .deb (Debian, Ubuntu, Raspberry Pi OS)',
  tar: 'Linux .tar.gz (outras distribuições)',
};
const ARCH_LABEL: Record<string, string> = {
  all: '64 e 32 bits, ARM',
  amd64: 'amd64 (64 bits)',
  '386': '386 (32 bits)',
  arm64: 'arm64',
  arm: 'arm (Raspberry Pi)',
};

export function DownloadsPage() {
  const { user } = useAuth();
  const q = useQuery({ queryKey: ['installers'], queryFn: () => unwrap(api.GET('/api/v1/installers')) });
  const superadmin = user?.role === 'superadmin';
  return (
    <div className="space-y-4">
      <PageHeader
        title="Downloads"
        related={[{ to: '/versoes', label: 'Versões do coletor' }]}
        subtitle={
          <span>
            Instaladores do coletor. Para instalar num cliente, use o link com o código em Coletores → Novo coletor
            (funciona sem login). As versões do coletor e do watchdog ficam em{' '}
            <Link className="text-brand-600 hover:underline" to="/versoes">
              Versões
            </Link>
            .
          </span>
        }
      />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <InstallerTable rows={q.data} canManage={superadmin} />
      )}
      {superadmin ? <PublishCard /> : null}
    </div>
  );
}

function InstallerTable({ rows, canManage }: { rows: Installer[]; canManage: boolean }) {
  const qc = useQueryClient();
  if (!rows.length) return <EmptyState title="Nenhum instalador publicado ainda" />;
  const toggle = (i: Installer) => {
    unwrap(
      api.PATCH('/api/v1/installers/{installer_id}', {
        params: { path: { installer_id: i.id } },
        body: { withdrawn: !i.withdrawn },
      }),
    )
      .then(() => {
        showSuccess(i.withdrawn ? 'Instalador devolvido' : 'Instalador retirado');
        void qc.invalidateQueries({ queryKey: ['installers'] });
      })
      .catch((err: unknown) => {
        showError(err, 'Alteração não salva');
      });
  };
  return (
    <Card>
      <table className="w-full text-sm" data-testid="installers">
        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-left">Sistema</th>
            <th className="px-3 py-2 text-left">Arquitetura</th>
            <th className="px-3 py-2 text-left">Versão</th>
            <th className="px-3 py-2 text-left">Arquivo</th>
            <th className="px-3 py-2 text-left">Publicado</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((i) => (
            <tr key={i.id} className="border-t border-slate-100 align-top dark:border-slate-800">
              <td className="px-3 py-1.5">{KIND_LABEL[i.kind]}</td>
              <td className="px-3 py-1.5">{ARCH_LABEL[i.arch] ?? i.arch}</td>
              <td className="px-3 py-1.5">
                {i.version} {i.latest ? <Badge tone="green">oferecida</Badge> : null}
                {i.withdrawn ? <Badge tone="red">retirada</Badge> : null}
                {i.notes ? <span className="block text-xs text-slate-500">{i.notes}</span> : null}
              </td>
              <td className="px-3 py-1.5 text-xs">
                {i.filename} · {fmtBytes(i.size_bytes)}
                <span className="block break-all font-mono text-[10px] text-slate-400">sha256 {i.sha256}</span>
              </td>
              <td className="px-3 py-1.5 text-xs">
                {fmtDateTime(i.created_at)}
                {i.published_by ? <span className="block text-slate-500">{i.published_by}</span> : null}
              </td>
              <td className="space-x-1 whitespace-nowrap px-3 py-1.5 text-right">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    downloadFile(`/api/v1/installers/${i.id}/file`, i.filename).catch((err: unknown) => {
                      showError(err, 'Download falhou');
                    });
                  }}
                >
                  <Download className="h-3.5 w-3.5" /> Baixar
                </Button>
                {canManage ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      toggle(i);
                    }}
                  >
                    {i.withdrawn ? 'Devolver' : 'Retirar'}
                  </Button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function PublishCard() {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<Installer['kind']>('windows');
  const [arch, setArch] = useState('all');
  const [version, setVersion] = useState('');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <Card>
      <CardHeader
        title="Publicar instalador"
        subtitle="Gere com scripts\build-installer.ps1 (Windows) e scripts\build_linux.py (.deb e .tar.gz)."
      />
      <div className="grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Arquivo" htmlFor="pub-file" className="sm:col-span-2">
          <Input
            id="pub-file"
            type="file"
            accept=".exe,.deb,.gz"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null;
              setFile(f);
              if (f) {
                const g = guess(f.name);
                setKind(g.kind);
                setArch(g.arch);
                setVersion(g.version);
              }
            }}
          />
        </Field>
        <Field label="Tipo" htmlFor="pub-kind">
          <Select
            id="pub-kind"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as Installer['kind']);
            }}
          >
            <option value="windows">Windows (setup.exe)</option>
            <option value="deb">Linux .deb</option>
            <option value="tar">Linux .tar.gz</option>
          </Select>
        </Field>
        <Field label="Arquitetura" htmlFor="pub-arch">
          <Select
            id="pub-arch"
            value={arch}
            onChange={(e) => {
              setArch(e.target.value);
            }}
          >
            {Object.entries(ARCH_LABEL).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Versão" htmlFor="pub-version">
          <Input
            id="pub-version"
            value={version}
            placeholder="1.0.0"
            onChange={(e) => {
              setVersion(e.target.value.trim());
            }}
          />
        </Field>
        <Field label="Notas" htmlFor="pub-notes" className="sm:col-span-2 lg:col-span-3">
          <Textarea
            id="pub-notes"
            value={notes}
            onChange={(e) => {
              setNotes(e.target.value);
            }}
          />
        </Field>
      </div>
      <div className="p-3 pt-0">
        <Button
          loading={busy}
          disabled={!file || !version}
          onClick={() => {
            if (!file) return;
            setBusy(true);
            const qs = new URLSearchParams({ kind, arch, version, filename: file.name, ...(notes ? { notes } : {}) });
            postBinary<Installer>(`/api/v1/installers?${qs.toString()}`, file)
              .then((i) => {
                showSuccess(`Instalador ${i.filename} publicado`);
                setFile(null);
                setNotes('');
                void qc.invalidateQueries({ queryKey: ['installers'] });
              })
              .catch((err: unknown) => {
                showError(err, 'Instalador não publicado');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          <Upload className="h-4 w-4" /> Publicar
        </Button>
      </div>
    </Card>
  );
}
