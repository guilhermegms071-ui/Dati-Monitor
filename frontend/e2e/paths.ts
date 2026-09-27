/** Caminhos do repositório usados pela configuração e pelo global-setup do Playwright. */
export const repoRoot = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
export const python =
  process.platform === 'win32' ? `${repoRoot}.venv/Scripts/python.exe` : `${repoRoot}.venv/bin/python`;
