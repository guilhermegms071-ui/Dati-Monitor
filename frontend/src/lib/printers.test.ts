import { describe, expect, it } from 'vitest';

import { modelWithoutBrand, printerName } from './printers';

describe('modelWithoutBrand', () => {
  it('tira a marca que o fabricante repete no início do modelo', () => {
    expect(modelWithoutBrand('Konica Minolta', 'KONICA MINOLTA bizhub C287')).toBe('bizhub C287');
    expect(modelWithoutBrand('Konica Minolta', 'KONICA MINOLTA AccurioPrint C4065')).toBe('AccurioPrint C4065');
    expect(modelWithoutBrand('HP', 'HP LaserJet M15w')).toBe('LaserJet M15w');
    expect(modelWithoutBrand('Brother', 'brother HL-1212W')).toBe('HL-1212W');
    expect(modelWithoutBrand('Canon', 'Canon-iR 1643i')).toBe('iR 1643i');
  });
  it('não mexe quando o modelo não começa pela marca', () => {
    expect(modelWithoutBrand('Kyocera', 'ECOSYS M3655idn')).toBe('ECOSYS M3655idn');
    expect(modelWithoutBrand('Canon', 'iR-ADV C5540')).toBe('iR-ADV C5540');
  });
  it('não corta uma palavra que só começa igual à marca', () => {
    expect(modelWithoutBrand('HP', 'HPE Officejet')).toBe('HPE Officejet');
  });
  it('modelo que é só a marca, ou sem marca/modelo', () => {
    expect(modelWithoutBrand('HP', 'HP')).toBe('HP');
    expect(modelWithoutBrand(null, 'M404')).toBe('M404');
    expect(modelWithoutBrand('HP', null)).toBeNull();
  });
});

describe('printerName', () => {
  it('marca + modelo, sem repetir a marca', () => {
    expect(printerName('Konica Minolta', 'KONICA MINOLTA bizhub C454e')).toBe('Konica Minolta bizhub C454e');
    expect(printerName('HP', 'HP LaserJet M15w')).toBe('HP LaserJet M15w');
    expect(printerName('Brother', 'brother HL-1212W')).toBe('Brother HL-1212W');
    expect(printerName('Canon', 'iR 1643i')).toBe('Canon iR 1643i');
  });
  it('sem marca ou sem modelo', () => {
    expect(printerName(null, 'M404')).toBe('M404');
    expect(printerName('HP', null)).toBe('HP');
    expect(printerName(null, null)).toBe('');
  });
});
