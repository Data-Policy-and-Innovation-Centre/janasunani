import { HistoryView } from "@/components/HistoryView";
import { PageHead } from "@/components/ui";

export default async function HistoryPageRoute({
  searchParams,
}: {
  searchParams: Promise<{ q?: string | string[] }>;
}) {
  const q = (await searchParams).q;
  const initialQuery = Array.isArray(q) ? (q[0] ?? "") : (q ?? "");

  return (
    <div className="pb-16">
      <PageHead
        kicker="Archive"
        title={
          <>
            Grievance <em>history</em>
          </>
        }
        lead="Browse and search historical grievances. Filter by free text, district, or category."
      />
      <HistoryView initialQuery={initialQuery} />
    </div>
  );
}
