import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Input } from '../../components/ui/form';
import { api, unwrap, type Schemas } from '../../lib/api';
import { showError, showSuccess } from '../../lib/notify';
import { formatCep, lookupCep } from '../../lib/viacep';

type Site = Schemas['SiteOut'];

const toText = (v: string | number | null | undefined) => (v === null || v === undefined ? '' : String(v));

/** Cadastro do Local: endereço completo pelo CEP (ViaCEP), coordenadas e ativação automática (16.1/16.9). */
export function SiteDialog({
  customerId,
  site,
  onClose,
}: {
  customerId: string;
  site: Site | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    name: site?.name ?? '',
    cep: formatCep(site?.cep),
    street: site?.street ?? '',
    number: site?.number ?? '',
    complement: site?.complement ?? '',
    district: site?.district ?? '',
    city: site?.city ?? '',
    state: site?.state ?? '',
    latitude: toText(site?.latitude),
    longitude: toText(site?.longitude),
    auto_activate_devices: site?.auto_activate_devices ?? false,
  });
  const [busy, setBusy] = useState(false);
  const [looking, setLooking] = useState(false);
  const set = (p: Partial<typeof form>) => {
    setForm((f) => ({ ...f, ...p }));
  };
  const nullable = (v: string) => (v.trim() ? v.trim() : null);

  const fillFromCep = () => {
    setLooking(true);
    lookupCep(form.cep)
      .then((a) => {
        set({ cep: formatCep(a.cep), street: a.street, district: a.district, city: a.city, state: a.state });
        showSuccess('Endereço preenchido pelo CEP');
      })
      .catch((err: unknown) => {
        showError(err, 'CEP');
      })
      .finally(() => {
        setLooking(false);
      });
  };

  const save = () => {
    setBusy(true);
    const body = {
      name: form.name.trim(),
      cep: nullable(form.cep),
      street: nullable(form.street),
      number: nullable(form.number),
      complement: nullable(form.complement),
      district: nullable(form.district),
      city: nullable(form.city),
      state: nullable(form.state),
      latitude: nullable(form.latitude.replace(',', '.')),
      longitude: nullable(form.longitude.replace(',', '.')),
      auto_activate_devices: form.auto_activate_devices,
    };
    const req = site
      ? unwrap(api.PATCH('/api/v1/sites/{site_id}', { params: { path: { site_id: site.id } }, body }))
      : unwrap(api.POST('/api/v1/sites', { body: { ...body, customer_id: customerId } }));
    req
      .then(() => {
        showSuccess('Local salvo');
        void qc.invalidateQueries({ queryKey: ['sites'] });
        onClose();
      })
      .catch((err: unknown) => {
        showError(err, 'Local não salvo');
      })
      .finally(() => {
        setBusy(false);
      });
  };

  const text = (key: keyof typeof form, label: string, id: string, className?: string) => (
    <Field label={label} htmlFor={id} className={className}>
      <Input
        id={id}
        value={form[key] as string}
        onChange={(e) => {
          set({ [key]: e.target.value });
        }}
      />
    </Field>
  );

  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={site ? 'Editar local' : 'Novo local'}
      footer={
        <Button loading={busy} disabled={!form.name.trim()} onClick={save}>
          Salvar
        </Button>
      }
    >
      <div className="grid gap-3 sm:grid-cols-6">
        {text('name', 'Nome', 'st-name', 'sm:col-span-6')}
        <Field label="CEP" htmlFor="st-cep" className="sm:col-span-3">
          <div className="flex gap-1">
            <Input
              id="st-cep"
              inputMode="numeric"
              value={form.cep}
              onChange={(e) => {
                set({ cep: e.target.value });
              }}
              onBlur={() => {
                if (form.cep.replace(/\D/g, '').length === 8 && !form.street) fillFromCep();
              }}
            />
            <Button type="button" size="sm" variant="secondary" loading={looking} onClick={fillFromCep}>
              Buscar
            </Button>
          </div>
        </Field>
        {text('street', 'Logradouro', 'st-street', 'sm:col-span-3')}
        {text('number', 'Número', 'st-number', 'sm:col-span-2')}
        {text('complement', 'Complemento', 'st-complement', 'sm:col-span-4')}
        {text('district', 'Bairro', 'st-district', 'sm:col-span-2')}
        {text('city', 'Cidade', 'st-city', 'sm:col-span-3')}
        {text('state', 'UF', 'st-state', 'sm:col-span-1')}
        {text('latitude', 'Latitude', 'st-lat', 'sm:col-span-3')}
        {text('longitude', 'Longitude', 'st-lng', 'sm:col-span-3')}
        <label className="flex items-start gap-2 text-sm sm:col-span-6">
          <input
            type="checkbox"
            className="mt-1"
            checked={form.auto_activate_devices}
            onChange={(e) => {
              set({ auto_activate_devices: e.target.checked });
            }}
          />
          <span>
            Ativar automaticamente os equipamentos descobertos
            <span className="block text-xs text-slate-500">
              Desligado: impressoras novas esperam em Equipamentos &gt; Descobertas até alguém ativar.
            </span>
          </span>
        </label>
      </div>
    </Dialog>
  );
}
