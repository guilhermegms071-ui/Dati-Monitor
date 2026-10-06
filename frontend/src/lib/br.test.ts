import { isValidCnpj, isValidEmail, maskCnpj, maskPhone } from './br';

describe('br', () => {
  it('máscara e conferência de CNPJ', () => {
    expect(maskCnpj('11222333000181')).toBe('11.222.333/0001-81');
    expect(maskCnpj('112223')).toBe('11.222.3');
    expect(maskCnpj('124884003000012222')).toBe('12.488.400/3000-01');
    expect(isValidCnpj('11.222.333/0001-81')).toBe(true);
    expect(isValidCnpj('11.222.333/0001-80')).toBe(false);
    expect(isValidCnpj('11111111111111')).toBe(false);
    expect(isValidCnpj('1122233300018')).toBe(false);
  });

  it('máscara de telefone fixo e celular', () => {
    expect(maskPhone('2133330001')).toBe('(21) 3333-0001');
    expect(maskPhone('21964088334')).toBe('(21) 96408-8334');
    expect(maskPhone('21')).toBe('(21');
    expect(maskPhone('')).toBe('');
  });

  it('e-mail', () => {
    expect(isValidEmail('gui@live.com')).toBe(true);
    expect(isValidEmail('gui@live')).toBe(false);
  });
});
