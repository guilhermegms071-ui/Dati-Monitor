import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { Field, Input } from '../../components/ui/form';
import { Card, CardHeader } from '../../components/ui/primitives';
import { api, unwrap } from '../../lib/api';
import { showError, showSuccess } from '../../lib/notify';
import { TONER_COLORS, toThresholds, type Thresholds } from '../../lib/toner';

export function ThresholdInputs({
  value,
  onChange,
  disabled,
  idPrefix,
}: {
  value: Thresholds;
  onChange: (v: Thresholds) => void;
  disabled: boolean;
  idPrefix: string;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {TONER_COLORS.map(([c, label]) => (
        <Field key={c} label={`${label} (%)`} htmlFor={`${idPrefix}-${c}`}>
          <Input
            id={`${idPrefix}-${c}`}
            inputMode="numeric"
            disabled={disabled}
            value={String(value[c])}
            onChange={(e) => {
              onChange({ ...value, [c]: Math.min(100, Number(e.target.value.replace(/\D/g, '') || 0)) });
            }}
          />
        </Field>
      ))}
    </div>
  );
}

/** Cliente > Suprimentos (seção 16.5): liga/desliga o monitoramento e o limiar de toner por cor. */
export function CustomerTonerCard({
  customerId,
  monitoring,
  thresholds,
  editable,
}: {
  customerId: string;
  monitoring: boolean;
  thresholds: Record<string, unknown>;
  editable: boolean;
}) {
  const qc = useQueryClient();
  const [on, setOn] = useState(monitoring);
  const [values, setValues] = useState(() => toThresholds(thresholds));
  const [busy, setBusy] = useState(false);
  return (
    <Card>
      <CardHeader
        title="Monitoramento de suprimentos"
        subtitle="Limiar de alerta por cor. Equipamentos no modo Global usam estes valores."
      />
      <div className="space-y-4 p-4">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            disabled={!editable}
            checked={on}
            onChange={(e) => {
              setOn(e.target.checked);
            }}
          />
          Monitorar suprimentos deste cliente
        </label>
        <ThresholdInputs value={values} onChange={setValues} disabled={!editable || !on} idPrefix="ct" />
        {editable ? (
          <Button
            loading={busy}
            onClick={() => {
              setBusy(true);
              unwrap(
                api.PATCH('/api/v1/customers/{customer_id}', {
                  params: { path: { customer_id: customerId } },
                  body: { toner_monitoring: on, toner_thresholds: values },
                }),
              )
                .then(() => {
                  showSuccess('Limiares salvos');
                  void qc.invalidateQueries({ queryKey: ['customer', customerId] });
                })
                .catch((err: unknown) => {
                  showError(err, 'Limiares não salvos');
                })
                .finally(() => {
                  setBusy(false);
                });
            }}
          >
            Salvar
          </Button>
        ) : null}
      </div>
    </Card>
  );
}
