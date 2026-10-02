import { describe, expect, it } from 'vitest';

import { printerName } from './printers';

describe('printerName', () => {
  it('não repete a marca que o driver já pôs no modelo', () => {
    expect(printerName('HP', 'HP LaserJet M15w')).toBe('HP LaserJet M15w');
    expect(printerName('Brother', 'brother HL-1212W')).toBe('brother HL-1212W');
  });
  it('junta marca e modelo quando o modelo não traz a marca', () => {
    expect(printerName('Canon', 'iR 1643i')).toBe('Canon iR 1643i');
    expect(printerName(null, 'M404')).toBe('M404');
    expect(printerName('HP', null)).toBe('HP');
    expect(printerName(null, null)).toBe('');
  });
});
