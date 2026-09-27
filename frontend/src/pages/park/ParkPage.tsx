import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown, ArrowUp, Columns3, Download, Filter, RefreshCw, Search, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router';

import { DeviceStatus, SupplyBars } from '../../components/domain';
import { Button } from '../../components/ui/button';
import { ConfirmButton, Dialog, Menu, MenuItem } from '../../components/ui/dialog';
import { Field, Input, Select } from '../../components/ui/form';
import {
  Badge,
  Card,
  Checkbox,
  EmptyState,
  ErrorState,
  PageHeader,
  Spinner,
  Tooltip,
} from '../../components/ui/primitives';
import { api, downloadFile, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtCommunication, fmtDate, fmtDateTime, fmtInt } from '../../lib/format';
import { DEVICE_STATUS } from '../../lib/labels';
import { showError, showSuccess } from '../../lib/notify';
import { useMediaQuery } from '../../lib/useMediaQuery';
import { cn } from '../../lib/utils';

type Row = Schemas['ParkRow'];
type SortKey =
  | 'status'
  | 'ip'
  | 'agent'
  | 'first_seen_at'
  | 'last_read_at'
  | 'asset_tag'
  | 'serial'
  | 'brand'
  | 'model'
  | 'customer'
  | 'total';

interface Column {
  id: string;
  label: string;
  /** Largura mínima (px); com espaço sobrando as colunas crescem na mesma proporção. */
  width: number;
  sort?: SortKey;
  filter?: 'ip' | 'serial' | 'brand' | 'model' | 'asset_tag';
  render: (r: Row) => ReactNode;
}

const COLUMNS: Column[] = [
  {
    id: 'status',
    label: 'Status',
    width: 108,
    sort: 'status',
    filter: 'ip',
    render: (r) => (
      <div className="flex flex-col items-start gap-0.5">
        <span className="font-mono text-xs">{r.ip ?? '—'}</span>
        <DeviceStatus status={r.last_status} disconnected={r.disconnected} />
      </div>
    ),
  },
  {
    id: 'agent',
    label: 'DCA',
    width: 88,
    sort: 'agent',
    render: (r) => <span className="text-xs">{r.agent_name ?? '—'}</span>,
  },
  {
    id: 'first_seen',
    label: 'Descoberta',
    width: 84,
    sort: 'first_seen_at',
    render: (r) => <span className="text-xs">{fmtDate(r.first_seen_at)}</span>,
  },
  {
    id: 'last_read',
    label: 'Comunicação',
    width: 92,
    sort: 'last_read_at',
    render: (r) => (
      <Tooltip content={fmtDateTime(r.last_read_at)}>
        <span className="text-xs">{fmtCommunication(r.last_read_at)}</span>
      </Tooltip>
    ),
  },
  {
    id: 'asset_tag',
    label: 'PAT',
    width: 48,
    sort: 'asset_tag',
    filter: 'asset_tag',
    render: (r) => <span className="text-xs">{r.asset_tag ?? '—'}</span>,
  },
  {
    id: 'serial',
    label: 'Serial',
    width: 108,
    sort: 'serial',
    filter: 'serial',
    render: (r) => (
      <Link
        to={`/parque/${r.id}`}
        className="font-mono text-xs font-medium text-brand-600 hover:underline dark:text-brand-100"
      >
        {r.serial}
      </Link>
    ),
  },
  {
    id: 'brand',
    label: 'Marca',
    width: 80,
    sort: 'brand',
    filter: 'brand',
    render: (r) => <span className="text-xs">{r.brand ?? '—'}</span>,
  },
  {
    id: 'model',
    label: 'Modelo',
    width: 124,
    sort: 'model',
    filter: 'model',
    render: (r) => (
      <div className="flex flex-col">
        <span className="text-xs font-medium">{r.model ?? '—'}</span>
        {r.sector ? <span className="text-[11px] text-slate-500">{r.sector}</span> : null}
      </div>
    ),
  },
  {
    id: 'customer',
    label: 'Cliente',
    width: 112,
    sort: 'customer',
    render: (r) => (
      <div className="flex flex-col">
        <span className="text-xs">{r.customer_name}</span>
        <span className="text-[11px] text-slate-500">{r.site_name}</span>
      </div>
    ),
  },
  {
    id: 'meter',
    label: 'Medidor',
    width: 120,
    sort: 'total',
    render: (r) => (
      <div className="flex flex-col">
        <span className="text-base font-semibold tabular-nums">{fmtInt(r.last_total)}</span>
        <span className="text-[11px] text-slate-500 tabular-nums">
          PB: {fmtInt(r.last_mono)} CL: {fmtInt(r.last_color)}
        </span>
      </div>
    ),
  },
  {
    id: 'monitor',
    label: 'Monitor',
    width: 60,
    render: (r) => (r.monitored ? <Badge tone="green">Sim</Badge> : <Badge>Não</Badge>),
  },
  { id: 'levels', label: 'Níveis', width: 148, render: (r) => <SupplyBars supplies={r.supplies} /> },
];
const DEFAULT_COLUMNS = COLUMNS.map((c) => c.id);
const ROW_HEIGHT = 56;
const CARD_HEIGHT = 128;
const COLUMN_BY_ID = new Map(COLUMNS.map((c) => [c.id, c]));

