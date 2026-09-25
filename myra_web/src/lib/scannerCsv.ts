export type ScannerCsvParams = Record<string, unknown>;

export interface ScannerCsvMetadata {
  scanner: string;
  scanned_date?: string | null;
  params: ScannerCsvParams;
  exported_at: string;
}

function sanitizeMetadataText(value: string): string {
  return value.replace(/[\r\n]+/g, ' ');
}

function formatMetadataValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }

  if (typeof value === 'string') {
    return sanitizeMetadataText(value);
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }

  return sanitizeMetadataText(JSON.stringify(value) ?? String(value));
}

export function formatScannerCsv(
  csv: string,
  metadata: ScannerCsvMetadata,
): string {
  const metadataLines = [
    `# scanner: ${sanitizeMetadataText(metadata.scanner)}`,
    `# scanned_date: ${sanitizeMetadataText(metadata.scanned_date ?? '')}`,
  ];

  for (const [key, value] of Object.entries(metadata.params)) {
    metadataLines.push(`# ${sanitizeMetadataText(key)}: ${formatMetadataValue(value)}`);
  }

  metadataLines.push(`# exported_at: ${sanitizeMetadataText(metadata.exported_at)}`);

  return `${metadataLines.join('\n')}\n\n${csv}`;
}
