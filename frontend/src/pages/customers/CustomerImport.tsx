import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { CompanyPicker } from '../../components/pickers';
import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Input } from '../../components/ui/form';
import { downloadFile, postBinary, type Schemas } from '../../lib/api';
import { fmtInt } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';

type Result = Schemas['CustomerImportResult'];

/** Importação de clientes por CSV (16.9): primeiro valida, depois importa — tudo ou nada. */
export function CustomerImportDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [company, setCompany] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);

  const run = (dryRun: boolean) => {
    if (!file || !company) return;
    setBusy(true);
    const qs = new URLSearchParams({ company_id: company, dry_run: String(dryRun) });
    postBinary<Result>(`/api/v1/customers/import?${qs.toString()}`, file)
      .then((r) => {
        setResult(r);
        if (r.imported) {
          showSuccess(`${fmtInt(r.customers)} cliente(s) e ${fmtInt(r.sites)} local(is) importados`);
          void qc.invalidateQueries({ queryKey: ['customers'] });
        }
      })
      .catch((err: unknown) => {
        showError(err, 'Importação falhou');
      })
      .finally(() => {
        setBusy(false);
      });
  };
  const blocking = result?.errors.filter((e) => e.message !== 'coluna desconhecida (ignorada)') ?? [];
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Importar clientes (CSV)"
      description="Separador ';' e a primeira linha com os nomes das colunas. Com qualquer erro, nada é gravado."
      footer={
        <>
          <Button
            variant="secondary"
            loading={busy}
            disabled={!file || !company}
            onClick={() => {
              run(true);
            }}
          >
            Validar
          </Button>
          <Button
            loading={busy}
            disabled={!file || !company || !result || !result.dry_run || blocking.length > 0}
            onClick={() => {
              run(false);
            }}
          >
            Importar
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            downloadFile('/api/v1/customers/import/template', 'modelo-importacao-clientes.csv').catch(
              (err: unknown) => {
                showError(err, 'Modelo não baixado');
              },
            );
          }}
        >
          Baixar modelo do CSV
        </Button>
        <Field label="Empresa" htmlFor="imp-company">
          <CompanyPicker id="imp-company" value={company} onChange={setCompany} />
        </Field>
        <Field label="Arquivo" htmlFor="imp-file">
          <Input
            id="imp-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setResult(null);
            }}
          />
        </Field>
        {result ? (
          <div
            className="rounded border border-slate-200 p-3 text-sm dark:border-slate-700"
            data-testid="import-result"
          >
            <p>
              {fmtInt(result.lines)} linha(s): {fmtInt(result.customers)} cliente(s), {fmtInt(result.sites)} local(is).
              {result.imported
                ? ' Importado.'
                : blocking.length
                  ? ' Corrija os erros e valide de novo.'
                  : ' Pronto para importar.'}
            </p>
            {result.errors.length ? (
              <ul className="mt-2 max-h-56 space-y-1 overflow-auto text-xs">
                {result.errors.map((e, i) => (
                  <li key={i} className={e.message.includes('ignorada') ? 'text-slate-500' : 'text-red-600'}>
                    Linha {e.line}, {e.column}: {e.message}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
