import { Globe } from 'lucide-react';
import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Select } from '../../components/ui/form';
import { api, unwrap } from '../../lib/api';
import { showError, showSuccess } from '../../lib/notify';

const OPTIONS: { label: string; port: number; scheme: 'http' | 'https' }[] = [
  { label: 'http (80)', port: 80, scheme: 'http' },
  { label: 'https (443)', port: 443, scheme: 'https' },
  { label: 'http (8080)', port: 8080, scheme: 'http' },
  { label: 'http (8000)', port: 8000, scheme: 'http' },
  { label: 'https (8443)', port: 8443, scheme: 'https' },
];

/** Abrir a página web da impressora pelo túnel do coletor (seção 4.9), em nova aba. */
export function WebAccessButton({ deviceId, size = 'sm' }: { deviceId: string; size?: 'sm' | 'md' }) {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState(0);
  const [busy, setBusy] = useState(false);
  const go = () => {
    const opt = OPTIONS[choice] ?? { port: 80, scheme: 'http' as const };
    // A aba abre já no clique (senão o navegador bloqueia o popup); o endereço chega depois.
    const tab = window.open('about:blank', '_blank');
    setBusy(true);
    unwrap(
      api.POST('/api/v1/devices/{device_id}/web-session', {
        params: { path: { device_id: deviceId } },
        body: { port: opt.port, scheme: opt.scheme },
      }),
    )
      .then((s) => {
        if (tab) tab.location.href = s.url;
        else window.location.assign(s.url);
        showSuccess(`Página aberta pelo coletor ${s.agent_name}; a sessão expira em 30 min`);
        setOpen(false);
      })
      .catch((err: unknown) => {
        tab?.close();
        showError(err, 'Página web não aberta');
      })
      .finally(() => {
        setBusy(false);
      });
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
        <Globe className="h-3.5 w-3.5" /> Abrir página web
      </Button>
      {open ? (
        <Dialog
          open
          onOpenChange={(o) => {
            if (!o) setOpen(false);
          }}
          title="Abrir a página web da impressora"
          description="O acesso passa pelo coletor do local, sem VPN. Fica registrado na auditoria e vale só para este navegador."
          footer={
            <Button loading={busy} onClick={go}>
              Abrir em nova aba
            </Button>
          }
        >
          <Field label="Porta" htmlFor="web-port">
            <Select
              id="web-port"
              value={choice}
              onChange={(e) => {
                setChoice(Number(e.target.value));
              }}
            >
              {OPTIONS.map((o, i) => (
                <option key={o.label} value={i}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
        </Dialog>
      ) : null}
    </>
  );
}
