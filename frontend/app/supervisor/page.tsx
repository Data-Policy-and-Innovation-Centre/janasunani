import { SupervisorSections } from "@/components/SupervisorSections";
import { PageHead } from "@/components/ui";

export default function SupervisorPage() {
  return (
    <div className="pb-16">
      <PageHead
        title={
          <>
            Monitoring <em>dashboard</em>
          </>
        }
        lead="Open cases by age. Disposed cases by stage."
      />
      <SupervisorSections />
    </div>
  );
}
