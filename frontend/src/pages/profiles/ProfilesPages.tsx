import { useQuery, useQueryClient } from '@tanstack/react-query';
import { GripVertical, Save } from 'lucide-react';
import { useRef, useState, type DragEvent } from 'react';
import { Link, useParams } from 'react-router';

import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Input, Select, Textarea } from '../../components/ui/form';
import { Badge, Card, CardHeader, EmptyState, ErrorState, PageHeader, Spinner } from '../../components/ui/primitives';
import { api, fetchText, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { useSendCommand } from '../../lib/commands';
import { fmtDateTime, fmtInt } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';

const OID_MIME = 'text/x-dati-oid';

export function ProfilesPage() {
  const q = useQuery({
    queryKey: ['profiles'],
    queryFn: () => unwrap(api.GET('/api/v1/profiles')),
  });
  return (
    <div className="space-y-4">
      <PageHeader
        title="Perfis de modelos"
        subtitle="Como cada marca é lida. OIDs novos saem de um walk real conferido com a folha de contadores."
      />
      <Card>
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} onRetry={() => void q.refetch()} />
        ) : q.data.length === 0 ? (
          <EmptyState title="Nenhum perfil carregado" />
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full text-sm" data-testid="profiles-table">
              <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
                <tr>
                  <th className="px-3 py-2 text-left">Perfil</th>
                  <th className="px-3 py-2 text-left">Descrição</th>
                  <th className="px-3 py-2 text-left">sysObjectID</th>
                  <th className="px-3 py-2 text-right">Versão</th>
                  <th className="px-3 py-2 text-left">Origem</th>
                  <th className="px-3 py-2 text-left">A preencher</th>
                </tr>
              </thead>
              <tbody>
                {q.data.map((p) => (
                  <tr key={p.key} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="px-3 py-1.5 font-medium">
                      <Link className="text-brand-600 hover:underline" to={`/perfis/${p.key}`}>
                        {p.key}
                      </Link>
                    </td>
                    <td className="px-3 py-1.5 text-slate-600 dark:text-slate-400">{p.description ?? '—'}</td>
                    <td className="px-3 py-1.5 font-mono text-xs">{p.sys_object_id_prefix ?? 'qualquer'}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {p.active_version ?? '—'}
                      {p.latest_version !== p.active_version ? (
                        <span className="text-xs text-slate-500"> (última {p.latest_version})</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-1.5">
                      <SourceBadge source={p.source} />
                    </td>
                    <td className="px-3 py-1.5">
                      {p.placeholders ? (
                        <Badge tone="yellow">{p.placeholders} OIDs pelo walk</Badge>
                      ) : (
                        <Badge tone="green">completo</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function SourceBadge({ source }: { source: string | null }) {
  if (source === 'portal') return <Badge tone="blue">publicado no portal</Badge>;
  if (source === 'file') return <Badge>arquivo do repositório</Badge>;
  return <span>—</span>;
}

export function ProfileDetailPage() {
  const { profileKey = '' } = useParams();
  const q = useQuery({
    queryKey: ['profiles', profileKey],
    queryFn: () => unwrap(api.GET('/api/v1/profiles/{key}', { params: { path: { key: profileKey } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  // key força um editor novo quando outra versão fica ativa.
  return <ProfileWorkbench key={`${profileKey}-${String(q.data.active_version)}`} detail={q.data} />;
}

function ProfileWorkbench({ detail }: { detail: Schemas['ProfileDetail'] }) {
  const { can } = useAuth();
  const editable = can('profiles.write');
  const [text, setText] = useState(detail.yaml);
  const [validation, setValidation] = useState<Schemas['ProfileValidation'] | null>(null);
  const [walk, setWalk] = useState<Schemas['MibWalkOut'] | null>(null);
  const editor = useRef<HTMLTextAreaElement>(null);

  function insertAt(oid: string, pos?: number) {
    const el = editor.current;
    const at = pos ?? el?.selectionStart ?? text.length;
    const end = pos ?? el?.selectionEnd ?? text.length;
    // Substitui um PREENCHER_PELO_WALK selecionado ou encosta o OID onde o cursor estiver.
    setText(text.slice(0, at) + oid + text.slice(end));
    setValidation(null);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(at + oid.length, at + oid.length);
    });
  }

  function onDrop(e: DragEvent<HTMLTextAreaElement>) {
    const oid = e.dataTransfer.getData(OID_MIME);
    if (!oid) return;
    e.preventDefault();
    insertAt(oid, e.currentTarget.selectionStart);
  }

  async function validate(): Promise<Schemas['ProfileValidation'] | null> {
    try {
      const v = await unwrap(api.POST('/api/v1/profiles/validate', { body: { yaml: text } }));
      setValidation(v);
      return v;
    } catch (err) {
      showError(err, 'Validação falhou');
      return null;
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title={`Perfil ${detail.key}`}
        subtitle={
          <span>
            Versão ativa {detail.active_version ?? '—'} · as alterações publicadas vão para todos os coletores
          </span>
        }
      />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Editor YAML"
            subtitle="Arraste um OID do walk para o editor, ou selecione PREENCHER_PELO_WALK e clique em Inserir."
            actions={
              <Button size="sm" variant="secondary" onClick={() => void validate()}>
                Validar
              </Button>
            }
          />
          <div className="space-y-3 p-3">
            <Textarea
              ref={editor}
              aria-label="YAML do perfil"
              data-testid="profile-yaml"
              spellCheck={false}
              className="h-[32rem] font-mono text-xs"
              value={text}
              readOnly={!editable}
              onChange={(e) => {
                setText(e.target.value);
                setValidation(null);
              }}
              onDragOver={(e) => {
                if (e.dataTransfer.types.includes(OID_MIME)) e.preventDefault();
              }}
              onDrop={onDrop}
            />
            {validation ? (
              validation.ok ? (
                <p className="text-sm text-emerald-700 dark:text-emerald-400" data-testid="profile-valid">
                  Perfil válido.
                </p>
              ) : (
                <p className="text-sm text-red-600" role="alert">
                  {validation.error}
                </p>
              )
            ) : null}
            {editable ? (
              <PublishBar
                key={walk?.id ?? 'none'}
                text={text}
                profileKey={detail.key}
                validate={validate}
                walk={walk}
              />
            ) : null}
          </div>
        </Card>
        <WalkExplorer walk={walk} onWalk={setWalk} onInsert={editable ? insertAt : undefined} />
      </div>
      <VersionsCard detail={detail} editable={editable} onLoad={setText} />
    </div>
  );
}

function PublishBar({
  text,
  profileKey,
  validate,
  walk,
}: {
  text: string;
  profileKey: string;
  validate: () => Promise<Schemas['ProfileValidation'] | null>;
  walk: Schemas['MibWalkOut'] | null;
}) {
  const qc = useQueryClient();
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [port, setPort] = useState(String(walk?.port ?? 161));
  const { send, busy: sending, watcher } = useSendCommand(walk?.agent_id ?? '');
  return (
    <div className="space-y-3 border-t border-slate-100 pt-3 dark:border-slate-800">
      {walk?.agent_id ? (
        <div className="flex flex-wrap items-end gap-2">
          <Field label={`Testar no equipamento ${walk.ip}`} htmlFor="p-port" hint="Porta SNMP do equipamento">
            <Input
              id="p-port"
              className="w-24"
              inputMode="numeric"
              value={port}
              onChange={(e) => {
                setPort(e.target.value.replace(/\D/g, ''));
              }}
            />
          </Field>
          <Button
            variant="secondary"
            loading={sending}
            onClick={() => {
              void validate().then((v) => {
                if (!v?.ok || !v.profile) return;
                void send('read_device', { ip: walk.ip, port: Number(port) || 161, profile: v.profile });
              });
            }}
          >
            Testar rascunho (sem publicar)
          </Button>
        </div>
      ) : (
        <p className="text-xs text-slate-500">Escolha um walk ao lado para testar o rascunho no equipamento.</p>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Observação da versão" htmlFor="p-notes" className="min-w-64 flex-1">
          <Input
            id="p-notes"
            maxLength={500}
            placeholder="Ex.: contadores conferidos com a folha do M479"
            value={notes}
            onChange={(e) => {
              setNotes(e.target.value);
            }}
          />
        </Field>
        <Button
          loading={busy}
          onClick={() => {
            setBusy(true);
            unwrap(api.POST('/api/v1/profiles', { body: { yaml: text, notes: notes || null } }))
              .then((d) => {
                if (d.key !== profileKey) showSuccess(`Publicado como perfil ${d.key}`);
                else showSuccess(`Versão ${String(d.active_version)} publicada para os coletores`);
                setNotes('');
                void qc.invalidateQueries({ queryKey: ['profiles'] });
              })
              .catch((err: unknown) => {
                showError(err, 'Perfil não publicado');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          Publicar nova versão
        </Button>
      </div>
      {watcher}
    </div>
  );
}

function VersionsCard({
  detail,
  editable,
  onLoad,
}: {
  detail: Schemas['ProfileDetail'];
  editable: boolean;
  onLoad: (yaml: string) => void;
}) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState<number | null>(null);
  return (
    <Card>
      <CardHeader title="Versões" subtitle="O histórico nunca é apagado; ativar uma versão antiga é o rollback." />
      <table className="w-full text-sm" data-testid="profile-versions">
        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-right">Versão</th>
            <th className="px-3 py-2 text-left">Origem</th>
            <th className="px-3 py-2 text-left">Quando</th>
            <th className="px-3 py-2 text-left">Autor</th>
            <th className="px-3 py-2 text-left">Observação</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {detail.versions.map((v) => (
            <tr key={v.version} className="border-t border-slate-100 dark:border-slate-800">
              <td className="px-3 py-1.5 text-right tabular-nums">
                {v.version} {v.active ? <Badge tone="green">ativa</Badge> : null}
              </td>
              <td className="px-3 py-1.5">
                <SourceBadge source={v.source} />
              </td>
              <td className="px-3 py-1.5">{fmtDateTime(v.created_at)}</td>
              <td className="px-3 py-1.5">{v.created_by ?? 'sistema'}</td>
              <td className="px-3 py-1.5 text-slate-600 dark:text-slate-400">{v.notes ?? ''}</td>
              <td className="space-x-1 px-3 py-1.5 text-right">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    fetchText(`/api/v1/profiles/${detail.key}/versions/${String(v.version)}`)
                      .then(onLoad)
                      .catch((err: unknown) => {
                        showError(err, 'Versão não carregada');
                      });
                  }}
                >
                  Abrir no editor
                </Button>
                {editable && !v.active ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={busy === v.version}
                    onClick={() => {
                      setBusy(v.version);
                      unwrap(
                        api.POST('/api/v1/profiles/{key}/activate', {
                          params: { path: { key: detail.key } },
                          body: { version: v.version },
                        }),
                      )
                        .then(() => {
                          showSuccess(`Versão ${String(v.version)} ativa nos coletores`);
                          void qc.invalidateQueries({ queryKey: ['profiles'] });
                        })
                        .catch((err: unknown) => {
                          showError(err, 'Versão não ativada');
                        })
                        .finally(() => {
                          setBusy(null);
                        });
                    }}
                  >
                    Ativar
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

const WALK_PAGE = 200;

function WalkExplorer({
  walk,
  onWalk,
  onInsert,
}: {
  walk: Schemas['MibWalkOut'] | null;
  onWalk: (w: Schemas['MibWalkOut'] | null) => void;
  onInsert?: (oid: string) => void;
}) {
  const { can } = useAuth();
  const walks = useQuery({
    queryKey: ['mib-walks'],
    queryFn: () => unwrap(api.GET('/api/v1/mib-walks')),
  });
  const [value, setValue] = useState('');
  const [text, setText] = useState('');
  const [prefix, setPrefix] = useState('');
  const [offset, setOffset] = useState(0);
  const [saving, setSaving] = useState(false);
  const filters = { value: value.trim() || null, q: text.trim() || null, prefix: prefix.trim() || null };
  const tree = useQuery({
    queryKey: ['mib-walks', walk?.id, 'tree', filters, offset],
    enabled: walk !== null,
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/mib-walks/{walk_id}/tree', {
          params: { path: { walk_id: walk?.id ?? '' }, query: { ...filters, offset, limit: WALK_PAGE } },
        }),
      ),
  });
  return (
    <Card>
      <CardHeader
        title="Explorador do walk"
        subtitle="Digite o valor impresso na folha de contadores para achar o OID que o guarda."
        actions={
          walk && can('profiles.write') ? (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                setSaving(true);
              }}
            >
              <Save className="h-3.5 w-3.5" /> Salvar como gravação de teste
            </Button>
          ) : null
        }
      />
      <div className="space-y-3 p-3">
        {walks.isError ? <ErrorState error={walks.error} onRetry={() => void walks.refetch()} /> : null}
        <Field
          label="Walk"
          htmlFor="w-walk"
          hint={
            walks.data?.length === 0 ? 'Nenhum walk ainda: peça um na página do equipamento (botão Walk).' : undefined
          }
        >
          <Select
            id="w-walk"
            value={walk?.id ?? ''}
            onChange={(e) => {
              onWalk(walks.data?.find((w) => w.id === e.target.value) ?? null);
              setOffset(0);
            }}
          >
            <option value="">Escolha um walk…</option>
            {(walks.data ?? []).map((w) => (
              <option key={w.id} value={w.id}>
                {w.ip} · {fmtDateTime(w.created_at)} · {fmtInt(w.oid_count)} OIDs
              </option>
            ))}
          </Select>
        </Field>
        {walk ? (
          <>
            <div className="grid gap-2 sm:grid-cols-3">
              <Input
                aria-label="Valor da folha"
                placeholder="Valor (ex.: 100.150)"
                value={value}
                onChange={(e) => {
                  setValue(e.target.value);
                  setOffset(0);
                }}
              />
              <Input
                aria-label="Texto"
                placeholder="Texto no OID ou valor"
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  setOffset(0);
                }}
              />
              <Input
                aria-label="Sub-árvore"
                placeholder="Sub-árvore (1.3.6.1.4.1…)"
                value={prefix}
                onChange={(e) => {
                  setPrefix(e.target.value);
                  setOffset(0);
                }}
              />
            </div>
            {tree.isPending ? (
              <Spinner />
            ) : tree.isError ? (
              <ErrorState error={tree.error} onRetry={() => void tree.refetch()} />
            ) : tree.data.total === 0 ? (
              <EmptyState title="Nenhum OID com esse filtro" />
            ) : (
              <>
                <p className="text-xs text-slate-500">
                  {fmtInt(tree.data.total)} de {fmtInt(tree.data.oid_count)} OIDs
                </p>
                <div className="scroll-thin max-h-[28rem] overflow-auto rounded border border-slate-100 dark:border-slate-800">
                  <table className="w-full text-xs" data-testid="walk-rows">
                    <tbody>
                      {tree.data.items.map((r) => (
                        <tr
                          key={r.oid}
                          draggable
                          onDragStart={(e) => {
                            e.dataTransfer.setData(OID_MIME, r.oid);
                            e.dataTransfer.setData('text/plain', r.oid);
                          }}
                          className="cursor-grab border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800"
                        >
                          <td className="w-4 px-1 text-slate-400">
                            <GripVertical className="h-3 w-3" aria-hidden />
                          </td>
                          <td className="px-2 py-1 font-mono">{r.oid}</td>
                          <td className="px-2 py-1 text-slate-500">{r.type}</td>
                          <td className="max-w-64 truncate px-2 py-1" title={r.value}>
                            {r.value}
                          </td>
                          {onInsert ? (
                            <td className="px-2 py-1 text-right">
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => {
                                  onInsert(r.oid);
                                }}
                              >
                                Inserir
                              </Button>
                            </td>
                          ) : null}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="flex justify-between">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={offset === 0}
                    onClick={() => {
                      setOffset(Math.max(0, offset - WALK_PAGE));
                    }}
                  >
                    Anteriores
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={offset + WALK_PAGE >= tree.data.total}
                    onClick={() => {
                      setOffset(offset + WALK_PAGE);
                    }}
                  >
                    Próximos
                  </Button>
                </div>
              </>
            )}
          </>
        ) : null}
      </div>
      {saving && walk ? (
        <FixtureDialog
          walk={walk}
          onClose={() => {
            setSaving(false);
          }}
        />
      ) : null}
    </Card>
  );
}

function FixtureDialog({ walk, onClose }: { walk: Schemas['MibWalkOut']; onClose: () => void }) {
  const [name, setName] = useState('');
  const [profile, setProfile] = useState('');
  const [serial, setSerial] = useState('');
  const [counters, setCounters] = useState({ total: '', mono: '', color: '' });
  const [busy, setBusy] = useState(false);
  const counterFields: [keyof typeof counters, string][] = [
    ['total', 'Total'],
    ['mono', 'PB'],
    ['color', 'Cor'],
  ];
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Salvar walk como gravação de teste"
      description={`Walk de ${walk.ip}. Vai para profiles/recordings/real e passa a rodar nos testes do motor; os valores da folha viram a conferência automática.`}
      footer={
        <Button
          loading={busy}
          disabled={name.length < 3}
          onClick={() => {
            setBusy(true);
            const values = Object.fromEntries(
              Object.entries(counters)
                .filter(([, v]) => v !== '')
                .map(([k, v]) => [k, Number(v)]),
            );
            unwrap(
              api.POST('/api/v1/mib-walks/{walk_id}/fixture', {
                params: { path: { walk_id: walk.id } },
                body: { name, profile: profile || null, serial: serial || null, counters: values },
              }),
            )
              .then((r) => {
                showSuccess(`Gravação ${r.file} salva`);
                onClose();
              })
              .catch((err: unknown) => {
                showError(err, 'Gravação não salva');
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
        <Field label="Nome do arquivo" htmlFor="f-name" hint="marca_modelo, minúsculas (ex.: hp_m479)">
          <Input
            id="f-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value.toLowerCase());
            }}
          />
        </Field>
        <Field label="Perfil esperado" htmlFor="f-profile">
          <Input
            id="f-profile"
            value={profile}
            onChange={(e) => {
              setProfile(e.target.value);
            }}
          />
        </Field>
        <Field label="Número de série" htmlFor="f-serial">
          <Input
            id="f-serial"
            value={serial}
            onChange={(e) => {
              setSerial(e.target.value);
            }}
          />
        </Field>
      </div>
      <p className="mt-4 text-sm font-medium">Folha de contadores (no momento do walk)</p>
      <div className="mt-2 grid gap-3 sm:grid-cols-3">
        {counterFields.map(([k, label]) => (
          <Field key={k} label={label} htmlFor={`f-${k}`}>
            <Input
              id={`f-${k}`}
              inputMode="numeric"
              value={counters[k]}
              onChange={(e) => {
                setCounters({ ...counters, [k]: e.target.value.replace(/\D/g, '') });
              }}
            />
          </Field>
        ))}
      </div>
    </Dialog>
  );
}
