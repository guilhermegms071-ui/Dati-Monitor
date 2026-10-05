/**
 * Modelo sem a marca que o fabricante já repete no início ("KONICA MINOLTA bizhub C287" com a marca
 * "Konica Minolta" vira "bizhub C287"). Se o modelo for só a marca, fica como veio.
 */
export function modelWithoutBrand(brand?: string | null, model?: string | null): string | null {
  if (!model) return null;
  const b = brand?.trim().toLowerCase();
  if (!b || !model.toLowerCase().startsWith(b)) return model;
  const after = model.slice(b.length);
  // "HPE Officejet" não começa pela marca "HP": só corta quando a marca é uma palavra inteira.
  if (/^[a-z0-9]/i.test(after)) return model;
  const rest = after.replace(/^[\s\-_:/]+/, '');
  return rest || model;
}

/** Nome para exibir: marca + modelo, sem repetir a marca ("Konica Minolta bizhub C287"). */
export function printerName(brand?: string | null, model?: string | null): string {
  return [brand, modelWithoutBrand(brand, model)].filter(Boolean).join(' ');
}
