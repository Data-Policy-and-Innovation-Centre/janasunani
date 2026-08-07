import { HistoryView } from "@/components/HistoryView";

export default async function HistoryPageRoute({
  searchParams,
}: {
  searchParams: Promise<{ q?: string | string[] }>;
}) {
  const q = (await searchParams).q;
  const initialQuery = Array.isArray(q) ? (q[0] ?? "") : (q ?? "");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-text-dark">Grievance history</h1>
        <p className="max-w-2xl text-sm text-text-secondary">
          Browse and search historical grievances. Filter by free-text,
          district, or category.
        </p>
      </div>
      <HistoryView initialQuery={initialQuery} />
    </div>
  );
}
