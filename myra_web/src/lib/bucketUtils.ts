export function resolveBucket(indices: string[], in_nifty500: number): string {
    if (indices.some(i => i.includes('NIFTY 50') && !i.includes('NEXT'))) {
        return "Large Cap (N50)";
    } else if (indices.some(i => i.includes('NIFTY NEXT 50'))) {
        return "Large Cap (N100)";
    } else if (indices.some(i => i.includes('NIFTY SMALLCAP 250') || i.includes('SMALL CAP 250'))) {
        return "Nifty Small Cap 250";
    } else if (in_nifty500 === 1 || indices.some(i => i.includes('NIFTY 500'))) {
        return "Broader Market (N500)";
    }
    return "Deep Frontier";
}
