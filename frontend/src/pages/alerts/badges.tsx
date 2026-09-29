import { Badge } from '../../components/ui/primitives';

const SEVERITY: Record<string, { label: string; tone: 'red' | 'yellow' | 'blue' }> = {
  critical: { label: 'Crítico', tone: 'red' },
  warning: { label: 'Atenção', tone: 'yellow' },
  info: { label: 'Aviso', tone: 'blue' },
};

export function SeverityBadge({ severity }: { severity: string }) {
  const s = SEVERITY[severity] ?? { label: severity, tone: 'blue' as const };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}