function cell(id: string, r: Row): ReactNode {
  return COLUMN_BY_ID.get(id)?.render(r) ?? null;
}

/** Linha do parque no celular: o essencial das colunas da tela em um cartão. */
function ParkCard({ row: r }: { row: Row }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col justify-between">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            {cell('serial', r)}
            <span className="truncate text-xs font-medium">{r.model ?? '—'}</span>
          </div>
          <p className="truncate text-[11px] text-slate-500">
            {r.customer_name} / {r.site_name}
          </p>
          <p className="truncate text-[11px] text-slate-500">
            <span className="font-mono">{r.ip ?? '—'}</span> · {fmtCommunication(r.last_read_at)}
          </p>
        </div>
        <DeviceStatus status={r.last_status} disconnected={r.disconnected} />
      </div>
      <div className="flex items-end justify-between gap-2">
        {cell('meter', r)}
        {cell('levels', r)}
      </div>
    </div>
  );
}
const PAGE = 200;

interface Filters {
  q: string;
  ip: string;
  serial: string;
  brand: string;
  model: string;
  asset_tag: string;
  sector: string;
  status: string[];
  customer_id: string;
  site_id: string;
  disconnected: boolean;
  inactive: boolean;
}
const EMPTY: Filters = {
  q: '',
  ip: '',
  serial: '',
  brand: '',
  model: '',
  asset_tag: '',
  sector: '',
  status: [],
  customer_id: '',
  site_id: '',
  disconnected: false,
  inactive: false,
};

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => {
      setV(value);
    }, ms);
    return () => {
      clearTimeout(t);
    };
  }, [value, ms]);
  return v;
}

function toQuery(f: Filters): Record<string, string | string[] | boolean> {
  const out: Record<string, string | string[] | boolean> = {};
  for (const [k, v] of Object.entries(f) as [keyof Filters, Filters[keyof Filters]][]) {
    if (Array.isArray(v) ? v.length : v) out[k] = v;
  }
  return out;
}

function qs(params: Record<string, string | string[] | boolean | number>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v))
      v.forEach((x) => {
        sp.append(k, x);
      });
    else sp.set(k, String(v));
  }
  return sp.toString();
}

