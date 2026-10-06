import * as AlertDialog from '@radix-ui/react-alert-dialog';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import * as Dropdown from '@radix-ui/react-dropdown-menu';
import { X } from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { showError } from '../../lib/notify';
import { cn } from '../../lib/utils';
import { Button } from './button';

const overlay = 'fixed inset-0 z-40 bg-slate-950/40 backdrop-blur-[1px]';
const panel =
  'fixed left-1/2 top-1/2 z-50 flex max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900';

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className={overlay} />
        <DialogPrimitive.Content className={cn(panel, wide ? 'max-w-4xl' : 'max-w-lg')}>
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-100 px-6 pb-4 pt-5 dark:border-slate-800">
            <div>
              <DialogPrimitive.Title className="text-lg font-semibold text-slate-900 dark:text-white">
                {title}
              </DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="mt-0.5 text-sm text-slate-500">
                  {description}
                </DialogPrimitive.Description>
              ) : (
                <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close
              className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
              aria-label="Fechar"
            >
              <X className="h-4 w-4" />
            </DialogPrimitive.Close>
          </div>
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
          {footer ? (
            <div className="flex shrink-0 justify-end gap-2 rounded-b-xl border-t border-slate-100 bg-slate-50 px-6 py-3.5 dark:border-slate-800 dark:bg-slate-900/60">
              {footer}
            </div>
          ) : null}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

/** Confirmação obrigatória para ações destrutivas (padrão de UX da seção 10). */
export function ConfirmButton({
  title,
  description,
  confirmLabel = 'Confirmar',
  danger,
  onConfirm,
  children,
  disabled,
  size = 'sm',
  variant = 'secondary',
}: {
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => Promise<unknown>;
  children: ReactNode;
  disabled?: boolean;
  size?: 'sm' | 'md';
  variant?: 'secondary' | 'danger' | 'primary' | 'ghost';
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <AlertDialog.Root open={open} onOpenChange={setOpen}>
      <AlertDialog.Trigger asChild>
        <Button size={size} variant={variant} disabled={disabled}>
          {children}
        </Button>
      </AlertDialog.Trigger>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className={overlay} />
        <AlertDialog.Content className={cn(panel, 'max-w-md')}>
          <div className="px-6 pb-2 pt-5">
            <AlertDialog.Title className="text-lg font-semibold text-slate-900 dark:text-white">
              {title}
            </AlertDialog.Title>
            <AlertDialog.Description className="mt-2 text-sm text-slate-600 dark:text-slate-400">
              {description}
            </AlertDialog.Description>
          </div>
          <div className="mt-4 flex justify-end gap-2 rounded-b-xl border-t border-slate-100 bg-slate-50 px-6 py-3.5 dark:border-slate-800 dark:bg-slate-900/60">
            <AlertDialog.Cancel asChild>
              <Button variant="secondary">Cancelar</Button>
            </AlertDialog.Cancel>
            <Button
              variant={danger ? 'danger' : 'primary'}
              loading={busy}
              onClick={() => {
                setBusy(true);
                onConfirm()
                  .then(() => {
                    setOpen(false);
                  })
                  .catch((err: unknown) => {
                    showError(err, title);
                  })
                  .finally(() => {
                    setBusy(false);
                  });
              }}
            >
              {confirmLabel}
            </Button>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

export function Menu({ trigger, children }: { trigger: ReactNode; children: ReactNode }) {
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>{trigger}</Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content
          align="end"
          sideOffset={4}
          className="z-50 min-w-52 rounded-lg border border-slate-200 bg-white p-1 shadow-xl dark:border-slate-800 dark:bg-slate-900"
        >
          {children}
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}

export function MenuItem({
  onSelect,
  children,
  danger,
  disabled,
}: {
  onSelect: () => void;
  children: ReactNode;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <Dropdown.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        'flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-2 text-sm text-slate-700 outline-none data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50 data-[highlighted]:bg-slate-100 data-[highlighted]:text-slate-900 dark:text-slate-200 dark:data-[highlighted]:bg-slate-800',
        danger && 'text-red-600',
      )}
    >
      {children}
    </Dropdown.Item>
  );
}

export function MenuSeparator() {
  return <Dropdown.Separator className="my-1 h-px bg-slate-200 dark:bg-slate-800" />;
}
