/** "HP" + "HP LaserJet M15w" vira "HP LaserJet M15w" (o driver do Windows já põe a marca no modelo). */
export function printerName(brand?: string | null, model?: string | null): string {
  if (brand && model?.toLowerCase().startsWith(brand.toLowerCase())) return model;
  return [brand, model].filter(Boolean).join(' ');
}
