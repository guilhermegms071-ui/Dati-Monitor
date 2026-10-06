import { useQueryClient } from '@tanstack/react-query';
import { ClipboardPen } from 'lucide-react';
import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Input, Textarea } from '../../components/ui/form';
import { api, unwrap } from '../../lib/api';
import { fmtInt } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';

/** Leitura manual (folha de contadores) — impressoras USB sem PJL e qualquer equipamento sem contador. */
export function ManualReadingButton({
  deviceId,
  label = 'Leitura manual',
  size = 'sm',
}: {
  deviceId: string;
  label?: string;
  size?: 'sm' | 'md';
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [total, setTotal] = useState('');
  const [mono, setMono] = useState('');
  const [color, setColor] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const num = (v: string) => (v === '' ? null : Number(v));
  const digits = (set: (v: string) => void) => (e: { target: { value: string } }) => {
    set(e.target.value.replace(/\D/g, ''));
  };
  return (
    <>
      <Button
        size={size}
        variant="secondary"
        onClick={() => {
          setOpen(true);
        }}
      >
        <ClipboardPen className="h-4 w-4" /> {label}
      </Button>
      {open ? (
        <Dialog
          open
          onOpenChange={(o) => {
            if (!o) setOpen(false);
          }}
          title="Leitura manual"
          description="Digite os contadores da folha impressa pelo painel. Contador menor que a última leitura é recusado."
          footer={
            <Button
              loading={busy}
              disabled={total === '' && (mono === '' || color === '')}
              onClick={() => {
                setBusy(true);
                unwrap(
                  api.POST('/api/v1/devices/{device_id}/manual-readings', {
                    params: { path: { device_id: deviceId } },
                    body: { total: num(total), mono: num(mono), color: num(color), note: note || null },
                  }),
                )
                  .then((r) => {
                    showSuccess(`Leitura registrada: total ${fmtInt(r.total)}`);
                    void qc.invalidateQueries({ queryKey: ['device', deviceId] });
                    void qc.invalidateQueries({ queryKey: ['computers'] });
                    setOpen(false);
                  })
                  .catch((err: unknown) => {
                    showError(err, 'Leitura não registrada');
                  })
                  .finally(() => {
                    setBusy(false);
                  });
              }}
            >
              Registrar
            </Button>
          }
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Total" htmlFor="m-total" hint="Vazio = PB + cor">
              <Input id="m-total" inputMode="numeric" value={total} onChange={digits(setTotal)} />
            </Field>
            <Field label="PB" htmlFor="m-mono">
              <Input id="m-mono" inputMode="numeric" value={mono} onChange={digits(setMono)} />
            </Field>
            <Field label="Cor" htmlFor="m-color">
              <Input id="m-color" inputMode="numeric" value={color} onChange={digits(setColor)} />
            </Field>
          </div>
          <Field label="Observação" htmlFor="m-note" className="mt-3">
            <Textarea
              id="m-note"
              value={note}
              onChange={(e) => {
                setNote(e.target.value);
              }}
            />
          </Field>
        </Dialog>
      ) : null}
    </>
  );
}
