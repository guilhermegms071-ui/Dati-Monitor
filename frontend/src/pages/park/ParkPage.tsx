import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown, ArrowUp, Download, Filter, RefreshCw, ScanSearch, Search, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';

import { SupplyBars } from '../../components/domain';
import { Button } from '../../components/ui/button';
import { ConfirmButton, Dialog, Menu, MenuItem } from '../../components/ui/dialog';
import { CustomerPicker, SitePicker } from '../../components/pickers';
import { Field, Input } from '../../components/ui/form';
import { Card, Checkbox, EmptyState, ErrorState, PageHeader, Spinner } from '../../components/ui/primitives';
import { api, downloadFile, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtInt } from '../../lib/format';
import { DEVICE_STATUS } from '../../lib/labels';
import { colorValue } from '../../lib/park';
import { showError, showSuccess } from '../../lib/notify';
import { printerName } from '../../lib/printers';
import { useMediaQuery } from '../../lib/useMediaQuery';
import { cn } from '../../lib/utils';

import { LastCommunication, Num, StatusPill, TonerBars } from './parkCells';

type Row = Schemas['ParkRow'];
type SortKey = 'status' | 'serial' | 'customer' | 'total' | 'mono' | 'color' | 'last_read_at';
type Tab = 'all' | 'disconnected' | 'alert' | 'inactive';

interface Column {
  id: string;
  label: string;
  /** Trilha do grid (larguras fixas para status e números; o resto divide o espaço). */
  track: string;
  min: number;
  sort?: SortKey;
  numeric?: boolean;
  render: (r: Row, tab: Tab) => ReactNode;
}

// Colunas fixas, nesta ordem (sem configuração por usuário): leitura rápida e igual para todos.
const COLUMNS: Column[] = [
  { id: 'status', label: 'Status', track: '132px', min: 132, sort: 'status', render: (r) => <StatusPill row={r} /> },
  {
    id: 'device',
    label: 'Equipamento',
    track: 'minmax(220px, 2fr)',
    min: 220,
    sort: 'serial',
    render: (r) => (
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
          {printerName(r.brand, r.model) || 'Modelo não identificado'}
        </span>
        <Link
          to={`/parque/${r.id}`}
          className="truncate font-mono text-[13px] text-[#71717A] hover:underline"
          onClick={(e) => {
            e.stopPropagation();
          }}
        >
          {r.serial}
        </Link>
      </div>
    ),
  },
  {
    id: 'customer',
    label: 'Cliente/Setor',
    track: 'minmax(180px, 1.5fr)',
    min: 180,
    sort: 'customer',
    render: (r) => (
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-sm text-zinc-800 dark:text-zinc-200">{r.customer_name}</span>
        {r.sector ? <span className="truncate text-[13px] text-[#71717A]">{r.sector}</span> : null}
      </div>
    ),
  },
  {
    id: 'total',
    label: 'Total',
    track: '112px',
    min: 112,
    sort: 'total',
    numeric: true,
    render: (r) => <Num value={r.last_total} />,
  },
  {
    id: 'mono',
    label: 'PB',
    track: '104px',
    min: 104,
    sort: 'mono',
    numeric: true,
    render: (r) => <Num value={r.last_mono} />,
  },
  {
    id: 'color',
    label: 'Cor',
    track: '104px',
    min: 104,
    sort: 'color',
    numeric: true,
    render: (r) => <Num value={colorValue(r)} />,
  },
  { id: 'toner', label: 'Toner', track: '96px', min: 96, render: (r) => <TonerBars supplies={r.supplies} /> },
  {
    id: 'last_read',
    label: 'Última comunicação',
    track: '168px',
    min: 168,
    sort: 'last_read_at',
    render: (r, tab) => <LastCommunication row={r} highlight={tab === 'disconnected'} />,
  },
];
const ROW_HEIGHT = 56;
const CARD_HEIGHT = 128;

/** Linha do parque no celular: o essencial em um cartão (sem hover: os níveis aparecem em %). */
function ParkCard({ row: r }: { row: Row }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col justify-between">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <Link to={`/parque/${r.id}`} className="font-mono text-xs font-medium text-brand-600 hover:underline">
              {r.serial}
            </Link>
            <span className="truncate text-xs font-medium">{printerName(r.brand, r.model) || '—'}</span>
          </div>
          <p className="truncate text-[11px] text-slate-500">
            {r.customer_name} / {r.site_name}
          </p>
          <div className="text-[11px] text-slate-500">
            <LastCommunication row={r} />
          </div>
        </div>
        <StatusPill row={r} />
      </div>
      <div className="flex items-end justify-between gap-2">
        <div className="flex flex-col">
          <span className="font-mono text-base font-semibold tabular-nums">{fmtInt(r.last_total)}</span>
          <span className="font-mono text-[11px] text-slate-500 tabular-nums">
            PB: {fmtInt(r.last_mono)} Cor: {colorValue(r) === null ? '—' : fmtInt(colorValue(r))}
          </span>
        </div>
        <SupplyBars supplies={r.supplies} />
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
};

