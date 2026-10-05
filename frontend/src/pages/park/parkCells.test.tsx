import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import type { Schemas } from '../../lib/api';

import { LastCommunication, Num, StatusPill, TonerBars } from './parkCells';

type Row = Schemas['ParkRow'];

function wrap(ui: React.ReactNode) {
  return render(
    <MemoryRouter>
      <TooltipPrimitive.Provider>{ui}</TooltipPrimitive.Provider>
    </MemoryRouter>,
  );
}

describe('células do Parque', () => {
  it('números em Geist Mono com milhar pt-BR; ausente vira "—" cinza, não 0', () => {
    wrap(
      <>
        <Num value={217031} />
        <Num value={null} />
      </>,
    );
    expect(screen.getByText('217.031')).toHaveClass('font-mono', 'tabular-nums');
    expect(screen.getByText('—')).toHaveClass('text-zinc-400');
  });

  it('barras de toner: vermelha abaixo de 10%, porcentagens só no tooltip', () => {
    wrap(
      <TonerBars
        supplies={[
          { color: 'black', percent: 89, level_state: 'ok', description: null },
          { color: 'magenta', percent: 8, level_state: 'ok', description: null },
        ]}
      />,
    );
    const bars = screen.getByTestId('toner-bars');
    expect(bars).toHaveAccessibleName('Toner: K 89% · M 8%');
    expect(bars.querySelectorAll('[data-low="true"]')).toHaveLength(1);
    expect(screen.queryByText('89%')).not.toBeInTheDocument();
  });

  it('selo de status com texto curto', () => {
    wrap(<StatusPill row={{ active: true, disconnected: false, last_status: 'warning' }} />);
    expect(screen.getByText('Atenção')).toHaveAttribute('data-tone', 'orange');
  });

  it('última comunicação: vermelha sem conexão e aviso discreto de comunicação instável', () => {
    const base = {
      last_read_at: new Date(Date.now() - 4 * 60_000).toISOString(),
      disconnected: false,
      comm_unstable: false,
    } as Row;
    const { rerender } = wrap(<LastCommunication row={base} />);
    expect(screen.getByText('há 4 min')).not.toHaveClass('text-red-600');
    expect(screen.queryByTestId('comm-unstable')).not.toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <TooltipPrimitive.Provider>
          <LastCommunication row={{ ...base, disconnected: true, comm_unstable: true }} />
        </TooltipPrimitive.Provider>
      </MemoryRouter>,
    );
    expect(screen.getByText('há 4 min')).toHaveClass('text-red-600');
    expect(screen.getByTestId('comm-unstable')).toHaveAccessibleName(
      'comunicação instável — verificar cabo/porta/duplex',
    );
  });
});
