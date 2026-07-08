import { HistoryView } from "@/components/HistoryView";

export default function HistoryPageRoute() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-text-dark">Grievance history</h1>
        <p className="max-w-2xl text-sm text-text-secondary">
          Browse and search historical grievances. Filter by free-text,
          district, or category. Rows are served by the mock history provider
          until wire-up.
        </p>
      </div>
      <HistoryView />
    </div>
  );
}