const TAB_QUERY: Record<Tab, Record<string, boolean>> = {
  all: {},
  disconnected: { disconnected: true },
  alert: { alert: true },
  inactive: { inactive: true },
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

function toQuery(f: Filters, tab: Tab): Record<string, string | string[] | boolean> {
  const out: Record<string, string | string[] | boolean> = { ...TAB_QUERY[tab] };
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

function TabChip({
  active,
  label,
  count,
  title,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number | undefined;
  title?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      title={title}
      onClick={onClick}
      className={cn(
        'inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-sm transition-colors',
        active
          ? 'border-zinc-900 bg-zinc-900 font-medium text-white dark:border-white dark:bg-white dark:text-zinc-900'
          : 'border-zinc-200 bg-white text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300',
      )}
    >
      {label}
      <span className={cn('font-mono text-xs tabular-nums', active ? 'opacity-80' : 'text-zinc-400')}>
        ({count === undefined ? '…' : fmtInt(count)})
      </span>
    </button>
  );
}

export function ParkPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const [tab, setTab] = useState<Tab>(search.get('desconectados') === '1' ? 'disconnected' : 'all');
  const [filters, setFilters] = useState<Filters>({ ...EMPTY, site_id: search.get('site') ?? '' });
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' }>({ key: 'serial', dir: 'asc' });
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [advanced, setAdvanced] = useState(false);
  const debounced = useDebounced(filters, 350);

  const query = useInfiniteQuery({
    queryKey: ['park', debounced, tab, sort],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/v1/park', {
          params: {
            query: {
              ...toQuery(debounced, tab),
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
  const pending = useQuery({
    queryKey: ['discoveries', 'count'],
    queryFn: () => unwrap(api.GET('/api/v1/discoveries/counts')),
    enabled: can('devices.read'),
  });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);
  const total = query.data?.pages[0]?.total ?? 0;

  const scrollRef = useRef<HTMLDivElement>(null);
  // Celular: a tabela vira uma lista de cartões (técnicos em campo, seção 10).
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

  const template = [selecting ? '40px' : null, ...COLUMNS.map((c) => c.track)].filter(Boolean).join(' ');
  const minTableWidth = COLUMNS.reduce((sum, c) => sum + c.min, selecting ? 40 : 0);
  const setF = (patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch }));
  };
  const toggleSort = (key: SortKey) => {
    setSort((s) => (s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }));
  };
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const toggleRow = (id: string, on: boolean) => {
    setSelected((s) => {
      const n = new Set(s);
      if (on) n.add(id);
      else n.delete(id);
      return n;
    });
  };
  const openRow = (r: Row) => {
    if (selecting) toggleRow(r.id, !selected.has(r.id));
    else void navigate(`/parque/${r.id}`);
  };

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

  const c = counts.data;
  const ids = [...selected];
  const pendingCount = pending.data?.pending ?? 0;
  return (
    <div className="flex h-full flex-col gap-3">
      <PageHeader
        title="Parque"
        subtitle="Impressoras monitoradas: situação, contadores e toner"
        related={[{ to: '/computadores', label: 'Impressoras USB (Computadores)' }]}
        actions={
          <>
            {can('devices.read') ? (
              <Link
                to="/descobertas"
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-zinc-200 px-3 text-sm text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
              >
                <ScanSearch className="h-3.5 w-3.5" /> Descobertas
                {pendingCount ? (
                  <span className="rounded-full bg-amber-100 px-1.5 text-xs font-medium text-amber-800 dark:bg-amber-900 dark:text-amber-200">
                    {pendingCount}
                  </span>
                ) : null}
              </Link>
            ) : null}
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
                      `/api/v1/park/export?${qs({ ...toQuery(debounced, tab), sort: sort.key, direction: sort.dir, format: fmt })}`,
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
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Situação dos equipamentos">
        <TabChip
          active={tab === 'all'}
          label="Todos"
          count={c?.total}
          onClick={() => {
            setTab('all');
          }}
        />
        <TabChip
          active={tab === 'disconnected'}
          label="Sem conexão"
          count={c?.disconnected}
          title={c ? `Sem leitura há mais de ${String(c.disconnected_hours)} h` : undefined}
          onClick={() => {
            setTab('disconnected');
          }}
        />
        <TabChip
          active={tab === 'alert'}
          label="Com alerta"
          count={c?.alert}
          title="Toner abaixo de 10%, erro ou atenção"
          onClick={() => {
            setTab('alert');
          }}
        />
        <TabChip
          active={tab === 'inactive'}
          label="Desativados"
          count={c?.inactive}
          onClick={() => {
            setTab('inactive');
          }}
        />
      </div>

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
          {can('devices.update') ? (
            <>
              <BulkEdit ids={ids} onSubmit={bulk} />
              <BulkMove ids={ids} onSubmit={bulk} />
              {tab === 'inactive' ? (
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
              className="sticky top-0 z-10 hidden border-b border-zinc-200 bg-zinc-50 text-xs font-medium text-zinc-500 md:grid dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400"
              style={{ gridTemplateColumns: template }}
              role="row"
            >
              {selecting ? (
                <div className="flex items-center px-3 py-2.5">
                  <Checkbox
                    checked={allSelected ? true : selected.size ? 'indeterminate' : false}
                    onCheckedChange={(v) => {
                      setSelected(v ? new Set(rows.map((r) => r.id)) : new Set());
                    }}
                    label="Selecionar todos os carregados"
                  />
                </div>
              ) : null}
              {COLUMNS.map((col) => (
                <div
                  key={col.id}
                  role="columnheader"
                  className={cn(
                    'flex items-center px-3 py-2.5',
                    col.numeric && 'justify-end',
                    tab === 'disconnected' &&
                      col.id === 'last_read' &&
                      'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
                  )}
                >
                  {col.sort ? (
                    <button
                      type="button"
                      className={cn(
                        'flex items-center gap-1 hover:text-zinc-900 dark:hover:text-white',
                        col.numeric && 'flex-row-reverse',
                      )}
                      onClick={() => {
                        if (col.sort) toggleSort(col.sort);
                      }}
                    >
                      {col.label}
                      {sort.key === col.sort ? (
                        sort.dir === 'asc' ? (
                          <ArrowUp className="h-3 w-3" />
                        ) : (
                          <ArrowDown className="h-3 w-3" />
                        )
                      ) : null}
                    </button>
                  ) : (
                    <span>{col.label}</span>
                  )}
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
                        toggleRow(r.id, v);
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
                      tabIndex={0}
                      data-testid="park-row"
                      aria-label={`${printerName(r.brand, r.model)} ${r.serial}`}
                      onClick={() => {
                        openRow(r);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') openRow(r);
                      }}
                      className={cn(
                        'absolute left-0 grid w-full cursor-pointer items-center border-b border-zinc-100 outline-none transition-colors dark:border-zinc-800',
                        vi.index % 2 === 1 && 'bg-zinc-50/50 dark:bg-zinc-900/30',
                        'hover:bg-zinc-100/70 focus-visible:bg-zinc-100 dark:hover:bg-zinc-800/60',
                        selected.has(r.id) && 'bg-brand-50 dark:bg-slate-800',
                      )}
                      style={{ gridTemplateColumns: template, ...position }}
                    >
                      {pick ? (
                        <div
                          className="px-3"
                          onClick={(e) => {
                            e.stopPropagation();
                          }}
                        >
                          {pick}
                        </div>
                      ) : null}
                      {COLUMNS.map((col) => (
                        <div
                          key={col.id}
                          role="cell"
                          className={cn(
                            'flex h-full min-w-0 items-center px-3',
                            col.numeric && 'justify-end',
                            tab === 'disconnected' && col.id === 'last_read' && 'bg-red-50/60 dark:bg-red-950/40',
                          )}
                        >
                          {col.render(r, tab)}
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
    </div>
  );
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
          <CustomerPicker
            id="f-customer"
            value={draft.customer_id}
            onChange={(id) => {
              set({ customer_id: id, site_id: '' });
            }}
          />
        </Field>
        <Field label="Local" htmlFor="f-site">
          <SitePicker
            id="f-site"
            customerId={draft.customer_id}
            value={draft.site_id}
            onChange={(id) => {
              set({ site_id: id });
            }}
          />
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
            <CustomerPicker
              id="m-customer"
              value={customer}
              onChange={(id) => {
                setCustomer(id);
                setSite('');
              }}
            />
          </Field>
          <Field label="Local" htmlFor="m-site">
            <SitePicker
              id="m-site"
              customerId={customer}
              value={site}
              onChange={(id) => {
                setSite(id);
              }}
            />
          </Field>
        </div>
      </Dialog>
    </>
  );
}
