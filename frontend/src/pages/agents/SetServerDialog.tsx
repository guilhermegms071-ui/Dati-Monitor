import { useState } from 'react';

import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Input } from '../../components/ui/form';
import type { Schemas } from '../../lib/api';
import type { CommandType } from '../../lib/commands';
import { serverAddressError } from '../../lib/serverAddress';

type Agent = Schemas['AgentOut'];
type Send = (type: CommandType, params?: Record<string, unknown>) => Promise<unknown>;

/** Mudar endereço do servidor: migra o coletor (ex.: da rede local para a hospedagem) sem reinstalar. */
export function SetServerDialog({ agent, send, onClose }: { agent: Agent; send: Send; onClose: () => void }) {
  const [server, setServer] = useState('');
  const [ws, setWs] = useState('');
  const serverError = server ? serverAddressError(server) : null;
  const wsError = serverAddressError(ws, true);
  const ready = Boolean(server.trim()) && !serverError && !wsError;
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Mudar endereço do servidor"
      footer={
        <Button
          disabled={!ready}
          onClick={() => {
            const params: Record<string, unknown> = { server_url: server.trim() };
            if (ws.trim()) params.ws_url = ws.trim();
            void send('set_server', params).then(onClose);
          }}
        >
          Enviar ao coletor
        </Button>
      }
    >
      <div className="space-y-3 text-sm">
        <p>
          O coletor <strong>{agent.name}</strong> confere a assinatura do pedido e se autentica no novo servidor com a
          própria credencial antes de trocar. Se o novo servidor não reconhecer o coletor, ele continua no atual e o
          comando falha com o motivo.
        </p>
        <Field
          label="Novo endereço do servidor"
          htmlFor="ss-server"
          hint="https://monitor.exemplo.com.br (http:// só com IP de rede privada, para teste)"
          error={serverError}
        >
          <Input
            id="ss-server"
            autoComplete="off"
            value={server}
            onChange={(e) => {
              setServer(e.target.value);
            }}
          />
        </Field>
        <Field
          label="Canal WebSocket (opcional)"
          htmlFor="ss-ws"
          hint="Vazio = o novo servidor informa o canal dele"
          error={wsError}
        >
          <Input
            id="ss-ws"
            autoComplete="off"
            value={ws}
            onChange={(e) => {
              setWs(e.target.value);
            }}
          />
        </Field>
      </div>
    </Dialog>
  );
}
