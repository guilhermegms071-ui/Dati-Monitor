import { useQueryClient } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

import { LoadMore } from '../../components/paging';
import { Button } from '../../components/ui/button';
import { ConfirmButton } from '../../components/ui/dialog';
import { Card, EmptyState, ErrorState, Spinner } from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtDateTime } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';

type Transfer = Schemas['TransferOut'];

/** Equipamentos que apareceram num local de outro cliente: aprovar passa o equipamento (e as leituras desde
 * que apareceu lá) para o novo cliente; manter deixa no cliente atual e ignora aquele local. */
export function TransfersPanel() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const { query, rows, total } = useCursorList<Transfer>(['transfers'], (cursor) =>
    unwrap(api.GET('/api/v1/transfers', { params: { query: { limit: PAGE_SIZE, cursor } } })),
  );
  const decide = (t: Transfer, action: 'approve' | 'reject') => {
    setBusy(t.device_id);
    return unwrap(
      api.POST('/api/v1/devices/{device_id}/transfer', {
        params: { path: { device_id: t.device_id } },
        body: { action },
      }),
    )
      .then(() => {
        showSuccess(
          action === 'approve'
            ? `${t.serial} agora é do cliente ${t.to_customer}`
            : `${t.serial} continua no cliente ${t.from_customer}`,
        );
        void qc.invalidateQueries({ queryKey: ['transfers'] });
        void qc.invalidateQueries({ queryKey: ['park'] });
      })
      .catch((err: unknown) => {
        showError(err, 'Não foi possível concluir a transferência');
      })
      .finally(() => {
        setBusy(null);
      });
  };
  const canDecide = can('devices.update');
  return (
    <Card className="overflow-x-auto">
      <p className="border-b border-slate-100 px-4 py-3 text-sm text-slate-600 dark:border-slate-800 dark:text-slate-300">
        Impressoras que apareceram num local de <strong>outro cliente</strong>. Até você decidir, elas continuam no
        cliente atual. Ao aprovar, as leituras desde que ela apareceu lá passam a contar para o novo cliente; os
        relatórios do cliente antigo continuam com o período em que ela esteve com ele.
      </p>
      {query.isPending ? (
        <Spinner />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : !rows.length ? (
        <EmptyState title="Nenhuma transferência aguardando" />
      ) : (
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
            <tr>
              <th className="px-3 py-2 text-left">Apareceu em</th>
              <th className="px-3 py-2 text-left">Nº de série</th>
              <th className="px-3 py-2 text-left">Modelo</th>
              <th className="px-3 py-2 text-left">De</th>
              <th className="px-3 py-2" />
              <th className="px-3 py-2 text-left">Para</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.device_id} className="border-t border-slate-100 dark:border-slate-800">
                <td className="whitespace-nowrap px-3 py-2 text-xs">{fmtDateTime(t.detected_at)}</td>
                <td className="px-3 py-2 font-mono text-xs">
                  <Link to={`/parque/${t.device_id}`} className="text-brand-600 hover:underline dark:text-brand-100">
                    {t.serial}
                  </Link>
                </td>
                <td className="px-3 py-2 text-xs">{t.model ?? '—'}</td>
                <td className="px-3 py-2 text-xs">
                  <span className="font-medium">{t.from_customer}</span>
                  <span className="block text-slate-500">{t.from_site}</span>
                </td>
                <td className="px-1 py-2 text-slate-400">
                  <ArrowRight className="h-4 w-4" aria-hidden />
                </td>
                <td className="px-3 py-2 text-xs">
                  <span className="font-medium">{t.to_customer}</span>
                  <span className="block text-slate-500">{t.to_site}</span>
                </td>
                <td className="px-3 py-2 text-right">
                  {canDecide ? (
                    <span className="flex justify-end gap-1">
                      <ConfirmButton
                        title="Aprovar transferência"
                        description={`${t.serial} passa para ${t.to_customer} (${t.to_site}) desde ${fmtDateTime(t.detected_at)}.`}
                        confirmLabel="Aprovar"
                        onConfirm={() => decide(t, 'approve')}
                      >
                        Aprovar
                      </ConfirmButton>
                      <Button
                        size="sm"
                        variant="secondary"
                        loading={busy === t.device_id}
                        onClick={() => void decide(t, 'reject')}
                      >
                        Manter em {t.from_customer}
                      </Button>
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <LoadMore query={query} shown={rows.length} total={total} />
    </Card>
  );
}
