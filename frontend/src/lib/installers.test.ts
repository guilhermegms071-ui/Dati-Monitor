import { guessInstaller } from './installers';

describe('guessInstaller', () => {
  it('reconhece os nomes gerados pelos scripts de build', () => {
    expect(guessInstaller('dati-monitor-setup-1.2.0.exe')).toEqual({ kind: 'windows', arch: 'all', version: '1.2.0' });
    expect(guessInstaller('dati-monitor-agent_1.2.0~rc.1_armhf.deb')).toEqual({
      kind: 'deb',
      arch: 'arm',
      version: '1.2.0-rc.1',
    });
    expect(guessInstaller('dati-monitor-agent_1.2.0_i386.deb').arch).toBe('386');
    expect(guessInstaller('dati-monitor-agent-1.2.0-linux-arm64.tar.gz')).toEqual({
      kind: 'tar',
      arch: 'arm64',
      version: '1.2.0',
    });
    expect(guessInstaller('dati-monitor-agent-1.2.0-linux-arm.tar.gz').arch).toBe('arm');
    expect(guessInstaller('dati-monitor-agent_1.2.0_amd64.deb').arch).toBe('amd64');
  });
});
