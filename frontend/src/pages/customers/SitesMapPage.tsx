import 'leaflet/dist/leaflet.css';

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet';
import { Link } from 'react-router';

import { CustomerPicker } from '../../components/pickers';
import { Field } from '../../components/ui/form';
import { Badge, Card, CardHeader, EmptyState, ErrorState, PageHeader, Spinner } from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';

type SiteItem = Schemas['SiteMapItem'];

const STATUS: Record<SiteItem['status'], { label: string; color: string; tone: 'green' | 'yellow' | 'red' | 'gray' }> =
  {
    ok: { label: 'OK', color: '#16a34a', tone: 'green' },
    warning: { label: 'Atenção', color: '#d97706', tone: 'yellow' },
    offline: { label: 'Coletor offline', color: '#dc2626', tone: 'red' },
    no_agent: { label: 'Sem coletor', color: '#64748b', tone: 'gray' },
  };
const BRAZIL: [number, number] = [-15.8, -47.9];

function detail(s: SiteItem): string {
  return `${String(s.devices)} equip. (${String(s.devices_offline)} sem conexão) · coletores ${String(s.agents_online)}/${String(s.agents)} · ${String(s.alerts_open)} alerta(s)`;
}

export function SitesMapPage() {
  const [customer, setCustomer] = useState('');
  const q = useQuery({
    queryKey: ['sites-map', customer],
    queryFn: () =>
      unwrap(api.GET('/api/v1/sites/map', { params: { query: customer ? { customer_id: customer } : {} } })),
  });
  const located = (q.data ?? []).filter((s) => s.latitude !== null && s.longitude !== null);
  const missing = (q.data ?? []).filter((s) => s.latitude === null || s.longitude === null);
  const center: [number, number] = located.length
    ? [
        located.reduce((a, s) => a + Number(s.latitude), 0) / located.length,
        located.reduce((a, s) => a + Number(s.longitude), 0) / located.length,
      ]
    : BRAZIL;
  return (
    <div className="space-y-4">
      <PageHeader title="Mapa dos locais" subtitle="Situação de cada local pelos coletores, equipamentos e alertas." />
      <Card className="p-3">
        <Field label="Cliente" htmlFor="map-customer" className="max-w-sm">
          <CustomerPicker id="map-customer" value={customer} onChange={setCustomer} />
        </Field>
        <div className="mt-3 flex flex-wrap gap-3 text-xs">
          {Object.values(STATUS).map((s) => (
            <span key={s.label} className="flex items-center gap-1">
              <span className="inline-block h-3 w-3 rounded-full" style={{ background: s.color }} aria-hidden />
              {s.label}
            </span>
          ))}
        </div>
      </Card>
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <>
          <Card className="overflow-hidden">
            <div className="h-[32rem]" data-testid="sites-map">
              <MapContainer
                key={`${String(center[0])},${String(center[1])}`}
                center={center}
                zoom={located.length ? 11 : 4}
                className="h-full w-full"
              >
                <TileLayer
                  attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                  url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                />
                {located.map((s) => (
                  <CircleMarker
                    key={s.id}
                    center={[Number(s.latitude), Number(s.longitude)]}
                    radius={9}
                    pathOptions={{ color: STATUS[s.status].color, fillColor: STATUS[s.status].color, fillOpacity: 0.8 }}
                  >
                    <Popup>
                      <strong>{s.customer_name}</strong> — {s.name}
                      <br />
                      {STATUS[s.status].label}: {detail(s)}
                      <br />
                      <Link to={`/clientes/${s.customer_id}`}>Abrir cliente</Link>
                    </Popup>
                  </CircleMarker>
                ))}
              </MapContainer>
            </div>
          </Card>
          <Card>
            <CardHeader
              title="Locais sem coordenadas"
              subtitle="Informe latitude/longitude no cadastro do local (o CEP preenche o endereço)."
            />
            {missing.length === 0 ? (
              <EmptyState title="Todos os locais estão no mapa" />
            ) : (
              <ul className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
                {missing.map((s) => (
                  <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                    <Link className="text-brand-600 hover:underline" to={`/clientes/${s.customer_id}`}>
                      {s.customer_name} — {s.name}
                    </Link>
                    <span className="text-xs text-slate-500">{detail(s)}</span>
                    <Badge tone={STATUS[s.status].tone}>{STATUS[s.status].label}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
