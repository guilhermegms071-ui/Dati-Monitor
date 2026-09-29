import { fmtInt } from '../lib/format';
import { Button } from './ui/button';

/** Rodapé da lista: quantos já estão na tela e o botão para buscar a próxima página. */
export function LoadMore({
  query,
  shown,
  total,
}: {
  query: { hasNextPage: boolean; isFetchingNextPage: boolean; fetchNextPage: () => unknown };
  shown: number;
  total?: number;
}) {
  if (!query.hasNextPage && total === undefined) return null;
  return (
    <div className="flex items-center justify-between gap-3 border-t border-slate-200 px-3 py-2 text-xs text-slate-500 dark:border-slate-800">
      <span>{total !== undefined ? `${fmtInt(shown)} de ${fmtInt(total)}` : `${fmtInt(shown)} carregados`}</span>
      {query.hasNextPage ? (
        <Button
          size="sm"
          variant="secondary"
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          Carregar mais
        </Button>
      ) : null}
    </div>
  );
}
