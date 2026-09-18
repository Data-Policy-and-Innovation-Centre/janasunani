import { SubmitForm } from "@/components/SubmitForm";
import { PageHead } from "@/components/ui";

export default function SubmitPage() {
  return (
    <div className="pb-16">
      <PageHead
        kicker="Intake"
        title={
          <>
            Submit a <em>grievance</em>
          </>
        }
        lead="Enter grievance text or upload a document. The demo extracts the text, redacts personally identifying information, classifies the grievance, summarises it, and proposes a routing."
      />
      <SubmitForm />
    </div>
  );
}
