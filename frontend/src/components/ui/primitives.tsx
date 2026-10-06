import * as CheckboxPrimitive from '@radix-ui/react-checkbox';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { AlertTriangle, Check, Inbox, Loader2, Minus } from 'lucide-react';
import type { HTMLAttributes, ReactNode } from 'react';
import { Link } from 'react-router';

import { fmtDateTime, fmtRelative } from '../../lib/format';
import { cn } from '../../lib/utils';

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'rounded-lg border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(16,24,40,0.04)] dark:border-slate-800 dark:bg-slate-900',
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({
  title,
  actions,
  subtitle,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-3.5 dark:border-slate-800">
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold text-slate-900 dark:text-slate-100">{title}</h2>
        {subtitle ? <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

const tones = {
  gray: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  green: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
  yellow: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  red: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300',
  blue: 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300',
  purple: 'bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300',
} as const;
export type Tone = keyof typeof tones;

export function Badge({
  tone = 'gray',
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Spinner({ label = 'Carregando…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 p-8 text-sm text-slate-500" role="status">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      {label}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-10 text-center text-sm text-slate-500">
      <Inbox className="h-8 w-8 text-slate-300" aria-hidden />
      <p className="font-medium text-slate-700 dark:text-slate-300">{title}</p>
      {children}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex flex-col items-center gap-2 p-8 text-center text-sm" role="alert">
      <AlertTriangle className="h-6 w-6 text-red-500" aria-hidden />
      <p className="font-medium text-red-700 dark:text-red-400">Não foi possível carregar</p>
      <p className="text-slate-600 dark:text-slate-400">{message}</p>
      {onRetry ? (
        <button type="button" className="text-brand-600 underline" onClick={onRetry}>
          Tentar de novo
        </button>
      ) : null}
    </div>
  );
}

export interface RelatedLink {
  to: string;
  label: string;
}

/** Título da tela; `related` = atalhos para as telas de apoio que não estão no menu principal. */
export function PageHeader({
  title,
  subtitle,
  actions,
  related,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  related?: RelatedLink[];
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">{title}</h1>
        {subtitle ? <div className="mt-1 text-sm text-slate-500 dark:text-slate-400">{subtitle}</div> : null}
        {related?.length ? (
          <nav aria-label="Ver também" className="mt-2.5 flex flex-wrap items-center gap-2 text-xs">
            <span className="text-slate-400">Ver também:</span>
            {related.map((r) => (
              <Link
                key={r.to}
                to={r.to}
                className="rounded-full border border-slate-200 bg-white px-2.5 py-0.5 font-medium text-slate-700 hover:border-brand-300 hover:text-brand-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:text-brand-200"
              >
                {r.label}
              </Link>
            ))}
          </nav>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <TooltipPrimitive.Root delayDuration={300}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          sideOffset={4}
          className="z-50 rounded bg-slate-900 px-2 py-1 text-xs text-white shadow dark:bg-slate-100 dark:text-slate-900"
        >
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

/** "há 5 min", com a data completa no tooltip (padrão de UX da seção 10). */
export function RelativeTime({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-slate-400">nunca</span>;
  return (
    <Tooltip content={fmtDateTime(value)}>
      <time dateTime={value} className="cursor-default">
        {fmtRelative(value)}
      </time>
    </Tooltip>
  );
}

export function Checkbox({
  checked,
  onCheckedChange,
  label,
}: {
  checked: boolean | 'indeterminate';
  onCheckedChange: (v: boolean) => void;
  label?: string;
}) {
  return (
    <CheckboxPrimitive.Root
      checked={checked}
      onCheckedChange={(v) => {
        onCheckedChange(v === true);
      }}
      aria-label={label}
      className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-slate-400 bg-white data-[state=checked]:border-brand-600 data-[state=checked]:bg-brand-600 data-[state=indeterminate]:bg-brand-600 dark:border-slate-600 dark:bg-slate-900"
    >
      <CheckboxPrimitive.Indicator className="text-white">
        {checked === 'indeterminate' ? <Minus className="h-3 w-3" /> : <Check className="h-3 w-3" />}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export const Tabs = TabsPrimitive.Root;

export function TabsList({ children }: { children: ReactNode }) {
  return (
    <TabsPrimitive.List className="mb-5 flex flex-wrap gap-1 border-b border-slate-200 dark:border-slate-800">
      {children}
    </TabsPrimitive.List>
  );
}

export function TabsTrigger({ value, children }: { value: string; children: ReactNode }) {
  return (
    <TabsPrimitive.Trigger
      value={value}
      className="-mb-px border-b-2 border-transparent px-3.5 py-2.5 text-sm font-medium text-slate-500 transition-colors hover:text-slate-900 data-[state=active]:border-brand-600 data-[state=active]:text-brand-700 dark:text-slate-400 dark:hover:text-slate-100 dark:data-[state=active]:text-brand-200"
    >
      {children}
    </TabsPrimitive.Trigger>
  );
}

export const TabsContent = TabsPrimitive.Content;

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-8 text-sm sm:grid-cols-2">
      {items.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-3 border-b border-slate-100 py-2 dark:border-slate-800">
          <dt className="text-slate-500">{k}</dt>
          <dd className="text-right font-medium text-slate-900 dark:text-slate-100">{v ?? '—'}</dd>
        </div>
      ))}
    </dl>
  );
}
