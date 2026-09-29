import { useQuery } from '@tanstack/react-query';

import { api, unwrap } from '../../lib/api';

/** Cadastro completo e atributos do equipamento (GET /devices/{id}). */
export function useDeviceDetail(deviceId: string) {
  return useQuery({
    queryKey: ['device', deviceId, 'detail'],
    queryFn: () => unwrap(api.GET('/api/v1/devices/{device_id}', { params: { path: { device_id: deviceId } } })),
  });
}
