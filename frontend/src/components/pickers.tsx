import { useQuery } from '@tanstack/react-query';
import { Search, X } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';

import { api, unwrap } from '../lib/api';
import { cn } from '../lib/utils';
import { Input } from './ui/form';

export interface PickerOption {
  id: string;
  label: string;
  hint?: string | null;
}

const SEARCH_LIMIT = 20;

function useDebounced<T>(value: T, ms = 250): T {
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

/**
 * Seletor com busca no servidor: mostra até 20 resultados do que foi digitado, em vez de baixar a lista
 * inteira para o navegador (seção 0, regra 13). `value` é o id; o rótulo do selecionado vem de `resolve`.
 */
export function RemotePicker({
  id,
  value,
  onChange,
  search,
  resolve,
  queryKey,
  placeholder = 'Digite para buscar…',
  disabled,
  allowClear = true,
}: {
  id?: string;
  value: string;
  onChange: (id: string) => void;
  search: (q: string) => Promise<PickerOption[]>;
  resolve: (id: string) => Promise<PickerOption>;
  queryKey: string;
  placeholder?: string;
  disabled?: boolean;
  allowClear?: boolean;
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [active, setActive] = useState(0);
  const q = useDebounced(text);
  const box = useRef<HTMLDivElement>(null);
  const selected = useQuery({
    queryKey: [queryKey, 'picker-label', value],
    queryFn: () => resolve(value),
    enabled: Boolean(value),
    staleTime: 60_000,
  });
  const options = useQuery({
    queryKey: [queryKey, 'picker-search', q],
    queryFn: () => search(q),
    enabled: open,
    staleTime: 30_000,
  });
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => {
      document.removeEventListener('mousedown', onDoc);
    };
  }, []);
  const items = options.data ?? [];
  const pick = (o: PickerOption) => {
    onChange(o.id);
    setText('');
    setOpen(false);
  };
  const shown = open ? text : (selected.data?.label ?? (value ? '…' : ''));
  return (
    <div ref={box} className="relative">
      <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
      <Input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        className="pl-8 pr-8"
        placeholder={value && !open ? undefined : placeholder}
        value={shown}
        disabled={disabled}
        onFocus={() => {
          setOpen(true);
          setActive(0);
        }}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, items.length - 1));
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === 'Enter' && open && items[active]) {
            e.preventDefault();
            pick(items[active]);
          } else if (e.key === 'Escape') {
            setOpen(false);
          }
        }}
      />
      {value && allowClear && !disabled ? (
        <button
          type="button"
          aria-label="Limpar seleção"
          className="absolute right-2 top-2 rounded p-0.5 text-slate-400 hover:text-slate-700"
          onClick={() => {
            onChange('');
          }}
        >
          <X className="h-4 w-4" />
        </button>
      ) : null}
      {open ? (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-64 w-full overflow-auto rounded-md border border-slate-200 bg-white py-1 text-sm shadow-lg dark:border-slate-700 dark:bg-slate-900"
        >
          {options.isPending ? (
            <li className="px-3 py-2 text-slate-500">Buscando…</li>
          ) : options.isError ? (
            <li className="px-3 py-2 text-red-600">Falha na busca: {options.error.message}</li>
          ) : !items.length ? (
            <li className="px-3 py-2 text-slate-500">Nada encontrado</li>
          ) : (
            items.map((o, i) => (
              <li
                key={o.id}
                role="option"
                aria-selected={o.id === value}
                className={cn(
                  'cursor-pointer px-3 py-1.5',
                  i === active ? 'bg-brand-50 dark:bg-slate-800' : '',
                  o.id === value ? 'font-medium' : '',
                )}
                onMouseEnter={() => {
                  setActive(i);
                }}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(o);
                }}
              >
                {o.label}
                {o.hint ? <span className="ml-1 text-xs text-slate-500">· {o.hint}</span> : null}
              </li>
            ))
          )}
          {items.length === SEARCH_LIMIT ? (
            <li className="px-3 py-1 text-xs text-slate-500">Mostrando os {SEARCH_LIMIT} primeiros; refine a busca.</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

export function CustomerPicker(props: {
  id?: string;
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <RemotePicker
      {...props}
      queryKey="customers"
      placeholder="Buscar cliente…"
      search={async (q) =>
        (
          await unwrap(api.GET('/api/v1/customers', { params: { query: { q: q || null, limit: SEARCH_LIMIT } } }))
        ).items.map((c) => ({ id: c.id, label: c.name, hint: c.erp_code }))
      }
      resolve={async (id) => {
        const c = await unwrap(api.GET('/api/v1/customers/{customer_id}', { params: { path: { customer_id: id } } }));
        return { id: c.id, label: c.name };
      }}
    />
  );
}

export function SitePicker({
  customerId,
  ...props
}: {
  id?: string;
  customerId: string;
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <RemotePicker
      {...props}
      disabled={props.disabled ?? !customerId}
      queryKey={`sites-${customerId}`}
      placeholder={customerId ? 'Buscar local…' : 'Escolha o cliente primeiro'}
      search={async (q) =>
        (
          await unwrap(
            api.GET('/api/v1/sites', {
              params: { query: { customer_id: customerId || null, q: q || null, limit: SEARCH_LIMIT } },
            }),
          )
        ).items.map((s) => ({ id: s.id, label: s.name, hint: s.city }))
      }
      resolve={async (id) => {
        const s = await unwrap(api.GET('/api/v1/sites/{site_id}', { params: { path: { site_id: id } } }));
        return { id: s.id, label: s.name };
      }}
    />
  );
}

export function CompanyPicker(props: {
  id?: string;
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <RemotePicker
      {...props}
      queryKey="companies"
      placeholder="Buscar empresa…"
      allowClear={false}
      search={async (q) =>
        (
          await unwrap(api.GET('/api/v1/companies', { params: { query: { q: q || null, limit: SEARCH_LIMIT } } }))
        ).items.map((c) => ({ id: c.id, label: c.legal_name, hint: c.cnpj }))
      }
      resolve={async (id) => {
        const c = await unwrap(api.GET('/api/v1/companies/{company_id}', { params: { path: { company_id: id } } }));
        return { id: c.id, label: c.legal_name };
      }}
    />
  );
}
