import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { render, screen } from '@testing-library/react';

import { SupplyBars } from './domain';

describe('SupplyBars', () => {
  it('mostra C/M/Y/K com %, n/d quando a impressora não informa e destaca nível crítico', () => {
    render(
      <TooltipPrimitive.Provider>
        <SupplyBars
          supplies={[
            { color: 'cyan', percent: 55, level_state: 'ok', description: 'Toner ciano' },
            { color: 'magenta', percent: null, level_state: 'unknown', description: null },
            { color: 'black', percent: 7.4, level_state: 'low', description: 'Toner preto' },
          ]}
        />
      </TooltipPrimitive.Provider>,
    );
    expect(screen.getByText('55%')).toHaveClass('text-slate-500');
    expect(screen.getByText('n/d')).toBeInTheDocument();
    expect(screen.getByText('7%')).toHaveClass('font-bold', 'text-red-600');
  });

  it('sem suprimentos mostra um traço', () => {
    render(<SupplyBars supplies={[]} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
