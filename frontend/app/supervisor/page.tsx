import { SupervisorSections } from "@/components/SupervisorSections";

export default function SupervisorPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-text-dark">
          Supervisor monitoring
        </h1>
        <p className="max-w-3xl text-sm leading-relaxed text-text-secondary">
          Track queue health, routing, the end-to-end journey, ATR discipline,
          demand, and closure outcomes. The earlier intelligence briefing is
          retained as a separate section.
        </p>
      </div>
      <SupervisorSections />
    </div>
  );
}
