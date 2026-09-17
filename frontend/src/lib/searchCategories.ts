export type DocumentCategory = "X" | "Y" | "Z";

export function documentCategory(value?: string | null): DocumentCategory | null {
  const categories: Record<string, DocumentCategory> = { A: "X", B: "Y", C: "Z", X: "X", Y: "Y", Z: "Z" };
  return categories[value ?? ""] ?? null;
}

export function categoryOrder(value?: string | null): number {
  const category = documentCategory(value);
  return category ? { X: 0, Y: 1, Z: 2 }[category] : 3;
}

export function categoryLabel(value?: string | null): string {
  const category = documentCategory(value);
  return category ? `${category}분류` : "미분류";
}