export function ParkPage() {
  const { user, can, setUser } = useAuth();
  const qc = useQueryClient();
  const [search] = useSearchParams();
  const [filters, setFilters] = useState<Filters>({
    ...EMPTY,
    disconnected: search.get('desconectados') === '1',
    site_id: search.get('site') ?? '',
  });
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'serial', dir: 'asc' });
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [advanced, setAdvanced] = useState(false);
  const [columnsOpen, setColumnsOpen] = useState(false);
  const debounced = useDebounced(filters, 350);
  const prefCols = (user?.preferences.park_columns as string[] | undefined) ?? DEFAULT_COLUMNS;
  const visible = COLUMNS.filter((c) => prefCols.includes(c.id));

  const query = useInfiniteQuery({
    queryKey: ['park', debounced, sort],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/v1/park', {
          params: {
            query: {
              ...toQuery(debounced),
              sort: sort.key,
              direction: sort.dir,
              limit: PAGE,
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor,
  });
  const counts = useQuery({ queryKey: ['park', 'counts'], queryFn: () => unwrap(api.GET('/api/v1/park/counts')) });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);
  const total = query.data?.pages[0]?.total ?? 0;

  const scrollRef = useRef<HTMLDivElement>(null);
  // Celular: a tabela de 12 colunas vira uma lista de cartões (técnicos em campo, seção 10).
  const compact = useMediaQuery('(max-width: 767px)');
  // O projeto não usa o React Compiler; o aviso só diz que ele pularia este componente.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => (compact ? CARD_HEIGHT : ROW_HEIGHT),
    overscan: 12,
  });
  useEffect(() => {
    virtualizer.measure();
  }, [compact, virtualizer]);
  const items = virtualizer.getVirtualItems();
  const lastIndex = items.at(-1)?.index ?? 0;
  useEffect(() => {
    if (lastIndex >= rows.length - 20 && query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage();
  }, [lastIndex, rows.length, query]);

  const template = [
    selecting ? '36px' : null,
    ...visible.map((c) => `minmax(${String(c.width)}px, ${String(c.width)}fr)`),
  ]
    .filter(Boolean)
    .join(' ');
  const minTableWidth = visible.reduce((sum, c) => sum + c.width, selecting ? 36 : 0);
  const setF = (patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch }));
  };
  const toggleSort = (key: SortKey) => {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }));
  };
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));

  async function saveColumns(cols: string[]) {
    try {
      const me = await unwrap(
        api.PATCH('/api/v1/auth/me/preferences', { body: { preferences: { park_columns: cols } } }),
      );
      setUser(me);
    } catch (err) {
      showError(err, 'Não foi possível salvar as colunas');
    }
  }

  async function bulk(body: Schemas['BulkDevicesIn'], done: string) {
    try {
      const r = await unwrap(api.POST('/api/v1/devices/bulk', { body }));
      showSuccess(
        `${done}: ${String(r.changed)} equipamento(s)${r.skipped.length ? ` (${String(r.skipped.length)} ignorado(s))` : ''}`,
      );
      setSelected(new Set());
      void qc.invalidateQueries({ queryKey: ['park'] });
    } catch (err) {
      showError(err, done);
      throw err;
    }
  }

  const ids = [...selected];
  return (
    <div className="flex h-full flex-col gap-3">
      <PageHeader
        title="Parque de equipamentos"
        subtitle={
          counts.data
            ? `${fmtInt(counts.data.total)} ativos · ${fmtInt(counts.data.disconnected)} desconectados · ${fmtInt(counts.data.inactive)} desativados`
            : undefined
        }
        actions={
          <>
            <Button variant="secondary" size="sm" onClick={() => void query.refetch()} loading={query.isRefetching}>
              <RefreshCw className="h-3.5 w-3.5" /> Atualizar
            </Button>
            <Menu
              trigger={
                <Button variant="secondary" size="sm">
                  <Download className="h-3.5 w-3.5" /> Exportar
                </Button>
              }
            >
              {(['xlsx', 'csv'] as const).map((fmt) => (
                <MenuItem
                  key={fmt}
                  onSelect={() => {
                    void downloadFile(
                      `/api/v1/park/export?${qs({ ...toQuery(debounced), sort: sort.key, direction: sort.dir, format: fmt })}`,
                      `parque.${fmt}`,
                    ).catch((err: unknown) => {
                      showError(err, 'Exportação falhou');
                    });
                  }}
                >
                  {fmt.toUpperCase()} (com os filtros aplicados)
                </MenuItem>
              ))}
            </Menu>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setColumnsOpen(true);
              }}
            >
              <Columns3 className="h-3.5 w-3.5" /> Colunas
            </Button>
          </>
        }
      />

      <Card className="flex flex-wrap items-center gap-3 p-3">
        <div className="relative min-w-60 flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
          <Input
            placeholder="Pesquisa global (serial, IP, modelo, cliente, setor, PAT…)"
            className="pl-8"
            value={filters.q}
            onChange={(e) => {
              setF({ q: e.target.value });
            }}
            aria-label="Pesquisa global"
          />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={selecting}
            onCheckedChange={(v) => {
              setSelecting(v);
              if (!v) setSelected(new Set());
            }}
            label="Selecionar"
          />{' '}
          Selecionar
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={filters.disconnected}
            onCheckedChange={(v) => {
              setF({ disconnected: v });
            }}
            label="Desconectados"
          />{' '}
          Desconectados
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={filters.inactive}
            onCheckedChange={(v) => {
              setF({ inactive: v });
            }}
            label="Desativados"
          />{' '}
          Desativados
        </label>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            setAdvanced(true);
          }}
        >
          <Filter className="h-3.5 w-3.5" /> Filtro avançado
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            setFilters(EMPTY);
          }}
        >
          <X className="h-3.5 w-3.5" /> Limpar filtros
        </Button>
        <span className="text-sm text-slate-500" data-testid="park-total">
          {fmtInt(total)} equipamento(s)
        </span>
      </Card>

      {selecting && selected.size ? (
        <Card className="flex flex-wrap items-center gap-2 border-brand-500 p-2 text-sm">
          <span className="px-2 font-medium">{selected.size} selecionado(s)</span>
          {can('agents.command') ? (
            <Button
              size="sm"
              onClick={() =>
                void bulk({ device_ids: ids, action: 'read_now' }, 'Leitura solicitada').catch(() => undefined)
              }
            >
              Ler agora
            </Button>
          ) : null}
          {can('devices.write') ? (
            <>
              <BulkEdit ids={ids} onSubmit={bulk} />
              <BulkMove ids={ids} onSubmit={bulk} />
              {filters.inactive ? (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    void bulk({ device_ids: ids, action: 'activate' }, 'Reativados').catch(() => undefined)
                  }
                >
                  Reativar
                </Button>
              ) : (
                <ConfirmButton
                  title="Desativar equipamentos"
                  description={`${String(selected.size)} equipamento(s) sairão do parque ativo e dos relatórios (as leituras continuam guardadas).`}
                  confirmLabel="Desativar"
                  danger
                  onConfirm={() => bulk({ device_ids: ids, action: 'deactivate' }, 'Desativados')}
                >
                  Desativar
                </ConfirmButton>
              )}
            </>
          ) : null}
        </Card>
      ) : null}

      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div ref={scrollRef} className="scroll-thin min-h-[420px] flex-1 overflow-auto" data-testid="park-table">
          <div style={{ minWidth: compact ? undefined : minTableWidth }}>
            <div
              className="sticky top-0 z-10 hidden border-b md:grid border-slate-200 bg-slate-100 text-xs font-semibold text-slate-600 dark:border-slate-800 dark:bg-slate-800 dark:text-slate-300"
              style={{ gridTemplateColumns: template }}
              role="row"
            >
              {selecting ? (
                <div className="flex items-center px-2 py-2">
                  <Checkbox
                    checked={allSelected ? true : selected.size ? 'indeterminate' : false}
                    onCheckedChange={(v) => {
                      setSelected(v ? new Set(rows.map((r) => r.id)) : new Set());
                    }}
                    label="Selecionar todos os carregados"
                  />
                </div>
              ) : null}
              {visible.map((c) => (
                <div key={c.id} className="flex flex-col gap-1 px-2 py-2" role="columnheader">
                  {c.sort ? (
                    <button
                      type="button"
                      className="flex items-center gap-1 text-left hover:text-slate-900 dark:hover:text-white"
                      onClick={() => {
                        if (c.sort) toggleSort(c.sort);
                      }}
                    >
                      {c.label}
                      {sort.key === c.sort ? (
                        sort.dir === 'asc' ? (
                          <ArrowUp className="h-3 w-3" />
                        ) : (
                          <ArrowDown className="h-3 w-3" />
                        )
                      ) : null}
                    </button>
                  ) : (
                    <span>{c.label}</span>
                  )}
                  {c.filter ? (
                    <input
                      className="h-6 rounded border border-slate-300 bg-white px-1.5 text-[11px] font-normal dark:border-slate-600 dark:bg-slate-900"
                      placeholder="filtrar"
                      aria-label={`Filtrar ${c.label}`}
                      value={filters[c.filter]}
                      onChange={(e) => {
                        if (c.filter) setF({ [c.filter]: e.target.value });
                      }}
                    />
                  ) : null}
                </div>
              ))}
            </div>
            {query.isPending ? (
              <Spinner />
            ) : query.isError ? (
              <ErrorState error={query.error} onRetry={() => void query.refetch()} />
            ) : rows.length === 0 ? (
              <EmptyState title="Nenhum equipamento com esses filtros" />
            ) : (
              <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
                {items.map((vi) => {
                  const r = rows[vi.index];
                  if (!r) return null;
                  const pick = selecting ? (
                    <Checkbox
                      checked={selected.has(r.id)}
                      onCheckedChange={(v) => {
                        setSelected((s) => {
                          const n = new Set(s);
                          if (v) n.add(r.id);
                          else n.delete(r.id);
                          return n;
                        });
                      }}
                      label={`Selecionar ${r.serial}`}
                    />
                  ) : null;
                  const position = { transform: `translateY(${String(vi.start)}px)`, height: vi.size };
                  if (compact) {
                    return (
                      <div
                        key={r.id}
                        role="row"
                        data-testid="park-row"
                        className={cn(
                          'absolute left-0 flex w-full gap-2 border-b border-slate-100 px-3 py-2 dark:border-slate-800',
                          selected.has(r.id) && 'bg-brand-50 dark:bg-slate-800',
                        )}
                        style={position}
                      >
                        {pick}
                        <ParkCard row={r} />
                      </div>
                    );
                  }
                  return (
                    <div
                      key={r.id}
                      role="row"
                      data-testid="park-row"
                      className={cn(
                        'absolute left-0 grid w-full items-center border-b border-slate-100 text-sm hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60',
                        selected.has(r.id) && 'bg-brand-50 dark:bg-slate-800',
                      )}
                      style={{ gridTemplateColumns: template, ...position }}
                    >
                      {pick ? <div className="px-2">{pick}</div> : null}
                      {visible.map((c) => (
                        <div key={c.id} className="min-w-0 truncate px-2">
                          {c.render(r)}
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>
            )}
            {query.isFetchingNextPage ? <Spinner label="Carregando mais…" /> : null}
          </div>
        </div>
      </Card>

      {advanced ? <AdvancedFilters open onOpenChange={setAdvanced} filters={filters} onApply={setFilters} /> : null}
      <Dialog
        open={columnsOpen}
        onOpenChange={setColumnsOpen}
        title="Colunas visíveis"
        description="A escolha fica salva no seu usuário."
      >
        <div className="grid grid-cols-2 gap-2">
          {COLUMNS.map((c) => (
            <label key={c.id} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={prefCols.includes(c.id)}
                onCheckedChange={(v) => {
                  const next = v ? [...prefCols, c.id] : prefCols.filter((x) => x !== c.id);
                  void saveColumns(COLUMNS.map((x) => x.id).filter((id) => next.includes(id)));
                }}
                label={c.label}
              />
              {c.label}
            </label>
          ))}
        </div>
        <Button className="mt-4" variant="secondary" size="sm" onClick={() => void saveColumns(DEFAULT_COLUMNS)}>
          Restaurar padrão
        </Button>
      </Dialog>
    </div>
  );
}

function useCustomersAndSites(customerId: string) {
  const customers = useQuery({
    queryKey: ['customers', 'all'],
    queryFn: () => unwrap(api.GET('/api/v1/customers', { params: { query: { limit: 500 } } })),
  });
  const sites = useQuery({
    queryKey: ['sites', customerId],
    queryFn: () => unwrap(api.GET('/api/v1/sites', { params: { query: { customer_id: customerId, limit: 500 } } })),
    enabled: Boolean(customerId),
  });
  return { customers: customers.data?.items ?? [], sites: sites.data?.items ?? [] };
}

function AdvancedFilters({
  open,
  onOpenChange,
  filters,
  onApply,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  filters: Filters;
  onApply: (f: Filters) => void;
}) {
  const [draft, setDraft] = useState(filters);
  const { customers, sites } = useCustomersAndSites(draft.customer_id);
  const set = (p: Partial<Filters>) => {
    setDraft((d) => ({ ...d, ...p }));
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Filtro avançado"
      footer={
        <>
          <Button
            variant="secondary"
            onClick={() => {
              setDraft(EMPTY);
            }}
          >
            Limpar
          </Button>
          <Button
            onClick={() => {
              onApply(draft);
              onOpenChange(false);
            }}
          >
            Aplicar
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Cliente" htmlFor="f-customer">
          <Select
            id="f-customer"
            value={draft.customer_id}
            onChange={(e) => {
              set({ customer_id: e.target.value, site_id: '' });
            }}
          >
            <option value="">Todos</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Local" htmlFor="f-site">
          <Select
            id="f-site"
            value={draft.site_id}
            onChange={(e) => {
              set({ site_id: e.target.value });
            }}
            disabled={!draft.customer_id}
          >
            <option value="">Todos</option>
            {sites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>
        {(['brand', 'model', 'sector', 'asset_tag', 'serial', 'ip'] as const).map((k) => (
          <Field
            key={k}
            label={
              { brand: 'Marca', model: 'Modelo', sector: 'Setor', asset_tag: 'PAT', serial: 'Serial', ip: 'IP' }[k]
            }
            htmlFor={`f-${k}`}
          >
            <Input
              id={`f-${k}`}
              value={draft[k]}
              onChange={(e) => {
                set({ [k]: e.target.value });
              }}
            />
          </Field>
        ))}
      </div>
      <fieldset className="mt-4">
        <legend className="mb-2 text-sm font-medium">Status</legend>
        <div className="grid grid-cols-2 gap-2">
          {Object.entries(DEVICE_STATUS).map(([k, v]) => (
            <label key={k} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={draft.status.includes(k)}
                onCheckedChange={(on) => {
                  set({ status: on ? [...draft.status, k] : draft.status.filter((s) => s !== k) });
                }}
                label={v.label}
              />
              {v.label}
            </label>
          ))}
        </div>
      </fieldset>
    </Dialog>
  );
}

type BulkFn = (body: Schemas['BulkDevicesIn'], done: string) => Promise<void>;

function BulkEdit({ ids, onSubmit }: { ids: string[]; onSubmit: BulkFn }) {
  const [open, setOpen] = useState(false);
  const [sector, setSector] = useState('');
  const [pat, setPat] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => {
          setOpen(true);
        }}
      >
        Editar setor/PAT
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Editar setor e PAT"
        description={`${String(ids.length)} equipamento(s). Campos vazios não são alterados.`}
        footer={
          <Button
            loading={busy}
            onClick={() => {
              setBusy(true);
              onSubmit(
                { device_ids: ids, action: 'update', sector: sector || null, asset_tag: pat || null },
                'Atualizados',
              )
                .then(() => {
                  setOpen(false);
                })
                .catch(() => undefined)
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
          <Field label="Setor" htmlFor="b-sector">
            <Input
              id="b-sector"
              value={sector}
              onChange={(e) => {
                setSector(e.target.value);
              }}
            />
          </Field>
          <Field label="PAT" htmlFor="b-pat">
            <Input
              id="b-pat"
              value={pat}
              onChange={(e) => {
                setPat(e.target.value);
              }}
            />
          </Field>
        </div>
      </Dialog>
    </>
  );
}

function BulkMove({ ids, onSubmit }: { ids: string[]; onSubmit: BulkFn }) {
  const [open, setOpen] = useState(false);
  const [customer, setCustomer] = useState('');
  const [site, setSite] = useState('');
  const [busy, setBusy] = useState(false);
  const { customers, sites } = useCustomersAndSites(customer);
  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => {
          setOpen(true);
        }}
      >
        Mover de cliente/local
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Mover equipamentos"
        description={`${String(ids.length)} equipamento(s). A troca fica registrada na linha do tempo de cada um.`}
        footer={
          <Button
            loading={busy}
            disabled={!site}
            onClick={() => {
              setBusy(true);
              onSubmit({ device_ids: ids, action: 'move', site_id: site }, 'Movidos')
                .then(() => {
                  setOpen(false);
                })
                .catch(() => undefined)
                .finally(() => {
                  setBusy(false);
                });
            }}
          >
            Mover
          </Button>
        }
      >
        <div className="grid gap-3">
          <Field label="Cliente" htmlFor="m-customer">
            <Select
              id="m-customer"
              value={customer}
              onChange={(e) => {
                setCustomer(e.target.value);
                setSite('');
              }}
            >
              <option value="">Escolha…</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Local" htmlFor="m-site">
            <Select
              id="m-site"
              value={site}
              onChange={(e) => {
                setSite(e.target.value);
              }}
              disabled={!customer}
            >
              <option value="">Escolha…</option>
              {sites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </Dialog>
    </>
  );
}
