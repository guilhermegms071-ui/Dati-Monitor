import { ApiError } from './api';
import { errorMessage } from './utils';
import { fieldErrors, translateMessage } from './validation';

const err = new ApiError(422, 'validation_error', 'Dados inválidos', {
  errors: [
    { loc: ['body', 'company_id'], msg: 'Input should be a valid UUID, invalid length', type: 'uuid_parsing' },
    { loc: ['body', 'cnpj'], msg: 'Value error, CNPJ inválido', type: 'value_error' },
  ],
});

describe('validação', () => {
  it('traduz e associa ao campo', () => {
    expect(fieldErrors(err)).toEqual({ company_id: 'escolha uma opção da lista', cnpj: 'CNPJ inválido' });
    expect(translateMessage('Field required')).toBe('obrigatório');
    expect(translateMessage('String should have at least 2 characters')).toBe('mínimo de 2 caracteres');
  });

  it('a mensagem do toast diz qual campo está errado', () => {
    expect(errorMessage(err)).toBe('Dados inválidos: Empresa: escolha uma opção da lista; CNPJ inválido');
    expect(errorMessage(new Error('falhou'))).toBe('falhou');
  });
});
