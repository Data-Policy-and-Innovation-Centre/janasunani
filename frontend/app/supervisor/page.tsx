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
        lead="What is open now and how long it has waited, and how long disposed cases took and at which stage the time went."
      />
      <SupervisorSections />
    </div>
  );
}
