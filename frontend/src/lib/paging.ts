import { useInfiniteQuery, type InfiniteData, type QueryKey } from '@tanstack/react-query';

/** Página devolvida pela API: toda lista é paginada no servidor por cursor (nada de lista inteira). */
export interface CursorPage<T> {
  items: T[];
  next_cursor?: string | null;
  total?: number;
}

export const PAGE_SIZE = 100;

/**
 * Lista paginada no servidor: busca a primeira página e as seguintes sob demanda ("Carregar mais").
 * Não há limite fixo de registros: o usuário continua carregando até o fim (seção 0, regra 13).
 */
export function useCursorList<T>(queryKey: QueryKey, fetchPage: (cursor: string | null) => Promise<CursorPage<T>>) {
  const query = useInfiniteQuery<
    CursorPage<T>,
    Error,
    InfiniteData<CursorPage<T>, string | null>,
    QueryKey,
    string | null
  >({
    queryKey,
    initialPageParam: null,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    getNextPageParam: (last) => last.next_cursor ?? null,
  });
  const pages: CursorPage<T>[] = query.data?.pages ?? [];
  const rows: T[] = [];
  for (const p of pages) rows.push(...p.items);
  const total = pages[0]?.total;
  return { query, rows, total };
}
