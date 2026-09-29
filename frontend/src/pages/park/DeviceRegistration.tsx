import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { Field, Input, Textarea } from '../../components/ui/form';
import { Card, CardHeader, Checkbox, EmptyState, ErrorState, KeyValue, Spinner } from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import { fmtBytes, fmtDateTime, fmtInt } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';

import { toThresholds } from '../../lib/toner';
import { ThresholdInputs } from '../customers/TonerThresholds';

import { useDeviceDetail } from './useDeviceDetail';

type Detail = Schemas['DeviceDetail'];
type CustomField = Schemas['CustomFieldOut'];

const text = (v: string | number | null | undefined) => (v === null || v === undefined ? '' : String(v));
const nullable = (v: string) => (v.trim() ? v.trim() : null);
const decimal = (v: string) => nullable(v.replace(',', '.'));
const integer = (v: string) => (v.trim() ? Number(v.replace(/\D/g, '')) : null);

/** Cadastro do equipamento (seção 16.7): patrimônio, serial alternativo, setor, franquia, excedente e campos
 * personalizados da revenda. O setor vazio volta a seguir o sysLocation da impressora. */
export function DeviceForm({ deviceId, editable }: { deviceId: string; editable: boolean }) {
  const q = useDeviceDetail(deviceId);
  const fields = useQuery<Schemas['CustomFieldOut'][]>({
    queryKey: ['custom-fields'],
    queryFn: () => unwrap(api.GET('/api/v1/custom-fields')),
  });
  if (q.isPending || fields.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (fields.isError) return <ErrorState error={fields.error} onRetry={() => void fields.refetch()} />;
  return (
    <DeviceFormInner
      key={q.dataUpdatedAt}
      device={q.data}
      customFields={fields.data.filter((f) => f.active)}
      editable={editable}
    />
  );
}

function DeviceFormInner({
  device,
  customFields,
  editable,
}: {
  device: Detail;
  customFields: CustomField[];
  editable: boolean;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    asset_tag: device.asset_tag ?? '',
    alt_serial: device.alt_serial ?? '',
    sector: device.sector_from_snmp ? '' : (device.sector ?? ''),
    notes: device.notes ?? '',
    franchise_value: text(device.franchise_value),
    franchise_pages_mono: text(device.franchise_pages_mono),
    franchise_pages_color: text(device.franchise_pages_color),
    overage_price_mono: text(device.overage_price_mono),
    overage_price_color: text(device.overage_price_color),
    monitored: device.monitored,
    active: device.active,
  });
  const [custom, setCustom] = useState<Record<string, string>>(() =>
    Object.fromEntries(customFields.map((f) => [f.key, text(device.custom_fields[f.key] as string | number | null)])),
  );
  const [busy, setBusy] = useState(false);
  const set = (p: Partial<typeof form>) => {
    setForm((f) => ({ ...f, ...p }));
  };
  const input = (
    key: keyof typeof form,
    label: string,
    id: string,
    extra: { hint?: string; placeholder?: string } = {},
  ) => (
    <Field label={label} htmlFor={id} hint={extra.hint}>
      <Input
        id={id}
        disabled={!editable}
        placeholder={extra.placeholder}
        value={form[key] as string}
        onChange={(e) => {
          set({ [key]: e.target.value });
        }}
      />
    </Field>
  );

  const save = () => {
    setBusy(true);
    const body = {
      asset_tag: form.asset_tag,
      alt_serial: form.alt_serial,
      sector: form.sector,
      notes: form.notes,
      franchise_value: decimal(form.franchise_value),
      franchise_pages_mono: integer(form.franchise_pages_mono),
      franchise_pages_color: integer(form.franchise_pages_color),
      overage_price_mono: decimal(form.overage_price_mono),
      overage_price_color: decimal(form.overage_price_color),
      monitored: form.monitored,
      active: form.active,
      ...(customFields.length ? { custom_fields: custom } : {}),
    };
    unwrap(api.PATCH('/api/v1/devices/{device_id}', { params: { path: { device_id: device.id } }, body }))
      .then(() => {
        void qc.invalidateQueries({ queryKey: ['device', device.id] });
        void qc.invalidateQueries({ queryKey: ['park'] });
        showSuccess('Equipamento atualizado');
      })
      .catch((err: unknown) => {
        showError(err, 'Não foi possível salvar');
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <Card className="p-4">
      <form
        className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        {input('asset_tag', 'Patrimônio (PAT)', 'd-pat')}
        {input('alt_serial', 'Serial alternativo', 'd-alt')}
        {input('sector', 'Setor', 'd-sector', {
          placeholder: device.sys_location ?? undefined,
          hint: device.sector_from_snmp
            ? 'Seguindo o local informado pela impressora (sysLocation)'
            : 'Vazio volta a seguir o local informado pela impressora',
        })}
        <div />
        {input('franchise_value', 'Valor da franquia (R$)', 'd-fvalue', { placeholder: '0,00' })}
        {input('franchise_pages_mono', 'Franquia PB (páginas)', 'd-fmono')}
        {input('franchise_pages_color', 'Franquia cor (páginas)', 'd-fcolor')}
        <div />
        {input('overage_price_mono', 'Excedente PB (R$/página)', 'd-omono', { placeholder: '0,0000' })}
        {input('overage_price_color', 'Excedente cor (R$/página)', 'd-ocolor', { placeholder: '0,0000' })}
        <div className="hidden lg:block lg:col-span-2" />
        {customFields.map((f) => (
          <Field key={f.key} label={f.label} htmlFor={`d-cf-${f.key}`}>
            <Input
              id={`d-cf-${f.key}`}
              type={f.field_type === 'date' ? 'date' : 'text'}
              inputMode={f.field_type === 'number' ? 'decimal' : undefined}
              disabled={!editable}
              value={custom[f.key] ?? ''}
              onChange={(e) => {
                setCustom((c) => ({ ...c, [f.key]: e.target.value }));
              }}
            />
          </Field>
        ))}
        <Field label="Observação" htmlFor="d-notes" className="sm:col-span-2 lg:col-span-4">
          <Textarea
            id="d-notes"
            disabled={!editable}
            value={form.notes}
            onChange={(e) => {
              set({ notes: e.target.value });
            }}
          />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={form.monitored}
            onCheckedChange={(v) => {
              if (editable) set({ monitored: v });
            }}
            label="Monitorar"
          />{' '}
          Monitorar (alertas e relatórios)
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={form.active}
            onCheckedChange={(v) => {
              if (editable) set({ active: v });
            }}
            label="Ativo"
          />{' '}
          Ativo no parque
        </label>
        {editable ? (
          <div className="sm:col-span-2 lg:col-span-4">
            <Button type="submit" loading={busy}>
              Salvar
            </Button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}

interface Attributes {
  firmware?: string[];
  memory_bytes?: number;
  storage?: { description: string; size_bytes: number; used_bytes: number }[];
  mac?: string;
  ssid?: string;
  uptime_seconds?: number;
  subsystems?: { name: string; description?: string; status: string }[];
  panel_text?: string;
  sys_location?: string;
  parts?: { name: string; part: string; color?: string; unit: string; value: number }[];
}

const SUBSYSTEM: Record<string, string> = {
  printer: 'Impressora',
  copier: 'Cópia',
  scanner: 'Scanner',
  processor: 'Processador',
  network: 'Rede',
  disk_storage: 'Disco',
};
const SUBSYSTEM_STATUS: Record<string, string> = {
  running: 'funcionando',
  warning: 'atenção',
  down: 'parado',
  testing: 'em teste',
  unknown: 'desconhecido',
};
const PART: Record<string, string> = {
  drum: 'Cilindro',
  fuser: 'Fusor',
  transfer: 'Unidade de transferência',
  maintenance_kit: 'Kit de manutenção',
  rollers: 'Roletes',
  waste_toner: 'Reservatório de toner usado',
  developer: 'Revelador',
  other: 'Outra peça',
};

function fmtUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return days ? `${String(days)} d ${String(hours)} h` : `${String(hours)} h`;
}

/** Atributos da leitura diária (seção 16.8). */
export function AttributesCard({ deviceId }: { deviceId: string }) {
  const q = useDeviceDetail(deviceId);
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const a = q.data.attributes as Attributes;
  if (!q.data.attributes_at) {
    return <EmptyState title="Atributos ainda não lidos">A leitura de atributos acontece uma vez por dia.</EmptyState>;
  }
  return (
    <Card>
      <CardHeader title="Atributos" subtitle={`Leitura de ${fmtDateTime(q.data.attributes_at)}`} />
      <div className="grid gap-4 p-4 lg:grid-cols-2">
        <KeyValue
          items={[
            ['Firmware', a.firmware?.length ? a.firmware.join(' · ') : '—'],
            ['Memória', a.memory_bytes ? fmtBytes(a.memory_bytes) : '—'],
            [
              'Disco',
              a.storage?.length
                ? a.storage
                    .map((d) => `${d.description}: ${fmtBytes(d.used_bytes)} de ${fmtBytes(d.size_bytes)}`)
                    .join(' · ')
                : '—',
            ],
            ['MAC', a.mac ?? '—'],
            ['Wi-Fi (SSID)', a.ssid ?? '—'],
            ['Ligada há', a.uptime_seconds !== undefined ? fmtUptime(a.uptime_seconds) : '—'],
            ['Local na impressora', a.sys_location ?? '—'],
            ['Painel', a.panel_text ?? '—'],
          ]}
        />
        <div className="space-y-3 text-sm">
          <div>
            <p className="mb-1 text-xs font-medium text-slate-500">Subsistemas</p>
            {a.subsystems?.length ? (
              <ul className="space-y-0.5">
                {a.subsystems.map((s, i) => (
                  <li key={`${s.name}-${String(i)}`}>
                    {SUBSYSTEM[s.name] ?? s.name}
                    {s.description ? <span className="text-slate-500"> ({s.description})</span> : null}:{' '}
                    {SUBSYSTEM_STATUS[s.status] ?? s.status}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-slate-500">—</p>
            )}
          </div>
          <div>
            <p className="mb-1 text-xs font-medium text-slate-500">Peças</p>
            {a.parts?.length ? (
              <ul className="space-y-0.5">
                {a.parts.map((p) => (
                  <li key={p.name}>
                    {PART[p.part] ?? p.part}
                    {p.color ? ` (${p.color})` : ''}:{' '}
                    {p.unit === 'percent'
                      ? `${String(p.value)}%`
                      : `${fmtInt(p.value)} ${p.unit === 'pages' ? 'páginas' : ''}`}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-slate-500">
                Nenhuma informada pelo perfil (níveis de cilindro, fusor e resíduo aparecem em Suprimentos)
              </p>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}

/** Limiar de toner do equipamento (seção 16.5): desligado, global (herda do cliente) ou individual. */
export function DeviceTonerCard({ deviceId, editable }: { deviceId: string; editable: boolean }) {
  const q = useDeviceDetail(deviceId);
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  return <DeviceTonerForm key={q.dataUpdatedAt} device={q.data} editable={editable} />;
}

function DeviceTonerForm({ device, editable }: { device: Detail; editable: boolean }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState(device.toner_mode);
  const [values, setValues] = useState(() => toThresholds(device.toner_thresholds));
  const [busy, setBusy] = useState(false);
  return (
    <Card>
      <CardHeader title="Limiar de toner" subtitle="Quando alertar toner baixo neste equipamento" />
      <div className="space-y-4 p-4">
        <div className="flex flex-wrap gap-4 text-sm">
          {(
            [
              ['off', 'Desligado'],
              ['global', 'Global (limiares do cliente)'],
              ['individual', 'Individual'],
            ] as const
          ).map(([v, label]) => (
            <label key={v} className="flex items-center gap-2">
              <input
                type="radio"
                name="toner-mode"
                disabled={!editable}
                checked={mode === v}
                onChange={() => {
                  setMode(v);
                }}
              />
              {label}
            </label>
          ))}
        </div>
        {mode === 'individual' ? (
          <ThresholdInputs value={values} onChange={setValues} disabled={!editable} idPrefix="dt" />
        ) : null}
        {editable ? (
          <Button
            loading={busy}
            onClick={() => {
              setBusy(true);
              unwrap(
                api.PATCH('/api/v1/devices/{device_id}', {
                  params: { path: { device_id: device.id } },
                  body: {
                    toner_mode: mode as 'off' | 'global' | 'individual',
                    ...(mode === 'individual' ? { toner_thresholds: values } : {}),
                  },
                }),
              )
                .then(() => {
                  showSuccess('Limiar de toner salvo');
                  void qc.invalidateQueries({ queryKey: ['device', device.id] });
                })
                .catch((err: unknown) => {
                  showError(err, 'Limiar não salvo');
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
