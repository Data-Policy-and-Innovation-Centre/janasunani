import { SupervisorSections } from "@/components/SupervisorSections";
import { PageHead } from "@/components/ui";

export default function SupervisorPage() {
  return (
    <div className="pb-16">
      <PageHead
        kicker="Oversight"
        title={
          <>
            Supervisor <em>monitoring</em>
          </>
        }
        lead="Queue health, routing, the end-to-end journey, ATR discipline, demand, and closure outcomes — read from validated aggregate artifacts. The earlier intelligence briefing is kept as a separate section."
      />
      <SupervisorSections />
    </div>
  );
}
