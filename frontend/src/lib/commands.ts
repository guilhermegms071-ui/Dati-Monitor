import { useQueryClient } from '@tanstack/react-query';
import { createElement, useState } from 'react';

import { CommandWatch } from '../components/domain';
import { api, unwrap, type Schemas } from './api';
import { showError } from './notify';

type Command = Schemas['CommandOut'];

export type CommandType = Schemas['CommandIn']['type'];

/** Envia um comando ao coletor e abre o acompanhamento ao vivo. */
export function useSendCommand(agentId: string) {
  const qc = useQueryClient();
  const [watching, setWatching] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function send(type: CommandType, params: Record<string, unknown> = {}): Promise<Command | null> {
    setBusy(true);
    try {
      const cmd = await unwrap(
        api.POST('/api/v1/agents/{agent_id}/commands', {
          params: { path: { agent_id: agentId } },
          body: { type, params },
        }),
      );
      void qc.invalidateQueries({ queryKey: ['commands', agentId] });
      setWatching(cmd.id);
      return cmd;
    } catch (err) {
      showError(err, 'Comando não enviado');
      return null;
    } finally {
      setBusy(false);
    }
  }
  const watcher = watching
    ? createElement(CommandWatch, {
        commandId: watching,
        onClose: () => {
          setWatching(null);
        },
      })
    : null;
  return { send, busy, watcher, watch: setWatching };
}
